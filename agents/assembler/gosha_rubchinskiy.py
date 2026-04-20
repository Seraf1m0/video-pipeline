#!/usr/bin/env python3
"""
gosha_rubchinskiy.py — чистый монтажёр видео для Video Pipeline.

Пайплайн:
  1. INIT     — канал, сессия, пути, стиль
  2. CLIPS    — подбор клипов из библиотеки (clip_selector + embeddings)
  3. TIMELINE — тайминг по result.json, trim клипов (stream copy), план переходов
  4. RENDER   — videotrack + audio + subtitles → final.mp4
  5. COMMIT   — фиксируем историю клипов, чистим temp

Запуск:
  py agents/assembler/gosha_rubchinskiy.py --channel de
  py agents/assembler/gosha_rubchinskiy.py --channel fr --session Video_20260328_120000
  py agents/assembler/gosha_rubchinskiy.py --channel es --no-subs --no-music
"""

import argparse
import concurrent.futures
import io
import json
import os
import random
import shutil
import subprocess
import sys
import time
from datetime import datetime
from pathlib import Path

# ── UTF-8 stdout ──────────────────────────────────────────────────────────────
try:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    elif hasattr(sys.stdout, "buffer"):
        sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
except Exception:
    pass

# ── Пути ─────────────────────────────────────────────────────────────────────
BASE_DIR   = Path(__file__).resolve().parent.parent.parent
DATA_DIR   = BASE_DIR / "data"
MUSIC_DIR  = DATA_DIR / "music"

# ── Импорты из проекта ────────────────────────────────────────────────────────
for _p in [
    BASE_DIR / "agents" / "utils",
    BASE_DIR / "agents" / "library",
    BASE_DIR / "agents" / "assembler",
    BASE_DIR / "agents" / "transcriber",
    BASE_DIR / "agents" / "motion_graphics",
    BASE_DIR / "bot",
]:
    if str(_p) not in sys.path:
        sys.path.insert(0, str(_p))

from paths import (
    get_channel_dir, get_session_dir, get_last_session,
    get_input_dir, get_transcripts_dir, get_output_dir,
    get_intro_clips_dir, get_intro_path, get_audio_path,
    get_result_json, get_ass_path, get_final_video,
    get_clips_dir, ensure_session_dirs, TEMP_ROOT,
)
from style_engine import get_channel_style, get_intro_transition_fn
from transitions import concat_all_with_transitions, _final_concat, intro_to_main_transition, set_temp_dir
from audio_mixer import build_final_audio
try:
    from gpu_compositor import run as _gpu_composite
    _GPU_COMPOSITOR_AVAILABLE = True
except ImportError:
    _GPU_COMPOSITOR_AVAILABLE = False
from ass_generator import generate_ass, generate_karaoke_ass, generate_scripture_ass
from subtitle_burner import burn_ass, generate_drawtext_filter

try:
    from clip_selector import select_clips_for_video, commit_clip_history
    _SELECTOR_OK = True
except ImportError as _e:
    _SELECTOR_OK = False
    print(f"[WARN] clip_selector недоступен: {_e}", flush=True)

from pipeline_validator import audit_final_audio

from dotenv import load_dotenv
load_dotenv(BASE_DIR / "config" / ".env")

# ── Константы ─────────────────────────────────────────────────────────────────
OUTPUT_RESOLUTION = "1920x1080"
OUTPUT_BITRATE    = "25M"
OUTPUT_MAXRATE    = "30M"
OUTPUT_BUFSIZE    = "60M"
NVENC_PRESET      = "p4"

ORGANETTO_FONT_PATH = os.environ.get(
    "ORGANETTO_FONT_PATH",
    r"C:\Users\Serafim\AppData\Local\Microsoft\Windows\Fonts\Organetto.ttf",
).strip()

SUBTITLE_FADE_IN_MS  = 100
SUBTITLE_FADE_OUT_MS = 100
SUBTITLE_RISE_PX     = 15
SUBTITLE_FONT_SIZE   = 28

# Канальные алиасы
_CH_ALIAS = {
    "de":  "channel_001_cosmos_de",
    "fr":  "channel_002_cosmos_fr",
    "es":  "channel_003_religion_es",
}

# ─────────────────────────────────────────────────────────────────────────────
# УТИЛИТЫ
# ─────────────────────────────────────────────────────────────────────────────

class _Tee:
    """Дублирует stdout в файл (для автоматического лога в _logs/)."""
    def __init__(self, stream, filepath: Path):
        filepath.parent.mkdir(parents=True, exist_ok=True)
        self._file   = open(filepath, "w", encoding="utf-8", errors="replace")
        self._stream = stream
    def write(self, data):
        self._stream.write(data)
        self._file.write(data)
    def flush(self):
        self._stream.flush()
        self._file.flush()
    def close(self):
        self._file.close()
    def __getattr__(self, attr):
        return getattr(self._stream, attr)

_tee: _Tee | None = None

def log(msg: str) -> None:
    print(f"[GOSHA] {msg}", flush=True)

_DURATION_CACHE: dict[str, float] = {}

def get_duration(path: Path) -> float:
    """ffprobe длительность медиафайла (результат кешируется)."""
    key = str(path.resolve())
    if key in _DURATION_CACHE:
        return _DURATION_CACHE[key]
    r = subprocess.run(
        ["ffprobe", "-v", "quiet", "-print_format", "json", "-show_format", str(path)],
        capture_output=True, text=True,
    )
    if r.returncode != 0:
        raise RuntimeError(f"ffprobe failed: {path.name}")
    val = float(json.loads(r.stdout)["format"]["duration"])
    _DURATION_CACHE[key] = val
    return val


def ffprobe_check(path: Path, expected: float, label: str, tolerance: float = 3.0) -> tuple[bool, float]:
    """
    ffprobe проверка длительности файла.
    Возвращает (ok, actual_dur).
    ok=False если файл отсутствует, нечитаем или короче expected - tolerance.
    """
    if not path.exists():
        log(f"  [ffprobe] {label}: файл не найден: {path.name}")
        return False, 0.0
    try:
        # Не используем кеш — файл мог только что записаться
        r = subprocess.run(
            ["ffprobe", "-v", "quiet", "-print_format", "json", "-show_format", str(path)],
            capture_output=True, text=True,
        )
        if r.returncode != 0:
            log(f"  [ffprobe] {label}: ffprobe error → {path.name}")
            return False, 0.0
        actual = float(json.loads(r.stdout)["format"]["duration"])
        diff = actual - expected
        sign = "+" if diff >= 0 else ""
        ok = actual >= expected - tolerance
        status = "✅" if ok else "⚠️ TRUNCATED"
        log(f"  [ffprobe] {label}: {actual:.1f}s (ожидалось {expected:.1f}s, Δ={sign}{diff:.1f}s) {status}")
        return ok, actual
    except Exception as e:
        log(f"  [ffprobe] {label}: ошибка {e}")
        return False, 0.0


def make_metadata_flags() -> list[str]:
    """
    Реалистичные метаданные Adobe Premiere / After Effects.
    Media Encoder — только рендерер, не создаёт.
    """
    COMBOS = [
        {"software": "Adobe Premiere Pro",     "encoder": None},
        {"software": "Adobe Premiere Pro CC",  "encoder": None},
        {"software": "Adobe Premiere Pro 2024","encoder": None},
        {"software": "Adobe Premiere Pro",     "encoder": "Adobe Media Encoder CC"},
        {"software": "Adobe Premiere Pro",     "encoder": "Adobe Media Encoder"},
        {"software": "Adobe After Effects",    "encoder": None},
        {"software": "Adobe After Effects CC", "encoder": None},
        {"software": "Adobe After Effects",    "encoder": "Adobe Media Encoder CC"},
        {"software": "Adobe After Effects",    "encoder": "Adobe Media Encoder"},
    ]
    combo = random.choice(COMBOS)

    base  = datetime(2024, 6, 1, 9, 0, 0)
    from datetime import timedelta
    delta = timedelta(
        days=random.randint(0, 660),
        hours=random.randint(0, 12),
        minutes=random.randint(0, 59),
        seconds=random.randint(0, 59),
    )
    ts = (base + delta).strftime("%Y-%m-%dT%H:%M:%S.000000Z")

    flags: list[str] = []
    if random.random() >= 0.08:  # 8% шанс не писать timestamp
        flags += ["-metadata", f"creation_time={ts}"]
    flags += ["-metadata", f"software={combo['software']}"]
    if combo["encoder"]:
        flags += ["-metadata", f"encoder={combo['encoder']}"]
    return flags


def _video_encode_args(use_nvenc: bool = True) -> list[str]:
    if use_nvenc:
        return [
            "-c:v", "h264_nvenc", "-preset", NVENC_PRESET,
            "-b:v", OUTPUT_BITRATE, "-maxrate", OUTPUT_MAXRATE,
            "-bufsize", OUTPUT_BUFSIZE, "-pix_fmt", "yuv420p",
        ]
    return [
        "-c:v", "libx264", "-preset", "fast",
        "-b:v", OUTPUT_BITRATE, "-maxrate", OUTPUT_MAXRATE,
        "-bufsize", OUTPUT_BUFSIZE, "-pix_fmt", "yuv420p",
    ]


def _run_ffmpeg(cmd: list[str], desc: str = "") -> bool:
    """Запустить ffmpeg, вернуть True при успехе."""
    r = subprocess.run(cmd, capture_output=True)
    if r.returncode != 0 and desc:
        log(f"  [ffmpeg ERR] {desc}: {r.stderr.decode(errors='replace')[-300:]}")
    return r.returncode == 0


# ─────────────────────────────────────────────────────────────────────────────
# STEP 1: INIT
# ─────────────────────────────────────────────────────────────────────────────

def resolve_channel(args) -> tuple[str, dict]:
    """Вернуть (channel_id, style)."""
    channel_id = _CH_ALIAS.get(args.channel, args.channel) if args.channel else "channel_001_cosmos_de"
    style = get_channel_style(channel_id)
    return channel_id, style


def resolve_session(channel_id: str, session_arg: str | None) -> str:
    """
    Найти/создать сессию.
    Если в корне канала лежит MP3/WAV/M4A — создаём новую сессию и перемещаем аудио.
    """
    if session_arg:
        return session_arg

    ch_dir = get_channel_dir(channel_id)
    for ext in ("*.mp3", "*.wav", "*.m4a"):
        found = list(ch_dir.glob(ext))
        if found:
            audio_src = found[0]
            new_session = "Video_" + datetime.now().strftime("%Y%m%d_%H%M%S")
            ensure_session_dirs(channel_id, new_session)
            dst = get_input_dir(channel_id, new_session) / "audio.mp3"
            shutil.move(str(audio_src), str(dst))
            log(f"Новая сессия: {new_session}  (аудио перемещено из корня канала)")
            return new_session

    session = get_last_session(channel_id)
    if not session:
        log(f"Нет сессий для канала {channel_id}")
        sys.exit(1)
    return session


def load_segments(result_json: Path) -> tuple[list, float]:
    """Загрузить сегменты и общую длительность из result.json."""
    if not result_json.exists():
        raise FileNotFoundError(f"result.json не найден: {result_json}")
    with open(result_json, encoding="utf-8") as f:
        data = json.load(f)
    segments = data.get("segments", [])
    total_dur = float(data.get("total_duration", 0.0))
    if not total_dur and segments:
        total_dur = float(segments[-1].get("end", 0.0))
    return segments, total_dur


def find_intro(channel_id: str, session: str) -> Path | None:
    """Найти intro.mp4 в папке сессии."""
    p = get_intro_path(channel_id, session)
    if p.exists() and p.stat().st_size > 10_000:
        return p
    session_dir = get_session_dir(channel_id, session)
    for f in sorted(session_dir.glob("*.mp4")):
        if f.stem.lower().startswith("intro") and f.stat().st_size > 10_000:
            return f
    return None


# ─────────────────────────────────────────────────────────────────────────────
# STEP 2: CLIPS
# ─────────────────────────────────────────────────────────────────────────────

def select_and_link_clips(
    channel_id:     str,
    session:        str,
    segments:       list,
    lib_clips_dir:  Path,
    temp_dir:       Path,
    intro_duration: float,
    skip_intro_clips: bool,
    text_only:      bool = False,
) -> tuple[Path, dict | None]:
    """
    Подобрать клипы через clip_selector, симлинковать main-клипы в temp/lib_clips/.
    Для интро-клипов — trim + сохранить в intro_clips/.
    Возвращает (lib_dir, selection_result).
    """
    if not _SELECTOR_OK:
        log("clip_selector недоступен — используем клипы сессии")
        return lib_clips_dir, None

    # Если clip_selection.json уже есть — не пересчитываем
    cs_path = get_session_dir(channel_id, session) / "clip_selection.json"
    if cs_path.exists():
        with open(cs_path, encoding="utf-8") as f:
            cs = json.load(f)
        log(f"clip_selection.json загружен (пропускаем подбор)")
        _mg_ov = cs.get("mg_overrides", {})
        if _mg_ov:
            log(f"MG overrides: {len(_mg_ov)} сегментов")
        result = {
            "intro_clips":    [tuple(x) for x in cs.get("intro_clips", [])],
            "main_clips":     [tuple(x) for x in cs.get("main_clips",  [])],
            "intro_duration": sum(d for _, _, d in [tuple(x) for x in cs.get("intro_clips", [])]),
            "main_duration":  sum(d for _, _, d in [tuple(x) for x in cs.get("main_clips",  [])]),
            "_clips_used":    [],
            "_history":       None,
            "_channel_id":    channel_id,
            "_session":       session,
            "_mg_overrides":  {int(k): v for k, v in _mg_ov.items()},
        }
    else:
        result = select_clips_for_video(
            session=session,
            channel_id=channel_id,
            segments=segments,
            max_repeats_in_video=3,
            max_from_prev=20,
            intro_duration=intro_duration,
            text_only=text_only,
        )

    main_clips   = result.get("main_clips",   [])
    intro_clips  = result.get("intro_clips",  [])

    log(f"Подобрано: main={len(main_clips)}, intro={len(intro_clips)}")

    # Симлинк main-клипов → temp/lib_clips/
    lib_dir = temp_dir / "lib_clips"
    lib_dir.mkdir(parents=True, exist_ok=True)

    # Пул запасных клипов — для сегментов у которых файл не найден в библиотеке
    _fallback_pool = [p for p in lib_clips_dir.glob("*.mp4") if p.is_file()]

    mg_overrides = result.get("_mg_overrides", {})
    mg_count     = 0

    matched = 0
    for seg_id, clip_id, _ in main_clips:
        dst = lib_dir / f"clip_{int(seg_id):03d}.mp4"
        if dst.exists():
            dst.unlink()

        # MG override: использовать сгенерированный клип вместо библиотечного
        if int(seg_id) in mg_overrides:
            mg_src = Path(mg_overrides[int(seg_id)])
            if mg_src.exists():
                try:
                    dst.symlink_to(mg_src)
                except OSError:
                    shutil.copy2(mg_src, dst)
                mg_count += 1
                matched  += 1
                continue
            else:
                log(f"  [{int(seg_id):03d}] MG файл не найден: {mg_src} → fallback на библиотечный")

        if clip_id is None:
            continue
        src = lib_clips_dir / f"{clip_id}.mp4"
        if not src.exists():
            if _fallback_pool:
                src = random.choice(_fallback_pool)
                log(f"  [{int(seg_id):03d}] не найден {clip_id}.mp4 → fallback {src.name}")
            else:
                log(f"  [{int(seg_id):03d}] файл не найден: {clip_id}.mp4")
                continue
        try:
            dst.symlink_to(src)
        except OSError:
            shutil.copy2(src, dst)
        matched += 1

    if mg_count:
        log(f"MG-клипы: {mg_count} сегментов заменено")
    log(f"Main-клипы: {matched}/{len(main_clips)} привязано")

    # Интро-клипы: trim stream copy (параллельно, без ре-энкода)
    # Клипы уже h264/1920×1080/yuv420p — stream copy мгновенный.
    if not skip_intro_clips and intro_clips:
        intro_out = get_intro_clips_dir(channel_id, session)
        intro_out.mkdir(parents=True, exist_ok=True)

        intro_tasks = []
        for idx, (seg_id, clip_id, seg_dur) in enumerate(intro_clips, 1):
            if clip_id is None:
                continue
            src = lib_clips_dir / f"{clip_id}.mp4"
            if not src.exists():
                continue
            dst = intro_out / f"intro_{idx:03d}.mp4"
            intro_tasks.append((src, dst, seg_dur, idx))

        def _trim_intro(args: tuple) -> bool:
            src, dst, seg_dur, idx = args
            cmd = [
                "ffmpeg", "-y", "-i", str(src),
                "-t", f"{seg_dur:.3f}",
                "-c:v", "copy", "-an",
                "-loglevel", "warning", str(dst),
            ]
            return _run_ffmpeg(cmd)

        t0_intro = time.time()
        ok_intro = 0
        with concurrent.futures.ThreadPoolExecutor(max_workers=min(16, os.cpu_count() or 8)) as ex:
            futs = {ex.submit(_trim_intro, t): t for t in intro_tasks}
            for fut, t in futs.items():
                if fut.result() and t[1].exists():
                    ok_intro += 1

        log(f"Intro-клипы: {ok_intro}/{len(intro_tasks)} за {time.time()-t0_intro:.1f}s → {intro_out}")

    return lib_dir, result


# ─────────────────────────────────────────────────────────────────────────────
# STEP 3: TIMELINE
# ─────────────────────────────────────────────────────────────────────────────

def detect_zone_boundary(segments: list) -> float:
    """
    Найти границу Zone A / Zone B из whisper-сегментов.
    Zone B начинается там где Whisper перешёл с рандома (2–4s) на строго 5s.
    Ищем первый сегмент с dur ≈ 5s за которым ещё минимум 2 таких же.
    """
    for i, seg in enumerate(segments):
        dur = float(seg.get("end", 0)) - float(seg.get("start", 0))
        if abs(dur - 5.0) < 0.25:
            # Проверяем что следующие 2 тоже ~5s (не случайное совпадение)
            run = sum(
                1 for s in segments[i : i + 3]
                if abs((float(s.get("end", 0)) - float(s.get("start", 0))) - 5.0) < 0.25
            )
            if run >= 2:
                return float(seg.get("start", 0.0))
    return float("inf")  # всё Zone A если 5s-сегментов нет


def compute_timings(
    segments:      list,
    zone_a_end_s:  float | None = None,
) -> list[dict]:
    """
    Тайминг клипов:
      Зона A (рандом 2–4s от Whisper): duration = seg.end - seg.start
      Зона B (строго 5s от Whisper):   duration = seg.end - seg.start (те же 5s)
      Граница зон: авто-определяется из данных Whisper (detect_zone_boundary).
      Никакого "остатка" — каждый клип получает ровно столько, сколько сказал Whisper.

    Возвращает list[{"seg_id", "start", "end", "duration", "zone"}].
    """
    if zone_a_end_s is None:
        zone_a_end_s = detect_zone_boundary(segments)

    timings = []
    video_t = 0.0

    for i, seg in enumerate(segments):
        seg_start = float(seg.get("start", 0.0))
        seg_end   = float(seg.get("end",   0.0))
        seg_id    = int(seg.get("id", i + 1))

        zone = "A" if seg_start < zone_a_end_s else "B"
        # Zone B клипы строго 5.0s → тайминг тоже строго 5.0s (устраняет дрейф ~4-7s)
        if zone == "B":
            dur = 5.0
        else:
            dur = max(seg_end - seg_start, 0.5)

        timings.append({
            "seg_id":   seg_id,
            "start":    video_t,
            "end":      video_t + dur,
            "duration": dur,
            "zone":     zone,
        })
        video_t += dur

    return timings


def trim_clips(
    segments:   list,
    timings:    list[dict],
    lib_dir:    Path,
    temp_dir:   Path,
    trans_dur:  float = 0.5,
) -> list[Path]:
    """
    Обрезать клипы до нужной длины через stream copy (мгновенно).
    Все клипы из библиотеки строго 5.0s, h264/1920x1080/25fps/yuv420p.

    Zone A (переменная длина): trim до dur (+ handle если transition после).
    Zone B (5.0s сегменты):    symlink без обрезки если нет handle,
                                trim до 5.5s если есть transition после.
    Лупинг не нужен — все клипы >= любого сегмента.
    Параллельно, max_workers=12.
    """
    out_dir = temp_dir / "trimmed"
    out_dir.mkdir(parents=True, exist_ok=True)

    all_order: list[Path] = []
    tasks: list[tuple[Path, Path, float, int]] = []  # (src, dst, dur, seg_id)
    copy_tasks: list[tuple[Path, Path]] = []          # Zone B: (src, dst) прямая копия

    for idx, (seg, timing) in enumerate(zip(segments, timings)):
        seg_id = timing["seg_id"]
        dur    = timing["duration"]
        src    = lib_dir / f"clip_{seg_id:03d}.mp4"
        if not src.exists():
            log(f"  [{seg_id:03d}] клип не найден — пропущен")
            continue
        dst = out_dir / f"t_{seg_id:03d}.mp4"
        all_order.append(dst)

        # tpad-архитектура: фризфреймы для xfade генерируются синтетически фильтром,
        # физический хвостовой handle клипу не нужен — trim строго до dur сегмента.
        if abs(dur - 5.0) > 0.05:
            # Zone A: нужна обрезка до точной длины сегмента
            tasks.append((src, dst, dur, seg_id))
        else:
            # Zone B: тоже re-encode с tpad для гарантированной точной длины 5.0s.
            # shutil.copy2 давал ~4.96s клипы → видео короче озвучки на N*0.04s.
            tasks.append((src, dst, dur, seg_id))

    n_trim = len(tasks)
    n_copy = len(copy_tasks)
    if tasks or copy_tasks:
        log(f"Trim {n_trim} (Zone A) + copy {n_copy} (Zone B) клипов на D: (12 потоков)...")
        t0 = time.time()

        def _trim(args: tuple) -> None:
            src, dst, dur, seg_id = args
            # Re-encode с tpad=clone чтобы гарантировать точную длину:
            # stream copy (-c:v copy) режет по keyframe → клип может быть
            # короче на 1-2 кадра → GPU compositor читает EOF раньше времени.
            cmd = [
                "ffmpeg", "-y", "-i", str(src),
                "-vf", f"fps=25,tpad=stop_mode=clone:stop_duration=0.08",
                "-t", f"{dur:.3f}",
                "-c:v", "libx264", "-preset", "ultrafast", "-crf", "18",
                "-pix_fmt", "yuv420p", "-an",
                "-loglevel", "warning",
                str(dst),
            ]
            if not _run_ffmpeg(cmd):
                log(f"  [{seg_id:03d}] trim failed")

        def _copy(args: tuple) -> None:
            src, dst = args
            shutil.copy2(src, dst)

        with concurrent.futures.ThreadPoolExecutor(max_workers=min(16, os.cpu_count() or 12)) as ex:
            futs = [ex.submit(_trim, t) for t in tasks] + \
                   [ex.submit(_copy, t) for t in copy_tasks]
            for fut in concurrent.futures.as_completed(futs):
                fut.result()

        log(f"Trim+Copy: {n_trim + n_copy} клипов за {time.time()-t0:.1f}s")

    ordered = [p for p in all_order if p.exists()]
    log(f"Итого клипов: {len(ordered)} (trim: {n_trim}, copy: {n_copy})")
    return ordered


_COSMOS_CHANNELS = {"channel_001_cosmos_de", "channel_002_cosmos_fr"}


def build_transition_plan(
    n_clips:          int,
    timings:          list[dict],
    zone_a_end_s:     float,
    zone_b_count:     int,
    clip_trans_name:  str,
    clip_trans_dur:   float,
    channel_id:       str = "",
) -> list[dict]:
    """
    Зона A: переход на КАЖДОМ стыке.
      - random-режим: лёгкие переходы + 1-2 тяжёлых (whip/glitch) только в зоне A.
      - Тяжёлый на стыке интро→клипы: только для cosmos-каналов.
    Зона B: N рандомных стыков, только лёгкие переходы.
      - N = 20 при 5 мин зоны B, +4 за каждую минуту свыше 5, макс 40.

    Возвращает list[{"type", "dur"}] длиной n_clips-1.
    """
    from transitions import build_random_trans_seq, _POOL_HEAVY, _POOL_HEAVY_WEIGHTS, _weighted_choice

    _HEAVY_NAMES = {p[0] for p in _POOL_HEAVY}
    is_cosmos    = channel_id in _COSMOS_CHANNELS
    n_boundaries = max(0, n_clips - 1)
    if n_boundaries == 0:
        return []

    is_random = (clip_trans_name == "random")

    # ── Индексы стыков по зонам ───────────────────────────────────────────────
    zone_a_indices = [
        i for i in range(n_boundaries)
        if i < len(timings) and timings[i].get("zone") == "A"
    ]
    zone_b_indices = [
        i for i in range(n_boundaries)
        if i < len(timings) and timings[i].get("zone") == "B"
    ]

    # ── Динамический zone_b_count по длине зоны B ─────────────────────────────
    zone_b_dur_s = sum(
        timings[i].get("duration", 0) for i in zone_b_indices if i < len(timings)
    )
    if is_random:
        extra_min    = max(0, (zone_b_dur_s - 300) / 60)
        zone_b_count = min(40, int(20 + extra_min * 4))

    zone_b_selected = set(
        random.sample(zone_b_indices, min(zone_b_count, len(zone_b_indices)))
    ) if zone_b_indices else set()

    # ── Генерация переходов ───────────────────────────────────────────────────
    if is_random:
        # Зона A: все стыки лёгкие по умолчанию
        zone_a_seq = build_random_trans_seq(len(zone_a_indices), light_only=True)
        zone_a_map = {idx: t for idx, t in zip(zone_a_indices, zone_a_seq)}

        # Тяжёлые в зоне A — только для cosmos-каналов:
        #   1. Первый стык (интро→клипы): рандомный тяжёлый
        #   2. Ещё один тяжёлый рандомно, но не раньше чем через 15 стыков
        if is_cosmos and zone_a_indices:
            import random as _rnd
            _rng = _rnd.Random()
            _heavy_pool = _POOL_HEAVY  # импортируется выше из transitions

            # Первый стык — рандомный тяжёлый
            first_a = zone_a_indices[0]
            h_name, h_dur, _ = _weighted_choice(_heavy_pool, _POOL_HEAVY_WEIGHTS, _rng)
            zone_a_map[first_a] = {"type": h_name, "dur": h_dur}

            # Второй тяжёлый — рандомно среди стыков с позицией >= first_a + 15
            eligible = [idx for idx in zone_a_indices if idx >= first_a + 15]
            if eligible:
                second_a = _rng.choice(eligible)
                h_name2, h_dur2, _ = _weighted_choice(_heavy_pool, _POOL_HEAVY_WEIGHTS, _rng)
                zone_a_map[second_a] = {"type": h_name2, "dur": h_dur2}

        # Зона B: только лёгкие
        zone_b_seq = build_random_trans_seq(len(zone_b_selected), light_only=True)
        zone_b_map = {idx: t for idx, t in zip(sorted(zone_b_selected), zone_b_seq)}

    # ── Сборка плана ──────────────────────────────────────────────────────────
    plan = []
    for i in range(n_boundaries):
        in_zone_b = i in set(zone_b_indices)

        if in_zone_b and i not in zone_b_selected:
            plan.append({"type": "cut", "dur": 0.0})
        elif is_random:
            t = zone_b_map.get(i) or zone_a_map.get(i, {"type": "fadeblack", "dur": 0.35})
            plan.append(t)
        else:
            plan.append({"type": clip_trans_name, "dur": clip_trans_dur})

    # ── Лог ──────────────────────────────────────────────────────────────────
    zone_a_trans  = sum(1 for i, p in enumerate(plan)
                        if p["type"] != "cut" and i in set(zone_a_indices))
    zone_b_actual = sum(1 for i, p in enumerate(plan)
                        if p["type"] != "cut" and i in set(zone_b_indices))
    if is_random:
        heavy_in_a = [p["type"] for i, p in enumerate(plan)
                      if p["type"] in _HEAVY_NAMES and i in set(zone_a_indices)]
        log(f"Переходы: Зона A={zone_a_trans}/{len(zone_a_indices)} "
            f"(тяжёлые в A: {heavy_in_a or 'нет'}) | "
            f"Зона B={zone_b_actual}/{len(zone_b_indices)} "
            f"(лимит={zone_b_count}, {zone_b_dur_s/60:.1f}мин)")
    else:
        log(f"Переходы: Зона A={zone_a_trans} | Зона B={zone_b_actual}/{len(zone_b_indices)} стыков")

    return plan


# ─────────────────────────────────────────────────────────────────────────────
# STEP 4: RENDER
# ─────────────────────────────────────────────────────────────────────────────

def render_videotrack(
    trimmed_clips: list[Path],
    trans_plan:    list[dict],
    trans_name:    str,
    trans_dur:     float,
    output:        Path,
    expected_dur:  float = 0.0,
) -> bool:
    """Склеить клипы с переходами → videotrack.mp4."""
    log(f"Рендер видеоряда: {len(trimmed_clips)} клипов → {output.name}")
    ok = concat_all_with_transitions(
        trimmed_clips, output, trans_name, trans_dur,
        trans_plan if trans_plan else None,
    )
    if ok and output.exists():
        actual = get_duration(output)
        diff   = actual - expected_dur
        sign   = "+" if diff >= 0 else ""
        log(f"  ✅ videotrack: {actual:.1f}s (ожидалось {expected_dur:.1f}s, Δ={sign}{diff:.1f}s)")


    return bool(ok) and output.exists()


def build_timeline_json(
    trimmed_clips: list[Path],
    trans_plan:    list[dict],
    intro_path:    Path | None,
    intro_trans_dur: float,
    fps:           float,
    output_path:   Path,
) -> Path:
    """
    Строит timeline.json — декларативное описание монтажа.
    Заменяет encode videotrack.mp4: никакого промежуточного рендера.
    GPU compositor читает клипы напрямую из этого файла.
    """
    clips = []
    for p in trimmed_clips:
        try:
            dur = get_duration(p)
        except Exception:
            dur = 0.0
        clips.append({"path": str(p), "duration": round(dur, 6)})

    # Приводим trans_plan к нужному формату (может содержать лишние поля)
    transitions = [
        {"type": t.get("type", "cut"), "dur": float(t.get("dur", 0.0))}
        for t in trans_plan
    ]

    timeline = {
        "version":        1,
        "fps":            fps,
        "intro":          str(intro_path) if intro_path else None,
        "intro_trans_dur": intro_trans_dur,
        "clips":          clips,
        "transitions":    transitions,
    }
    with open(output_path, "w", encoding="utf-8") as f:
        json.dump(timeline, f, ensure_ascii=False, indent=2)

    log(f"timeline.json: {len(clips)} клипов, "
        f"{sum(1 for t in transitions if t['type'] != 'cut')} переходов → {output_path.name}")
    return output_path


def build_audio_track(
    voiceover:    Path,
    intro_path:   Path | None,
    intro_dur:    float,
    total_dur:    float,
    style:        dict,
    output:       Path,
    no_music:     bool,
) -> bool:
    """
    Единый аудио-пасс через build_final_audio().
    DE/FR: intro audio тихо под основной озвучкой, музыка стартует после интро.
    ES:    без интро, музыка с t=0.
    """
    has_intro = intro_path is not None and intro_path.exists() and intro_dur > 0.5
    music_start = intro_dur if has_intro else 0.0
    music_vol   = float(style.get("music_vol_db", -22.0))
    intro_vol   = float(style.get("intro_audio_vol_db", -20.0))

    lufs_target = float(style.get("lufs_target", -16.0))
    build_final_audio(
        voice_path       = voiceover,
        output_path      = output,
        sfx_events       = [],
        music_start_sec  = music_start,
        music_vol_db     = music_vol if not no_music else -100.0,
        intro_audio_path = str(intro_path) if has_intro else None,
        intro_trim_s     = intro_dur + 2.0 if has_intro else 0.0,
        intro_vol_db     = intro_vol,
        video_duration   = total_dur,
        lufs_target      = lufs_target,
    )
    return output.exists()




def _suppress_mg_zones_in_ass(
    ass_path: Path,
    mg_zones: list[tuple[float, float]],  # (start, end) в Whisper-времени
    sub_intro_dur: float,
) -> None:
    """
    Убирает Dialogue-строки .ass файла, чьё время полностью или частично
    попадает в MG fullscreen-зону. Тайминги субтитров — в sub_time = whisper - sub_intro_dur.
    """
    import re as _re
    if not mg_zones or not ass_path.exists():
        return
    # Конвертируем MG-зоны в subtitle-время
    sub_zones = [(max(0.0, s - sub_intro_dur), max(0.0, e - sub_intro_dur)) for s, e in mg_zones]

    def _ass_ts_to_sec(ts: str) -> float:
        # формат: H:MM:SS.cs (centiseconds)
        try:
            h, m, rest = ts.split(":")
            s, cs = rest.split(".")
            return int(h) * 3600 + int(m) * 60 + int(s) + int(cs) / 100.0
        except Exception:
            return 0.0

    lines = ass_path.read_text(encoding="utf-8").splitlines(keepends=True)
    out = []
    removed = 0
    for line in lines:
        if not line.startswith("Dialogue:"):
            out.append(line)
            continue
        parts = line.split(",", 9)
        if len(parts) < 10:
            out.append(line)
            continue
        s_sec = _ass_ts_to_sec(parts[1].strip())
        e_sec = _ass_ts_to_sec(parts[2].strip())
        overlap = any(s_sec < z_end and e_sec > z_start for z_start, z_end in sub_zones)
        if overlap:
            removed += 1
        else:
            out.append(line)
    if removed:
        ass_path.write_text("".join(out), encoding="utf-8")
        log(f"[SUBS] Убрано {removed} строк субтитров из MG-зон")


def generate_subtitles(
    result_json:    Path,
    ass_path:       Path,
    style:          dict,
    intro_dur:      float,
    no_subs:        bool,
    intro_trans_dur: float = 0.0,
    mg_zones:       list[tuple[float, float]] | None = None,
) -> tuple[bool, str]:
    """
    Генерация субтитров по стилю канала.
    Возвращает: (ass_ok, drawtext_filter)
      - DE "default" / FR "karaoke" / ES "scripture": ass_ok=True, drawtext_filter=""
    """
    if no_subs or not result_json.exists():
        return False, ""

    # Эффективный сдвиг субтитров: intro_dur минус длина перехода интро→контент.
    # _ffmpeg_merge_intro_clips использует xfade с offset=intro_dur-trans_dur,
    # т.е. контент начинается на trans_dur раньше конца интро в финальном видео.
    # Без поправки субтитры спешали бы на trans_dur секунд относительно аудио.
    sub_intro_dur = max(0.0, intro_dur - intro_trans_dur)

    sub_style = style.get("subtitle_style", "default")
    font_name = "Organetto Bold" if Path(ORGANETTO_FONT_PATH).exists() else "Organetto"
    font_size = SUBTITLE_FONT_SIZE + style.get("subtitle_size_offset", 0)
    rise_px   = SUBTITLE_RISE_PX  + style.get("subtitle_rise_extra_px", 0)
    border    = style.get("subtitle_border_style", 1)

    try:
        if sub_style == "karaoke":
            karaoke_font = style.get("subtitle_font", font_name)
            generate_karaoke_ass(
                str(result_json), str(ass_path),
                karaoke_font, font_size,
                SUBTITLE_FADE_IN_MS, rise_px,
                sub_intro_dur, 3, "&H0000FFFF", "&H00707070", border,
            )
        elif sub_style == "scripture":
            generate_scripture_ass(
                str(result_json), str(ass_path),
                style.get("subtitle_font", "Montserrat Bold"), font_size,
                350, 200, sub_intro_dur,
                style.get("subtitle_max_words", 5),
            )
        elif sub_style == "scale_pop":
            # FR "scale_pop" → drawtext, 6-шаговый scale ease-out + тень
            fp = style.get("subtitle_font_path", "C:/Windows/Fonts/arialbd.ttf")
            dt_filter = generate_drawtext_filter(
                result_json_path = result_json,
                font_path        = fp,
                font_size        = font_size,
                fade_out         = SUBTITLE_FADE_OUT_MS / 1000.0,
                intro_duration   = sub_intro_dur,
                max_words        = style.get("subtitle_max_words", 2),
                animation        = "scale_pop",
                shadow_opacity   = 0.60,
                shadow_x         = 3,
                shadow_y         = 4,
            )
            return False, dt_filter
        else:
            # DE "default" → ASS (fade + rise)
            generate_ass(
                result_json_path = result_json,
                output_ass_path  = ass_path,
                font_name        = font_name,
                font_size        = font_size,
                fade_in_ms       = SUBTITLE_FADE_IN_MS,
                fade_out_ms      = SUBTITLE_FADE_OUT_MS,
                rise_px          = rise_px,
                intro_duration   = sub_intro_dur,
                border_style     = border,
            )
        # Убрать субтитры поверх MG fullscreen-зон
        if mg_zones and ass_path.exists():
            _suppress_mg_zones_in_ass(ass_path, mg_zones, sub_intro_dur)
        return ass_path.exists(), ""
    except Exception as e:
        log(f"[SUBS] ошибка: {e}")
        return False, ""


def _color_grade_filter(color_grade: str, start_sec: float = 0.0) -> str:
    """FFmpeg vf-фильтр цветокоррекции."""
    ce = f"enable='gte(t,{start_sec:.3f})'"
    if color_grade == "cool_cinematic":
        return (
            f"eq=contrast=1.04:saturation=0.97:gamma=1.03:brightness=0.00:{ce},"
            f"colorbalance=rs=-0.03:gs=-0.01:bs=0.04:rm=-0.02:gm=0.00:bm=0.02:"
            f"rh=-0.03:gh=0.00:bh=0.03:{ce},"
            f"vignette=PI/6:{ce}"
        )
    elif color_grade == "gold_dramatic":
        return (
            f"eq=contrast=1.12:saturation=0.82:gamma=0.94:brightness=0.01:{ce},"
            f"colorbalance=rs=0.09:gs=0.03:bs=-0.09:rm=0.05:gm=0.02:bm=-0.05:"
            f"rh=0.06:gh=0.02:bh=-0.07:{ce},"
            f"vignette=PI/4:{ce}"
        )
    else:  # warm_cinematic (default)
        return (
            f"eq=contrast=1.05:saturation=1.04:gamma=0.97:brightness=0.01:{ce},"
            f"colorbalance=rs=0.04:gs=0.01:bs=-0.03:rm=0.02:gm=0.01:bm=-0.02:"
            f"rh=0.03:gh=0.01:bh=-0.04:{ce},"
            f"vignette=PI/6:{ce}"
        )


def final_render(
    intro_path:     Path | None,
    videotrack:     Path,
    audio_path:     Path,
    ass_path:       Path,
    ass_ok:         bool,
    drawtext_filter: str,
    style:          dict,
    intro_dur:      float,
    output:         Path,
) -> bool:
    """
    Один финальный FFmpeg-проход:
      intro + videotrack → xfade → color grade → subtitles → audio → output

    Субтитры:
      DE "default": drawtext_filter встраивается в vf (нет отдельного прохода)
      FR "karaoke" / ES "scripture": ass= filter из .ass файла
    """
    color_grade = style.get("color_grade", "warm_cinematic")
    color_filter = _color_grade_filter(color_grade, start_sec=intro_dur)

    # Субтитры: drawtext (DE) или ass= (FR/ES)
    sub_filter = None
    if drawtext_filter:
        sub_filter = drawtext_filter
        log("  Субтитры: drawtext (встроено в filter_complex)")
    elif ass_ok and ass_path.exists():
        ass_posix = ass_path.as_posix()
        if len(ass_posix) >= 2 and ass_posix[1] == ":":
            ass_posix = ass_posix[0] + "\\:" + ass_posix[2:]
        font_dir = ""
        if Path(ORGANETTO_FONT_PATH).exists():
            fd = Path(ORGANETTO_FONT_PATH).parent.as_posix()
            if len(fd) >= 2 and fd[1] == ":":
                fd = fd[0] + "\\:" + fd[2:]
            font_dir = fd
        sub_filter = (
            f"ass='{ass_posix}':fontsdir='{font_dir}'" if font_dir
            else f"ass='{ass_posix}'"
        )

    vf = f"{color_filter},{sub_filter}" if sub_filter else color_filter
    metadata = make_metadata_flags()

    intro_trans_fn = get_intro_transition_fn(style)
    intro_trans_dur = float(style.get("clip_transition_duration", 0.5))

    log(f"Финальный рендер → {output.name}")
    log(f"  Цвет: {color_grade}  |  Субтитры: {'ASS' if ass_ok else 'нет'}")

    audio_arg = str(audio_path) if audio_path.exists() else ""
    fc_kwargs = dict(
        post_vf        = vf,
        audio_path     = audio_arg,
        metadata_flags = metadata,
    )

    if intro_path and videotrack.exists():
        ok = intro_trans_fn(intro_path, videotrack, output, intro_trans_dur, **fc_kwargs)
        if not ok:
            log("  Переход не удался — простая склейка")
            _final_concat([str(intro_path), str(videotrack)], output, **fc_kwargs)
    elif intro_path:
        _final_concat([str(intro_path)], output, **fc_kwargs)
    elif videotrack.exists():
        _final_concat([str(videotrack)], output, **fc_kwargs)
    else:
        log("Нет ни интро, ни видеоряда!")
        return False

    return output.exists()


# ─────────────────────────────────────────────────────────────────────────────
# STEP 5: COMMIT & CLEANUP
# ─────────────────────────────────────────────────────────────────────────────

def do_commit_history(
    selection_result: dict | None,
    channel_id:       str,
    session:          str,
    final_output:     Path,
    *,
    force: bool = False,
) -> None:
    """Зафиксировать историю клипов после успешного рендера.

    force=True — пропустить проверку существования final_output
    (используется при ранней фиксации сразу после подбора клипов).
    """
    if not force and not final_output.exists():
        return
    if not selection_result or not _SELECTOR_OK:
        return

    cs_path = get_session_dir(channel_id, session) / "clip_selection.json"
    already = False
    if cs_path.exists():
        try:
            with open(cs_path, encoding="utf-8") as f:
                already = json.load(f).get("history_committed", False)
        except Exception:
            pass

    if already:
        log("История уже зафиксирована (re-render)")
        return

    commit_clip_history(selection_result)
    try:
        cs_data = {}
        if cs_path.exists():
            with open(cs_path, encoding="utf-8") as f:
                cs_data = json.load(f)
        cs_data["history_committed"] = True
        with open(cs_path, "w", encoding="utf-8") as f:
            json.dump(cs_data, f, ensure_ascii=False, indent=2)
    except Exception as e:
        log(f"  Не удалось обновить clip_selection.json: {e}")
    log("История клипов сохранена")


# ─────────────────────────────────────────────────────────────────────────────
# MOTION GRAPHICS  (Gemini plan → Remotion render → inject into lib_clips)
# ─────────────────────────────────────────────────────────────────────────────

# Композиции в panel-режиме: панель выезжает слева, видео сдвигается вправо
_MG_PANEL_COMPS = {"List"}
_MG_PANEL_W     = 640   # ширина панели (px)
_MG_VIDEO_SHIFT = 384   # сдвиг видео вправо (px)


def _plan_motion_graphics(channel_id: str, session: str, segments: list) -> list[dict] | None:
    """
    Gemini анализирует транскрипт и возвращает список MG-зон.
    Кеш: session_dir/motion_graphics_plan.json — если есть, Gemini не вызывается.
    """
    session_dir = get_session_dir(channel_id, session)
    plan_path   = session_dir / "motion_graphics_plan.json"

    if plan_path.exists():
        log("MG: план загружен из кеша")
        return json.loads(plan_path.read_text(encoding="utf-8")).get("zones", [])

    try:
        import mg_planner_gemini as _mgp
        _LANG = {
            "channel_001_cosmos_de": "German",
            "channel_002_cosmos_fr": "French",
            "channel_003_religion_es": "Spanish",
        }
        lang  = _LANG.get(channel_id, "German")
        log(f"MG: планирование Gemini  (lang={lang}, {len(segments)} сегментов)...")
        zones = _mgp.plan_zones(segments, lang=lang)

        plan_path.write_text(
            json.dumps({"channel_id": channel_id, "session": session, "zones": zones},
                       ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        # Сохраняем читаемый TXT для проверки
        txt_path = session_dir / "motion_graphics_plan.txt"
        lines = [f"MG ПЛАН — {session}", "=" * 60]
        for z in zones:
            props = z.get("props", {})
            lines += [
                f"\nZone {z['zone_id']:02d}: {z['start_time']:.0f}s – {z['end_time']:.0f}s"
                f"  [{z['composition']}]  palette={z.get('palette','')}",
                f"  title : {z.get('title', '')}",
            ]
            for k, v in props.items():
                if k in ("bg_color", "accent_color"):
                    continue
                lines.append(f"  {k:<12}: {v}")
        txt_path.write_text("\n".join(lines), encoding="utf-8")

        log(f"MG: {len(zones)} зон → {plan_path.name}")
        log(f"MG: план для проверки → {txt_path}")
        for z in zones:
            log(f"  Zone {z['zone_id']:02d}: {z['start_time']:.0f}s-{z['end_time']:.0f}s  "
                f"{z['composition']:<12}  {str(z.get('title',''))[:40]}")
        return zones

    except Exception as _e:
        log(f"[MG] Планирование провалилось: {_e}")
        return None


def _render_mg_zones(zones: list[dict], mg_dir: Path) -> list[dict]:
    """
    Рендерит Remotion-анимации параллельно (4 потока).
    Возвращает зоны с заполненным rendered_path.
    """
    if not zones:
        return []
    mg_dir.mkdir(parents=True, exist_ok=True)

    try:
        import mg_renderer as _mgr
    except ImportError as _e:
        log(f"[MG] mg_renderer недоступен: {_e}")
        return zones

    log(f"MG: рендер {len(zones)} зон (Remotion)...")
    t0 = time.time()

    def _one(z: dict) -> dict:
        mp4 = _mgr.render_zone(z, mg_dir)
        return {**z, "rendered_path": str(mp4) if mp4 else None}

    with concurrent.futures.ThreadPoolExecutor(max_workers=min(4, len(zones))) as ex:
        rendered = list(ex.map(_one, zones))

    ok = sum(1 for z in rendered if z.get("rendered_path"))
    log(f"MG: рендер завершён — {ok}/{len(zones)} зон за {time.time()-t0:.1f}s")
    return rendered


def _mix_mg_audio_into_track(
    audio_path: Path,
    mg_sfx_events: list[dict],  # {"file": str, "time_s": float}
    temp_dir: Path,
) -> bool:
    """Накладывает MG SFX-аудио поверх готового аудиотрека (voice+music) через adelay."""
    if not mg_sfx_events or not audio_path.exists():
        return True
    mixed = temp_dir / "_mg_audio_mixed.wav"
    cmd = ["ffmpeg", "-y", "-i", str(audio_path)]
    for ev in mg_sfx_events:
        cmd += ["-i", str(ev["file"])]
    fc = ["[0:a]aresample=44100[base]"]
    for k, ev in enumerate(mg_sfx_events):
        delay_ms = max(0, int(float(ev["time_s"]) * 1000))
        fc.append(f"[{k+1}:a]adelay={delay_ms}|{delay_ms},volume=0.85[mg{k}]")
    all_in = "[base]" + "".join(f"[mg{k}]" for k in range(len(mg_sfx_events)))
    fc.append(f"{all_in}amix=inputs={1+len(mg_sfx_events)}:duration=first:normalize=0[out]")
    cmd += ["-filter_complex", ";".join(fc), "-map", "[out]",
            "-c:a", "pcm_f32le", "-ar", "44100", "-loglevel", "warning", str(mixed)]
    r = subprocess.run(cmd, capture_output=True)
    if r.returncode == 0 and mixed.exists():
        mixed.replace(audio_path)
        log(f"MG: SFX аудио подмешано ({len(mg_sfx_events)} зон)")
        return True
    log(f"MG: ошибка подмешивания SFX аудио: {r.stderr.decode(errors='replace')[:200]}")
    return False


def _inject_mg_into_lib_clips(
    zones:    list[dict],
    lib_dir:  Path,
    temp_dir: Path,
    segments: list[dict],
) -> tuple[int, list[dict], set[int]]:
    """
    Встраивает MG-клипы прямо в temp/lib_clips/ до финального рендера:
      - Fullscreen: нарезаем MG по сегментам и relink каждый clip_NNN.mp4
      - Panel (List): bake(lib_clip + panel_overlay) → clip_NNN.mp4
    Возвращает (injected_count, mg_sfx_events, intra_mg_seg_ids).
    intra_mg_seg_ids — сегменты 2..N каждой зоны (не первый): на этих стыках
    нужен cut-переход чтобы не разрывать непрерывную Remotion-анимацию.
    """
    if not zones:
        return 0, [], set()

    mg_audio_dir = temp_dir / "mg_audio"
    mg_audio_dir.mkdir(parents=True, exist_ok=True)

    # (start, end, seg_id) — в порядке возрастания start
    seg_times: list[tuple[float, float, int]] = sorted(
        [(float(s.get("start", 0)), float(s.get("end", 0)), int(s.get("id", i + 1)))
         for i, s in enumerate(segments)],
        key=lambda x: x[0],
    )

    injected = 0
    sfx_events: list[dict] = []
    intra_mg_seg_ids: set[int] = set()

    for zone in zones:
        rendered = zone.get("rendered_path")
        if not rendered or not Path(rendered).exists():
            log(f"  [MG] Zone {zone['zone_id']} не отрендерена — пропуск")
            continue

        comp    = zone.get("composition", "")
        z_start = float(zone.get("start_time", 0))
        z_end   = float(zone.get("end_time",   0))
        anim    = Path(rendered)

        # Все сегменты, чей start попадает в зону [z_start, z_end)
        covered = [(s, e, sid) for (s, e, sid) in seg_times if z_start <= s < z_end]
        if not covered:
            closest = min(seg_times, key=lambda x: abs(x[0] - z_start))
            covered = [(closest[0], closest[1], closest[2])]

        if comp in _MG_PANEL_COMPS:
            seg_ids = [sid for (_, _, sid) in covered]
            n = _bake_panel_zone(seg_ids, anim, covered, lib_dir, temp_dir)
            log(f"  [MG] Zone {zone['zone_id']} (List/panel): {n}/{len(seg_ids)} сег бекнуто")
            injected += (1 if n > 0 else 0)
            intra_mg_seg_ids.update(seg_ids[1:])  # 2..N сег — стыки без перехода
        else:
            # Fullscreen: нарезаем MG-клип по каждому сегменту зоны.
            ok = _slice_mg_into_segs(anim, covered, z_start, lib_dir, temp_dir)
            log(f"  [MG] Zone {zone['zone_id']} ({comp}): fullscreen {ok}/{len(covered)} сег")
            injected += (1 if ok > 0 else 0)
            all_sids = [sid for (_, _, sid) in covered]
            intra_mg_seg_ids.update(all_sids[1:])  # 2..N сег — стыки без перехода

        # Извлечь аудио из MG-зоны для подмешивания в финальный трек
        audio_out = mg_audio_dir / f"mg_zone_{zone['zone_id']}.wav"
        if not audio_out.exists():
            r = subprocess.run(
                ["ffmpeg", "-y", "-i", str(anim),
                 "-vn", "-c:a", "pcm_f32le", "-ar", "44100",
                 "-loglevel", "warning", str(audio_out)],
                capture_output=True,
            )
            if r.returncode != 0 or not audio_out.exists():
                log(f"  [MG] Аудио зоны {zone['zone_id']} не извлечено")
                continue
        sfx_events.append({"file": str(audio_out), "time_s": z_start, "seg_id": covered[0][2]})
        log(f"  [MG] Аудио зоны {zone['zone_id']} → {audio_out.name} (t={z_start:.1f}s)")

    return injected, sfx_events, intra_mg_seg_ids


def _slice_mg_into_segs(
    anim_mp4:   Path,
    covered:    list[tuple[float, float, int]],  # (start, end, seg_id) в порядке возрастания
    zone_start: float,
    lib_dir:    Path,
    temp_dir:   Path,
) -> int:
    """
    Нарезает fullscreen MG-клип на части по длительности каждого сегмента.
    Каждая часть заменяет соответствующий clip_NNN.mp4 в lib_dir.
    """
    slice_dir = temp_dir / "mg_slices"
    slice_dir.mkdir(parents=True, exist_ok=True)
    ok = 0

    # Длина анимации — защита от нарезки за конец файла (плановый dur < zone span)
    _anim_dur_r = subprocess.run(
        ["ffprobe", "-v", "error", "-show_entries", "format=duration",
         "-of", "csv=p=0", str(anim_mp4)],
        capture_output=True, text=True,
    )
    _anim_file_dur = float(_anim_dur_r.stdout.strip() or "0") if _anim_dur_r.returncode == 0 else 0.0

    for seg_start, seg_end, seg_id in covered:
        anim_offset = seg_start - zone_start   # позиция в MG-клипе
        seg_dur     = seg_end - seg_start

        # Сегмент начинается за концом анимации → пропустить (план vs рендер расходятся)
        if _anim_file_dur > 0 and anim_offset >= _anim_file_dur - 0.05:
            log(f"  [MG] Slice seg {seg_id:03d}: offset={anim_offset:.2f}s >= anim_dur={_anim_file_dur:.2f}s → skip")
            continue
        # Усечь сегмент если выходит за конец анимации
        if _anim_file_dur > 0:
            seg_dur = min(seg_dur, _anim_file_dur - anim_offset)

        sliced = slice_dir / f"slice_{seg_id:03d}.mp4"
        if not sliced.exists():
            # Output seek (-ss после -i): точный побайтный старт, без keyframe drift.
            # Input seek был бы быстрее, но для коротких зон (8-10s) разница <0.5s.
            cmd = [
                "ffmpeg", "-y",
                "-i", str(anim_mp4),
                "-ss", f"{anim_offset:.3f}",
                "-t",  f"{seg_dur:.3f}",
                "-vf", "fps=25,scale=1920:1080",
                "-c:v", "libx264", "-preset", "fast", "-crf", "17",
                "-pix_fmt", "yuv420p", "-an",
                "-loglevel", "warning",
                str(sliced),
            ]
            r = subprocess.run(cmd, capture_output=True)
            if r.returncode != 0 or not sliced.exists():
                log(f"  [MG] Slice seg {seg_id:03d} FAILED")
                continue

        dst = lib_dir / f"clip_{seg_id:03d}.mp4"
        try:
            if dst.is_symlink() or dst.exists():
                dst.unlink()
            dst.symlink_to(sliced.resolve())
        except OSError:
            shutil.copy2(sliced, dst)
        ok += 1

    return ok


def _bake_panel_zone(
    seg_ids:  list[int],
    anim_mp4: Path,
    covered:  list[tuple[float, float, int]],  # (start, end, seg_id) упорядоченные
    lib_dir:  Path,
    temp_dir: Path,
) -> int:
    """
    List-режим: панель Remotion выезжает слева поверх каждого lib-клипа.
    Видео сдвигается на VIDEO_SHIFT вправо.
    Каждый запечённый клип — stream-ready: 25fps, 1920×1080, без аудио.

    Панель въезжает в начале первого клипа, статична в середине, выезжает в конце последнего.
    Анимация берётся из нужного временного диапазона MG-клипа через -ss на инпуте.
    """
    pw = _MG_PANEL_W
    vs = _MG_VIDEO_SHIFT
    sd = 0.4   # длительность въезда/выезда (s)

    bake_dir = temp_dir / "mg_baked"
    bake_dir.mkdir(parents=True, exist_ok=True)
    ok = 0
    n  = len(seg_ids)

    zone_start = covered[0][0] if covered else 0.0

    for i, seg_id in enumerate(seg_ids):
        src = lib_dir / f"clip_{seg_id:03d}.mp4"
        if not src.exists():
            continue

        baked    = bake_dir / f"baked_{seg_id:03d}.mp4"
        seg_s, seg_e, _ = covered[i]
        clip_dur = seg_e - seg_s
        anim_seek = seg_s - zone_start   # смещение в MG-клипе для этого сегмента

        is_first = (i == 0)
        is_last  = (i == n - 1)

        if not baked.exists():
            # ── строим выражения x для панели и видео ─────────────────────
            if is_first and is_last:
                # Один сегмент: въезд + удержание + выезд
                hold_end = max(sd, clip_dur - sd)
                anim_x = (
                    f"if(lt(t,{sd:.3f}),-{pw}+{pw}*t/{sd:.3f},"
                    f"if(lt(t,{hold_end:.3f}),0,"
                    f"-{pw}*(t-{hold_end:.3f})/{sd:.3f}))"
                )
                main_x = (
                    f"if(lt(t,{sd:.3f}),{vs}*t/{sd:.3f},"
                    f"if(lt(t,{hold_end:.3f}),{vs},"
                    f"{vs}*(1-(t-{hold_end:.3f})/{sd:.3f})))"
                )
            elif is_first:
                # Въезд в начале, удерживается до конца клипа
                anim_x = f"if(lt(t,{sd:.3f}),-{pw}+{pw}*t/{sd:.3f},0)"
                main_x = f"if(lt(t,{sd:.3f}),{vs}*t/{sd:.3f},{vs})"
            elif is_last:
                # Уже въехала, выезд в конце
                hold_end = max(0.0, clip_dur - sd)
                anim_x = f"if(lt(t,{hold_end:.3f}),0,-{pw}*(t-{hold_end:.3f})/{sd:.3f})"
                main_x = f"if(lt(t,{hold_end:.3f}),{vs},{vs}*(1-(t-{hold_end:.3f})/{sd:.3f}))"
            else:
                # Средний клип: панель статична
                anim_x = "0"
                main_x = str(vs)

            filter_v = (
                f"[1:v]crop={pw}:1080:0:0,fps=25,setpts=PTS-STARTPTS[anim];"
                f"[0:v]fps=25,setpts=PTS-STARTPTS[main];"
                f"color=black:s=1920x1080:r=25[bg];"
                f"[bg][main]overlay=x='{main_x}':y=0[base];"
                f"[base][anim]overlay=x='{anim_x}':y=0[vout]"
            )
            cmd = [
                "ffmpeg", "-y",
                "-i", str(src),
                "-ss", f"{anim_seek:.3f}", "-i", str(anim_mp4),
                "-filter_complex", filter_v,
                "-map", "[vout]", "-an",
                "-c:v", "libx264", "-preset", "fast", "-crf", "17",
                "-t", f"{clip_dur:.3f}",
                "-loglevel", "warning",
                str(baked),
            ]
            r = subprocess.run(cmd, capture_output=True)
            if r.returncode != 0 or not baked.exists():
                err = r.stderr.decode(errors="replace")[-200:]
                log(f"  [MG] Panel bake seg {seg_id:03d} FAILED: {err}")
                continue

        # Relink
        dst = lib_dir / f"clip_{seg_id:03d}.mp4"
        try:
            if dst.is_symlink() or dst.exists():
                dst.unlink()
            dst.symlink_to(baked.resolve())
        except OSError:
            shutil.copy2(baked, dst)
        ok += 1

    return ok


# ─────────────────────────────────────────────────────────────────────────────
# TRANSCRIPTION (inline Whisper, runs when result.json is missing)
# ─────────────────────────────────────────────────────────────────────────────

def _transcribe_for_gosha(channel_id: str, session: str, audio_path: Path) -> Path:
    """
    Запустить Whisper-транскрипцию прямо внутри Гоши.
    Вызывается автоматически если result.json не найден.
    Возвращает путь к созданному result.json.
    """
    # transcriber.py делает "from agents.utils.progress import ProgressBar" —
    # для этого нужен корень проекта в sys.path
    if str(BASE_DIR) not in sys.path:
        sys.path.insert(0, str(BASE_DIR))
    try:
        import transcriber as _tr
    except ImportError as _e:
        log(f"✗ Не удалось импортировать transcriber: {_e}")
        sys.exit(1)

    log("=" * 60)
    log("ТРАНСКРИПЦИЯ (Whisper)")
    log("=" * 60)

    # Предобработка: убрать длинные паузы до транскрипции
    log("Предобработка аудио...")
    audio_path = _tr.preprocess_voice(audio_path)

    # Определяем устройство и загружаем модель
    device, gpu_name, model_size = _tr.detect_device()
    log(f"Whisper: {model_size} на {'GPU: ' + gpu_name if device == 'cuda' else 'CPU'}")
    model = _tr.get_whisper_model()

    # Транскрипция
    whisper_segs, duration, all_words = _tr.transcribe(
        model, audio_path, use_gpu=(device == "cuda"), channel_id=channel_id,
    )
    log(f"Whisper: {len(whisper_segs)} сегментов, {len(all_words)} слов, {duration:.1f}s")

    # Нарезка на блоки (random: 2–4s → 5s)
    segments = _tr.build_segments(whisper_segs, "random", channel_id=channel_id)
    log(f"Нарезка: {len(segments)} блоков")

    # FFmpeg верификация и хвосты
    segments, ffmpeg_meta = _tr.verify_with_ffmpeg(audio_path, segments)

    # Сохранение result.json + субтитры
    json_path = _tr.save(session, segments, ffmpeg_meta, words=all_words, channel_id=channel_id)
    _tr.save_srt(session, segments, channel_id=channel_id)
    _tr.save_vtt(session, segments, channel_id=channel_id)

    log(f"Транскрипция готова → {json_path.name}")
    log("=" * 60)
    return json_path


# ─────────────────────────────────────────────────────────────────────────────
# MAIN
# ─────────────────────────────────────────────────────────────────────────────

def main() -> None:
    parser = argparse.ArgumentParser(description="Assembler v2 — чистый монтаж видео")
    parser.add_argument("--channel",  help="Канал: de | fr | es | channel_001_cosmos_de | ...")
    parser.add_argument("--session",  help="Имя сессии (по умолчанию: последняя)")
    parser.add_argument("--text-only", action="store_true", help="Только Gemini text embeddings — без pHash sliding window")
    parser.add_argument("--no-subs",  action="store_true", help="Без субтитров")
    parser.add_argument("--no-music", action="store_true", help="Без фоновой музыки")
    parser.add_argument("--skip-intro-clips", action="store_true",
                        help="Не генерировать intro_clips/ (только main-клипы)")
    parser.add_argument("--skip-mg", action="store_true",
                        help="Пропустить планирование и рендер Remotion motion graphics")
    parser.add_argument("--skip-thumbnail", action="store_true",
                        help="Пропустить генерацию превью (DE/FR cosmos)")
    parser.add_argument("--skip-meta", action="store_true",
                        help="Пропустить мета-агент (ES religion: titles + tags + thumbnail)")
    parser.add_argument("--intro-duration", type=float, default=90.0,
                        help="Длительность интро в секундах (default: 90)")
    args = parser.parse_args()

    t_start = time.time()
    log("=" * 60)
    log("GOSHA v2 — МОНТАЖ ВИДЕО")
    log("=" * 60)

    # ── 1. INIT ───────────────────────────────────────────────────────────────
    channel_id, style = resolve_channel(args)
    session            = resolve_session(channel_id, args.session)
    ensure_session_dirs(channel_id, session)

    # Автолог в channel_root/_logs/gosha_SESSION.log
    global _tee
    try:
        from paths import get_channel_dir
        _log_path = get_channel_dir(channel_id) / "_logs" / f"gosha_{session}.log"
        _tee = _Tee(sys.stdout, _log_path)
        sys.stdout = _tee
    except Exception:
        pass

    intro_enabled = style.get("intro_transition", "none") != "none"
    zone_a_end_s  = float(style.get("zone_a_end_s",    300.0))
    zone_b_count  = int(style.get("zone_b_transitions", 35))
    trans_name    = style.get("clip_transition",          "crossfade")
    trans_dur     = float(style.get("clip_transition_duration", 0.5))
    log(f"Канал:   {channel_id}")
    log(f"Сессия:  {session}")
    log(f"Стиль:   trans={trans_name}({trans_dur}s)  zone_a={zone_a_end_s}s  zone_b={zone_b_count}")

    output_dir = get_output_dir(channel_id, session)
    output_dir.mkdir(parents=True, exist_ok=True)

    # Temp на D: SSD — рендер быстрый, удаляется после сборки
    temp_dir = TEMP_ROOT / channel_id / session / "temp"
    _d_free_gb = shutil.disk_usage("D:/").free // 1024 ** 3
    if _d_free_gb < 15:
        log(f"  ⚠️ D: мало места: {_d_free_gb}GB свободно — освободите диск!")
    else:
        log(f"  D: свободно: {_d_free_gb}GB")
    temp_dir.mkdir(parents=True, exist_ok=True)
    set_temp_dir(temp_dir)   # все temp-файлы transitions.py → D: SSD

    # Загрузка данных
    voiceover = get_audio_path(channel_id, session)
    if not voiceover:
        log("✗ Озвучка не найдена"); sys.exit(1)

    result_json = get_result_json(channel_id, session)
    if not result_json.exists():
        log("result.json не найден — запуск транскрипции Whisper...")
        result_json = _transcribe_for_gosha(channel_id, session, voiceover)

    try:
        segments, total_dur = load_segments(result_json)
    except FileNotFoundError as e:
        log(f"✗ {e}"); sys.exit(1)
    log(f"Сегментов: {len(segments)}  |  Длительность: {total_dur:.1f}s")

    # Реальная длина голоса — источник истины. result.json может ошибаться
    # (Whisper иногда выдаёт total_duration с паузами в конце или округлением).
    voice_actual_dur = get_duration(voiceover)
    if abs(voice_actual_dur - total_dur) > 1.0:
        log(f"⚠️  result.json: {total_dur:.1f}s | голос: {voice_actual_dur:.1f}s → используем голос")
        total_dur = voice_actual_dur
    log(f"Озвучка: {voiceover.name}  ({total_dur:.1f}s)")

    intro_path = find_intro(channel_id, session) if intro_enabled else None
    intro_dur  = 0.0
    if intro_path:
        intro_dur = get_duration(intro_path)
        log(f"Интро:   {intro_path.name} ({intro_dur:.1f}s)")
    elif intro_enabled:
        log("Интро:   не найдено")

    # ── 1b. MOTION GRAPHICS PLAN (Gemini) ─────────────────────────────────────
    # Gemini анализирует транскрипт → план зон → Remotion стартует в фон
    # параллельно с clip selection (~45s overlap).
    _mg_zones: list[dict] | None = None
    _fut_mg_render: "concurrent.futures.Future | None" = None
    _mg_render_executor: "concurrent.futures.ThreadPoolExecutor | None" = None

    _MG_CHANNELS = {"channel_001_cosmos_de", "channel_002_cosmos_fr"}

    if not args.skip_mg and channel_id in _MG_CHANNELS:
        _mg_zones = _plan_motion_graphics(channel_id, session, segments)
        if _mg_zones:
            _mg_dir = temp_dir / "motion_graphics"
            _mg_render_executor = concurrent.futures.ThreadPoolExecutor(max_workers=1)
            _fut_mg_render = _mg_render_executor.submit(_render_mg_zones, _mg_zones, _mg_dir)
            log("MG: Remotion рендер запущен в фон...")

    # ── 1c. THUMBNAIL (фоновый поллер) ───────────────────────────────────────
    # Запускается параллельно с clip selection и рендером.
    # Если thumbnail_text.txt пустой — ждёт каждые 30с пока пользователь не заполнит.
    # Только для cosmos DE/FR.
    _THUMBNAIL_CHANNELS = {"channel_001_cosmos_de", "channel_002_cosmos_fr"}
    _fut_thumb: "concurrent.futures.Future | None" = None
    _thumb_executor: "concurrent.futures.ThreadPoolExecutor | None" = None

    if not getattr(args, "skip_thumbnail", False) and channel_id in _THUMBNAIL_CHANNELS:
        _thumb_txt = get_session_dir(channel_id, session) / "thumbnail_text.txt"
        if not _thumb_txt.exists():
            _thumb_txt.write_text(
                "# Строка 1: маленький текст сверху (напр. SOLAR-DATEN)\n"
                "# Строка 2: большой жирный текст (напр. VERNICHTET)\n",
                encoding="utf-8",
            )
            log(f"THUMBNAIL: 📝 создан {_thumb_txt} — заполни текст, агент проверяет каждые 30с")

        _thumb_agent = BASE_DIR / "agents" / "thumbnail" / "thumbnail_agent.py"

        def _poll_and_run_thumbnail(
            _channel=channel_id, _session=session,
            _txt=_thumb_txt, _agent=_thumb_agent,
        ):
            _POLL_INTERVAL = 30
            _TIMEOUT       = 1800  # 30 мин — достаточно для любого рендера
            deadline = time.time() + _TIMEOUT
            announced = False

            while time.time() < deadline:
                lines = [
                    l.strip() for l in _txt.read_text(encoding="utf-8").splitlines()
                    if l.strip() and not l.strip().startswith("#")
                ]
                text = " ".join(lines) if lines else None

                if text:
                    log(f"THUMBNAIL: текст найден ('{text[:50]}') — запускаю агент...")
                    _env = os.environ.copy()
                    _env["PYTHONPATH"] = str(BASE_DIR) + os.pathsep + _env.get("PYTHONPATH", "")
                    r = subprocess.run(
                        [sys.executable, str(_agent),
                         "--channel", _channel, "--session", _session, "--text", text],
                        capture_output=True, text=True,
                        encoding="utf-8", errors="replace",
                        cwd=str(BASE_DIR), env=_env,
                    )
                    return r.returncode == 0, r.stderr

                if not announced:
                    log(f"THUMBNAIL: жду текст в {_txt.name} (каждые {_POLL_INTERVAL}с, до 30 мин)...")
                    announced = True

                time.sleep(_POLL_INTERVAL)

            return False, "timeout — текст не был заполнен за 30 минут"

        _thumb_executor = concurrent.futures.ThreadPoolExecutor(max_workers=1)
        _fut_thumb = _thumb_executor.submit(_poll_and_run_thumbnail)
        log("THUMBNAIL: наблюдатель запущен в фоне")

    # ── 1d. META AGENT (ES religion only) ────────────────────────────────────
    # Titles + descriptions + tags + thumbnail — всё параллельно с рендером.
    _META_CHANNELS = {"channel_003_religion_es"}
    _fut_meta: "concurrent.futures.Future | None" = None
    _meta_executor: "concurrent.futures.ThreadPoolExecutor | None" = None

    if not getattr(args, "skip_meta", False) and channel_id in _META_CHANNELS:
        _meta_agent = BASE_DIR / "agents" / "meta_agent" / "meta_generator.py"
        def _run_meta(_channel=channel_id, _session=session, _agent=_meta_agent):
            _env = os.environ.copy()
            _env["PYTHONPATH"] = str(BASE_DIR) + os.pathsep + _env.get("PYTHONPATH", "")
            r = subprocess.run(
                [sys.executable, str(_agent),
                 "--channel", _channel, "--session", _session],
                capture_output=True, text=True,
                encoding="utf-8", errors="replace",
                cwd=str(BASE_DIR), env=_env,
            )
            return r.returncode == 0, r.stderr
        _meta_executor = concurrent.futures.ThreadPoolExecutor(max_workers=1)
        _fut_meta = _meta_executor.submit(_run_meta)
        log("META: агент запущен в фоне (titles + tags + thumbnail)...")

    # ── 2. CLIPS ──────────────────────────────────────────────────────────────
    t_clips = time.time()
    lib_clips_dir = get_clips_dir(channel_id)
    lib_dir, selection_result = select_and_link_clips(
        channel_id     = channel_id,
        session        = session,
        segments       = segments,
        lib_clips_dir  = lib_clips_dir,
        temp_dir       = temp_dir,
        intro_duration = args.intro_duration if intro_enabled else 0.0,
        skip_intro_clips = args.skip_intro_clips,
        text_only = args.text_only,
    )
    log(f"[⏱] Clips: {time.time()-t_clips:.1f}s")

    # Если нет intro.mp4 но интро требуется → стоп
    if intro_enabled and not intro_path:
        intro_clips_dir = get_intro_clips_dir(channel_id, session)
        session_dir     = get_session_dir(channel_id, session)
        log(f"\nИнтро-клипы готовы: {intro_clips_dir}")
        log(f"Смонтируй intro.mp4 и положи в: {session_dir}")
        log(f"Затем запусти снова: py agents/assembler/gosha_rubchinskiy.py --channel {args.channel or channel_id}")
        sys.exit(0)

    # ── 2b. COMMIT clip history сразу после выбора клипов ────────────────────
    # Коммитим немедленно чтобы следующее видео уже знало какие клипы заняты.
    # Re-render этой же сессии пропустит коммит (history_committed=True).
    do_commit_history(selection_result, channel_id, session, Path("_pending"), force=True)

    # ── 2c. MOTION GRAPHICS INJECT ────────────────────────────────────────────
    # Ждём завершения Remotion-рендера (он стартовал ДО clip selection)
    # и встраиваем MG-клипы прямо в temp/lib_clips/ — без дополнительного рендера.
    _mg_sfx_events:       list[dict]               = []
    _mg_fullscreen_zones: list[tuple[float, float]] = []
    _mg_intra_seg_ids:    set[int]                  = set()

    if _fut_mg_render is not None:
        log("MG: ожидание Remotion...")
        t_mg_wait = time.time()
        try:
            _mg_zones_rendered = _fut_mg_render.result()
        except Exception as _mg_err:
            log(f"[MG] Рендер упал: {_mg_err}")
            _mg_zones_rendered = []
        finally:
            if _mg_render_executor:
                _mg_render_executor.shutdown(wait=False)
        log(f"[⏱] MG render wait: {time.time()-t_mg_wait:.1f}s")

        n_mg, _mg_sfx_events, _mg_intra_seg_ids = _inject_mg_into_lib_clips(_mg_zones_rendered, lib_dir, temp_dir, segments)
        if n_mg:
            log(f"MG: ✅ {n_mg} зон внедрено в видеоряд")
        # Собираем fullscreen-зоны для подавления субтитров
        _mg_fullscreen_zones = [
            (float(z["start_time"]), float(z["end_time"]))
            for z in _mg_zones_rendered
            if z.get("composition") not in _MG_PANEL_COMPS
            and z.get("rendered_path") and Path(z.get("rendered_path", "")).exists()
        ]

    # ── 3. TIMELINE ───────────────────────────────────────────────────────────
    t_timeline = time.time()
    log("\n--- TIMELINE ---")

    # Фильтруем только сегменты у которых есть клип в lib_dir.
    # Сегменты интро (1-31) не имеют main-клипов — они покрыты intro.mp4.
    segments_main = [
        seg for seg in segments
        if (lib_dir / f"clip_{int(seg['id']):03d}.mp4").exists()
    ]
    log(f"Сегментов всего: {len(segments)}  |  с клипами (main): {len(segments_main)}")

    if not segments_main:
        log("✗ Ни одного клипа не найдено в lib_dir"); sys.exit(1)

    # Целевая длительность видеоряда = вся озвучка минус интро
    clips_total_dur = total_dur - intro_dur
    log(f"Длительность клипов: {total_dur:.1f}s − {intro_dur:.1f}s = {clips_total_dur:.1f}s")

    # Тайминги — граница зон авто-определяется из Whisper
    timings = compute_timings(segments_main)

    # Safety: Zone B зафиксированы на 5.0s — масштабируем только Zone A
    zone_a_timings = [t for t in timings if t["zone"] == "A"]
    zone_b_sum     = sum(t["duration"] for t in timings if t["zone"] == "B")  # ровно N × 5.0s
    zone_a_sum     = sum(t["duration"] for t in zone_a_timings)
    zone_a_target  = clips_total_dur - zone_b_sum
    if zone_a_sum > 0 and abs(zone_a_sum - zone_a_target) > 0.5:
        scale = zone_a_target / zone_a_sum
        for t in zone_a_timings:
            t["duration"] *= scale
        log(f"Zone A тайминги скалированы: {zone_a_sum:.1f}s -> {zone_a_target:.1f}s (x{scale:.4f})")

    # Пересчитываем start-позиции после скейлинга Zone A
    _vt = 0.0
    for t in timings:
        t["start"] = _vt
        _vt += t["duration"]

    zone_a_segs = sum(1 for t in timings if t["zone"] == "A")
    zone_b_segs = sum(1 for t in timings if t["zone"] == "B")
    detected_boundary = timings[zone_a_segs]["start"] if zone_b_segs > 0 else clips_total_dur
    log(f"Зона A: {zone_a_segs} сег (0–{detected_boundary:.0f}s)  |  Зона B: {zone_b_segs} сег")

    # План переходов вычисляем ДО trim, чтобы знать какие клипы нуждаются в handle
    # (Зона A = каждый стык, Зона B = N рандомных)
    trans_plan = build_transition_plan(
        n_clips         = len(timings),
        timings         = timings,
        zone_a_end_s    = detected_boundary,
        zone_b_count    = zone_b_count,
        clip_trans_name = trans_name,
        clip_trans_dur  = trans_dur,
        channel_id      = channel_id,
    )

    # Стыки внутри MG-зон → cut: Remotion-анимация непрерывна, crossfade её разрывает.
    # Только вход (первый сег) и выход (после последнего) зоны сохраняют переход.
    if _mg_intra_seg_ids:
        mg_cut_count = 0
        for i, timing in enumerate(timings[:-1]):
            if timings[i + 1]["seg_id"] in _mg_intra_seg_ids:
                trans_plan[i] = {"type": "cut", "dur": 0.0}
                mg_cut_count += 1
        if mg_cut_count:
            log(f"MG: {mg_cut_count} внутризонных стыков → cut (непрерывная анимация)")

    # Корректируем SFX-тайминги: adelay должен совпадать с видео-позицией анимации,
    # а не с Whisper-таймстампом (Zone A скейлинг и Zone B 5.0s сдвигают видео vs аудио).
    if _mg_sfx_events:
        _sid_to_vstart = {t["seg_id"]: t["start"] for t in timings}
        _sfx_corrections = []
        for ev in _mg_sfx_events:
            sid = ev.get("seg_id")
            if sid is not None and sid in _sid_to_vstart:
                old_t = ev["time_s"]
                ev["time_s"] = _sid_to_vstart[sid]
                _sfx_corrections.append(f"{old_t:.1f}→{ev['time_s']:.1f}")
        if _sfx_corrections:
            log(f"MG: SFX тайминги скорректированы по видео-позициям: {', '.join(_sfx_corrections)}")

    # Trim клипов (stream copy, параллельно)
    # tpad генерирует freeze-frame handles синтетически — физический handle не нужен.
    trimmed_clips = trim_clips(segments_main, timings, lib_dir, temp_dir)
    if not trimmed_clips:
        log("✗ Нет обрезанных клипов"); sys.exit(1)

    log(f"[⏱] Timeline+Trim: {time.time()-t_timeline:.1f}s")

    # ── 4. RENDER ─────────────────────────────────────────────────────────────
    t_render = time.time()
    log("\n--- RENDER ---")

    videotrack_path  = temp_dir / "videotrack.mp4"
    timeline_path    = temp_dir / "timeline.json"
    mixed_audio_path = temp_dir / "audio.m4a"  # M4A = MP4+AAC: точный заголовок длины (не raw .aac)
    ass_path         = get_ass_path(channel_id, session)
    final_output     = get_final_video(channel_id, session)

    _au_ok, _au_dur = ffprobe_check(mixed_audio_path, total_dur, "audio (cached)") \
        if mixed_audio_path.exists() else (False, 0.0)

    # Определяем путь рендера ДО запуска параллельных задач
    # GPU single-pass: timeline.json + GPU compositor (нет videotrack encode)
    # CPU fallback:    videotrack.mp4 + final_render (FFmpeg)
    _use_gpu = (
        _GPU_COMPOSITOR_AVAILABLE
        and not args.no_subs
    )

    # Параллельно: аудио + субтитры (видеоряд — только для CPU fallback)
    _vt_ok = False
    with concurrent.futures.ThreadPoolExecutor(max_workers=3) as ex:
        fut_audio = None if _au_ok else ex.submit(
            build_audio_track,
            voiceover, intro_path, intro_dur, total_dur,
            style, mixed_audio_path, args.no_music,
        )
        fut_subs = ex.submit(
            generate_subtitles,
            result_json, ass_path, style, intro_dur, args.no_subs, trans_dur,
            _mg_fullscreen_zones or None,
        )
        # CPU fallback: encode videotrack параллельно с аудио/субтитрами
        fut_video = None
        if not _use_gpu:
            _vt_ok, _vt_dur = ffprobe_check(videotrack_path, clips_total_dur, "videotrack (cached)") \
                if videotrack_path.exists() else (False, 0.0)
            if not _vt_ok:
                fut_video = ex.submit(
                    render_videotrack,
                    trimmed_clips, trans_plan, trans_name, trans_dur, videotrack_path,
                    clips_total_dur,
                )

        if _au_ok:
            log(f"  Аудио: resume ({_au_dur:.1f}s уже готов)")
            audio_ok = True
        else:
            audio_ok = fut_audio.result()

        # Подмешиваем MG SFX-аудио поверх готового трека
        if audio_ok and _mg_sfx_events and not _au_ok:
            _mix_mg_audio_into_track(mixed_audio_path, _mg_sfx_events, temp_dir)

        ass_ok, drawtext_filter = fut_subs.result()

        if fut_video is not None:
            video_ok = fut_video.result()
        else:
            video_ok = _vt_ok

    log(f"Аудио: {'OK' if audio_ok else 'FAIL'}  |  Субтитры: {'OK' if ass_ok else 'FAIL'}")
    log(f"[⏱] Parallel prep: {time.time()-t_render:.1f}s")

    # ffprobe аудио
    if audio_ok and not _au_ok:
        au_valid, _ = ffprobe_check(mixed_audio_path, total_dur, "audio")
        if not au_valid:
            log("✗ Аудио truncated — проверьте место на диске"); sys.exit(1)

    # Уточняем _use_gpu с учётом результатов субтитров
    _use_gpu = (
        _use_gpu
        and ass_ok
        and ass_path.exists()
        and not drawtext_filter   # scale_pop использует drawtext — fallback CPU
    )

    render_ok = False

    if _use_gpu:
        # ── Single-pass GPU рендер ────────────────────────────────────────────
        log("Финальный рендер → GPU compositor (single-pass, без videotrack encode)")

        color_grade_name = style.get("color_grade", "warm_cinematic")
        intro_trans_dur  = float(style.get("clip_transition_duration", 1.0))
        sub_style        = style.get("subtitle_style", "default")
        _font_size       = SUBTITLE_FONT_SIZE + style.get("subtitle_size_offset", 0)
        _sub_font        = style.get("subtitle_font", "")
        _FONT_MAP = {
            "Organetto":            ORGANETTO_FONT_PATH,
            "Organetto Bold":       ORGANETTO_FONT_PATH,
            "Montserrat Bold":      r"C:\Users\Serafim\AppData\Local\Microsoft\Windows\Fonts\Montserrat-Bold.ttf",
            "Montserrat ExtraBold": r"C:\Users\Serafim\AppData\Local\Microsoft\Windows\Fonts\MONTSERRAT-EXTRABOLD.TTF",
            "Arial Black":          r"C:\Windows\Fonts\ariblk.ttf",
        }
        _font_path = _FONT_MAP.get(_sub_font) or ORGANETTO_FONT_PATH
        _sub_anim  = style.get("subtitle_anim", "slide_up")

        # Строим timeline.json (мгновенно — просто JSON с путями)
        build_timeline_json(
            trimmed_clips   = trimmed_clips,
            trans_plan      = trans_plan,
            intro_path      = intro_path,
            intro_trans_dur = intro_trans_dur,
            fps             = 25.0,
            output_path     = timeline_path,
        )

        # ── CTA timestamps для GPU compositor ────────────────────────────────
        _cta_mov_path = style.get("cta_mov")
        _cta_ts_for_gpu: list[float] = []
        if _cta_mov_path and Path(_cta_mov_path).exists():
            from apply_cta import compute_timestamps, get_duration as _cta_dur
            _cta_dur_s  = _cta_dur(Path(_cta_mov_path))
            _cta_min_g  = float(style.get("cta_min_gap_s", 90))
            _cta_max_g  = float(style.get("cta_max_gap_s", 120))
            _cta_ts_for_gpu = compute_timestamps(
                total_dur, intro_dur, _cta_dur_s, _cta_min_g, _cta_max_g
            )
            if _cta_ts_for_gpu:
                log(f"CTA: {len(_cta_ts_for_gpu)} плашек → {[f'{t:.0f}s' for t in _cta_ts_for_gpu]}")

        render_ok = _gpu_composite(
            timeline         = timeline_path,
            output           = final_output,
            audio            = mixed_audio_path,
            ass_path         = ass_path,
            color_grade_name = color_grade_name,
            fps              = 25.0,
            intro_trans_dur  = intro_trans_dur,
            font_size        = _font_size,
            fade_ms          = SUBTITLE_FADE_IN_MS,
            font_path        = _font_path,
            subtitle_style   = sub_style,
            subtitle_anim    = _sub_anim,
            cta_path         = Path(_cta_mov_path) if _cta_mov_path and _cta_ts_for_gpu else None,
            cta_timestamps   = _cta_ts_for_gpu or None,
            cta_intro_dur    = intro_dur,
        )
        if not render_ok:
            log("  ⚠️ GPU compositor не удался — fallback на CPU render")
            _use_gpu = False

    if not _use_gpu:
        # ── CPU fallback: encode videotrack → FFmpeg final_render ─────────────
        log("Финальный рендер → CPU (FFmpeg)")
        if not _vt_ok and fut_video is None:
            # Videotrack не был запущен параллельно — запускаем сейчас
            _vt_ok2, _vt_dur2 = ffprobe_check(videotrack_path, clips_total_dur, "videotrack (cached)") \
                if videotrack_path.exists() else (False, 0.0)
            if _vt_ok2:
                video_ok = True
                log(f"  Видеоряд: resume ({_vt_dur2:.1f}s уже готов)")
            else:
                video_ok = render_videotrack(
                    trimmed_clips, trans_plan, trans_name, trans_dur, videotrack_path,
                    clips_total_dur,
                )

        # ffprobe валидация видеоряда
        if video_ok:
            vt_valid, _ = ffprobe_check(videotrack_path, clips_total_dur, "videotrack")
            if not vt_valid:
                log("  ⚠️ Videotrack truncated — ретрай с libx264")
                from transitions import _set_encoder_x264
                _set_encoder_x264()
                video_ok = render_videotrack(
                    trimmed_clips, trans_plan, trans_name, trans_dur, videotrack_path,
                    clips_total_dur,
                )

        if not video_ok and not intro_path:
            log("✗ Нет ни видеоряда, ни интро"); sys.exit(1)

        render_ok = final_render(
            intro_path      = intro_path,
            videotrack      = videotrack_path,
            audio_path      = mixed_audio_path,
            ass_path        = ass_path,
            ass_ok          = ass_ok,
            drawtext_filter = drawtext_filter,
            style           = style,
            intro_dur       = intro_dur,
            output          = final_output,
        )

    if not render_ok:
        log("✗ Финальный рендер не удался"); sys.exit(1)

    # ffprobe валидация финального видео
    final_valid, final_actual = ffprobe_check(final_output, total_dur, "final video", tolerance=5.0)
    if not final_valid:
        if _use_gpu:
            # GPU single-pass не имеет videotrack.mp4 — ретрай через CPU невозможен
            log(f"  ⚠️ GPU output truncated ({final_actual:.1f}s vs {total_dur:.1f}s) — "
                f"проверьте _iter_timeline_frames")
        else:
            log(f"  ⚠️ Final truncated ({final_actual:.1f}s vs {total_dur:.1f}s) — ретрай с libx264")
            from transitions import _set_encoder_x264
            _set_encoder_x264()
            final_output.unlink(missing_ok=True)
            render_ok = final_render(
                intro_path      = intro_path,
                videotrack      = videotrack_path,
                audio_path      = mixed_audio_path,
                ass_path        = ass_path,
                ass_ok          = ass_ok,
                drawtext_filter = drawtext_filter,
                style           = style,
                intro_dur       = intro_dur,
                output          = final_output,
            )
            ffprobe_check(final_output, total_dur, "final video (x264)", tolerance=5.0)

    log(f"[⏱] Final render: {time.time()-t_render:.1f}s total")
    audit_final_audio(final_output, voice_path=voiceover)

    # ── 5. COMMIT & CLEANUP ───────────────────────────────────────────────────
    do_commit_history(selection_result, channel_id, session, final_output)

    # Ждём фоновые агенты (обычно уже готовы к этому моменту)
    if _fut_thumb is not None:
        _thumb_final = get_session_dir(channel_id, session) / "thumbnail" / "thumbnail_final.png"
        if _thumb_final.exists():
            log(f"THUMBNAIL: ✅ уже готово → {_thumb_final.name}")
        else:
            log("THUMBNAIL: ожидание завершения...")
            t_thumb_wait = time.time()
            try:
                _ok, _err = _fut_thumb.result(timeout=600)
                waited = time.time() - t_thumb_wait
                if _ok:
                    log(f"THUMBNAIL: ✅ готово (+{waited:.0f}s) → {_thumb_final.name}")
                else:
                    log(f"THUMBNAIL: ⚠️ не удалось{(': ' + _err[:200]) if _err else ''}")
            except Exception as _te:
                log(f"THUMBNAIL: ⚠️ {_te}")
            finally:
                if _thumb_executor:
                    _thumb_executor.shutdown(wait=False)

    if _fut_meta is not None:
        _meta_final = get_session_dir(channel_id, session) / "meta" / "final.txt"
        if _meta_final.exists():
            log(f"META: ✅ уже готово → {_meta_final.name}")
        else:
            log("META: ожидание завершения...")
            t_meta_wait = time.time()
            try:
                _ok, _err = _fut_meta.result(timeout=900)
                waited = time.time() - t_meta_wait
                if _ok:
                    log(f"META: ✅ готово (+{waited:.0f}s) → {_meta_final.name}")
                else:
                    log(f"META: ⚠️ не удалось{(': ' + _err[:200]) if _err else ''}")
            except Exception as _me:
                log(f"META: ⚠️ {_me}")
            finally:
                if _meta_executor:
                    _meta_executor.shutdown(wait=False)

    shutil.rmtree(temp_dir, ignore_errors=True)
    log("Временные файлы удалены")

    elapsed = time.time() - t_start
    m, s = divmod(int(elapsed), 60)
    size_mb = final_output.stat().st_size / 1024 / 1024 if final_output.exists() else 0

    log("")
    log("=" * 60)
    log(f"ГОТОВО!  Время: {m}м {s}с  |  Размер: {size_mb:.1f} МБ")
    log(f"Файл: {final_output}")
    log("=" * 60)


if __name__ == "__main__":
    main()
