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
    BASE_DIR / "bot",
]:
    if str(_p) not in sys.path:
        sys.path.insert(0, str(_p))

from paths import (
    get_channel_dir, get_session_dir, get_last_session,
    get_input_dir, get_transcripts_dir, get_output_dir,
    get_intro_clips_dir, get_intro_path, get_audio_path,
    get_result_json, get_ass_path, get_final_video,
    get_clips_dir, ensure_session_dirs,
)
from style_engine import get_channel_style, get_intro_transition_fn
from transitions import concat_all_with_transitions, _final_concat, intro_to_main_transition
from audio_mixer import build_final_audio
from ass_generator import generate_ass, generate_karaoke_ass, generate_scripture_ass
from subtitle_burner import burn_ass, generate_drawtext_filter
from sfx_mixer import (
    inject_sfx, compute_transition_times, build_haiku_sfx_events,
    _build_sfx_events, _enforce_sfx_sync, _prune_sfx_by_ratio,
    _sfx_gain,
)

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
    "de": "channel_001_cosmos_de",
    "fr": "channel_002_cosmos_fr",
    "es": "channel_003_religion_es",
}

# ─────────────────────────────────────────────────────────────────────────────
# УТИЛИТЫ
# ─────────────────────────────────────────────────────────────────────────────

def log(msg: str) -> None:
    print(f"[ASSEMBLER] {msg}", flush=True)


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
        result = {
            "intro_clips":    [tuple(x) for x in cs.get("intro_clips", [])],
            "main_clips":     [tuple(x) for x in cs.get("main_clips",  [])],
            "intro_duration": sum(d for _, _, d in [tuple(x) for x in cs.get("intro_clips", [])]),
            "main_duration":  sum(d for _, _, d in [tuple(x) for x in cs.get("main_clips",  [])]),
            "_clips_used":    [],
            "_history":       None,
            "_channel_id":    channel_id,
            "_session":       session,
        }
    else:
        result = select_clips_for_video(
            session=session,
            channel_id=channel_id,
            segments=segments,
            max_repeats_in_video=10,
            max_from_prev=20,
            intro_duration=intro_duration,
        )

    main_clips   = result.get("main_clips",   [])
    intro_clips  = result.get("intro_clips",  [])

    log(f"Подобрано: main={len(main_clips)}, intro={len(intro_clips)}")

    # Симлинк main-клипов → temp/lib_clips/
    lib_dir = temp_dir / "lib_clips"
    lib_dir.mkdir(parents=True, exist_ok=True)

    matched = 0
    for seg_id, clip_id, _ in main_clips:
        if clip_id is None:
            continue
        src = lib_clips_dir / f"{clip_id}.mp4"
        if not src.exists():
            log(f"  [{int(seg_id):03d}] файл не найден: {clip_id}.mp4")
            continue
        dst = lib_dir / f"clip_{int(seg_id):03d}.mp4"
        if dst.exists():
            dst.unlink()
        try:
            dst.symlink_to(src)
        except OSError:
            shutil.copy2(src, dst)
        matched += 1

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
        with concurrent.futures.ThreadPoolExecutor(max_workers=8) as ex:
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
            # Zone B без handle → прямой symlink (клип уже 5.0s)
            if dst.exists():
                dst.unlink()
            try:
                dst.symlink_to(src.resolve())
            except OSError:
                shutil.copy2(src, dst)

    if tasks:
        log(f"Trim {len(tasks)} + symlink {max(0, len(all_order) - len(tasks))} клипов (12 потоков)...")
        t0 = time.time()

        def _trim(args: tuple) -> None:
            src, dst, dur, seg_id = args
            cmd = [
                "ffmpeg", "-y", "-i", str(src),
                "-t", f"{dur:.3f}",
                "-c:v", "copy", "-an",
                "-loglevel", "warning",
                str(dst),
            ]
            if not _run_ffmpeg(cmd):
                log(f"  [{seg_id:03d}] trim failed")

        with concurrent.futures.ThreadPoolExecutor(max_workers=12) as ex:
            futs = [ex.submit(_trim, t) for t in tasks]
            for fut in concurrent.futures.as_completed(futs):
                fut.result()

        log(f"Trim: {len(tasks)} клипов за {time.time()-t0:.1f}s")

    ordered = [p for p in all_order if p.exists()]
    log(f"Итого клипов: {len(ordered)} (trim: {len(tasks)}, symlink: {len(ordered) - len(tasks)})")
    return ordered


def build_transition_plan(
    n_clips:          int,
    timings:          list[dict],
    zone_a_end_s:     float,
    zone_b_count:     int,
    clip_trans_name:  str,
    clip_trans_dur:   float,
) -> list[dict]:
    """
    Зона A: переход на каждом стыке.
    Зона B: ровно zone_b_count рандомных стыков получают переход, остальные = cut.

    Возвращает list[{"type", "dur"}] длиной n_clips-1.
    """
    n_boundaries = max(0, n_clips - 1)
    if n_boundaries == 0:
        return []

    # Определяем какие стыки в зоне B
    zone_b_indices = [
        i for i in range(n_boundaries)
        if i < len(timings) and timings[i].get("zone") == "B"
    ]
    zone_b_trans = set(
        random.sample(zone_b_indices, min(zone_b_count, len(zone_b_indices)))
    ) if zone_b_indices else set()

    plan = []
    for i in range(n_boundaries):
        timing = timings[i] if i < len(timings) else {}
        in_zone_b = timing.get("zone") == "B"

        if in_zone_b and i not in zone_b_trans:
            plan.append({"type": "cut", "dur": 0.0})
        else:
            plan.append({"type": clip_trans_name, "dur": clip_trans_dur})

    zone_a_trans = sum(
        1 for i, p in enumerate(plan)
        if p["type"] != "cut" and i < len(timings) and timings[i].get("zone") == "A"
    )
    zone_b_actual = sum(
        1 for i, p in enumerate(plan)
        if p["type"] != "cut" and i < len(timings) and timings[i].get("zone") == "B"
    )
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


def build_audio_track(
    voiceover:    Path,
    intro_path:   Path | None,
    intro_dur:    float,
    total_dur:    float,
    style:        dict,
    output:       Path,
    sfx_events:   list,
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

    build_final_audio(
        voice_path       = voiceover,
        output_path      = output,
        sfx_events       = sfx_events,
        music_start_sec  = music_start,
        music_vol_db     = music_vol if not no_music else -100.0,
        intro_audio_path = str(intro_path) if has_intro else None,
        intro_trim_s     = intro_dur + 2.0 if has_intro else 0.0,
        intro_vol_db     = intro_vol,
        video_duration   = total_dur,
    )
    return output.exists()


_RISER_FILES = [
    Path("assets/sfx/riser/NGTVST - Riser 2.wav"),
    Path("assets/sfx/riser/NGTVST - Riser 3.wav"),
]

def _build_sfx_riser_events(
    trans_plan:    list[dict],
    trans_times:   list[float],
    timings:       list[dict],
    intro_dur:     float,
    xf_dur:        float = 1.0,
    zone_a_chance: float = 0.75,   # 75% переходов в Zone A получают riser
    zone_b_chance: float = 0.25,   # 25% переходов в Zone B получают riser
    vol_db:        float = -30.0,  # тихий, нейтральный фон
) -> list[dict]:
    """
    Умное размещение riser SFX на переходах:
      Zone A: ~75% transition-стыков получают riser
      Zone B: ~25% transition-стыков получают riser (не перегружать)
      Hard cut стыки: никогда

    Riser стартует за riser_dur секунд до стыка, пик = момент смены клипа.
    Объём -30dB — едва слышимый нейтральный подъём.
    """
    import random
    if not trans_times or not trans_plan:
        return []

    riser_files = [f for f in _RISER_FILES if f.exists()]
    if not riser_files:
        return []

    riser_dur = 4.5   # NGTVST Riser 2/3 длятся 4.5s → стартуем за 4.5s до стыка
    vol_lin   = 10 ** (vol_db / 20.0)
    events    = []

    for i, (t_cut, plan) in enumerate(zip(trans_times, trans_plan)):
        if plan.get("type") == "cut":
            continue  # hard cut — без SFX

        # Определяем зону по таймингу
        zone = timings[i].get("zone", "B") if i < len(timings) else "B"
        chance = zone_a_chance if zone == "A" else zone_b_chance

        if random.random() > chance:
            continue  # пропускаем по вероятности

        t_start = max(0.0, t_cut - riser_dur)
        riser_file = random.choice(riser_files)

        events.append({
            "time":   t_start,
            "time_s": t_start,
            "file":   str(riser_file),
            "vol":    vol_lin,
            "gain":   vol_lin,
        })

    log(f"[SFX] Riser события: {len(events)} (Zone A ~{zone_a_chance*100:.0f}%, Zone B ~{zone_b_chance*100:.0f}%)")
    return events


def _build_sfx_events_for_render(
    trans_plan:    list[dict],
    trans_times:   list[float],
    timings:       list[dict],
    intro_dur:     float,
    channel_id:    str,
    sfx_enabled:   bool,
    sfx_vol_scale: float,
    xf_dur:        float = 1.0,
) -> list[dict]:
    """Собрать SFX riser события для переходов."""
    if not sfx_enabled:
        return []
    try:
        events = _build_sfx_riser_events(
            trans_plan  = trans_plan,
            trans_times = trans_times,
            timings     = timings,
            intro_dur   = intro_dur,
            xf_dur      = xf_dur,
        )
        if sfx_vol_scale != 1.0:
            events = [{**e, "vol": e.get("vol", 1.0) * sfx_vol_scale,
                             "gain": e.get("gain", 1.0) * sfx_vol_scale} for e in events]
        return events
    except Exception as e:
        log(f"[SFX] ошибка: {e}")
        return []


def generate_subtitles(
    result_json: Path,
    ass_path:    Path,
    style:       dict,
    intro_dur:   float,
    no_subs:     bool,
) -> tuple[bool, str]:
    """
    Генерация субтитров по стилю канала.
    Возвращает: (ass_ok, drawtext_filter)
      - DE "default" / FR "karaoke" / ES "scripture": ass_ok=True, drawtext_filter=""
    """
    if no_subs or not result_json.exists():
        return False, ""

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
                intro_dur, 3, "&H00FFFFFF", "&H00707070", border,
            )
            return ass_path.exists(), ""
        elif sub_style == "scripture":
            generate_scripture_ass(
                str(result_json), str(ass_path),
                style.get("subtitle_font", "Montserrat Bold"), font_size,
                350, 200, intro_dur,
                style.get("subtitle_max_words", 5),
            )
            return ass_path.exists(), ""
        elif sub_style == "scale_pop":
            # FR "scale_pop" → drawtext, 6-шаговый scale ease-out + тень
            fp = style.get("subtitle_font_path", "C:/Windows/Fonts/arialbd.ttf")
            dt_filter = generate_drawtext_filter(
                result_json_path = result_json,
                font_path        = fp,
                font_size        = font_size,
                fade_out         = SUBTITLE_FADE_OUT_MS / 1000.0,
                intro_duration   = intro_dur,
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
                intro_duration   = intro_dur,
                border_style     = border,
            )
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
# MAIN
# ─────────────────────────────────────────────────────────────────────────────

def main() -> None:
    parser = argparse.ArgumentParser(description="Assembler v2 — чистый монтаж видео")
    parser.add_argument("--channel",  help="Канал: de | fr | es | channel_001_cosmos_de | ...")
    parser.add_argument("--session",  help="Имя сессии (по умолчанию: последняя)")
    parser.add_argument("--no-subs",  action="store_true", help="Без субтитров")
    parser.add_argument("--no-music", action="store_true", help="Без фоновой музыки")
    parser.add_argument("--skip-intro-clips", action="store_true",
                        help="Не генерировать intro_clips/ (только main-клипы)")
    parser.add_argument("--skip-visual-queries", action="store_true",
                        help="Пропустить генерацию visual queries через Claude Haiku")
    parser.add_argument("--intro-duration", type=float, default=90.0,
                        help="Длительность интро в секундах (default: 90)")
    args = parser.parse_args()

    t_start = time.time()
    log("=" * 60)
    log("ASSEMBLER v2 — МОНТАЖ ВИДЕО")
    log("=" * 60)

    # ── 1. INIT ───────────────────────────────────────────────────────────────
    channel_id, style = resolve_channel(args)
    session            = resolve_session(channel_id, args.session)
    ensure_session_dirs(channel_id, session)

    intro_enabled = style.get("intro_transition", "none") != "none"
    zone_a_end_s  = float(style.get("zone_a_end_s",    300.0))
    zone_b_count  = int(style.get("zone_b_transitions", 35))
    trans_name    = style.get("clip_transition",          "crossfade")
    trans_dur     = float(style.get("clip_transition_duration", 0.5))
    sfx_enabled   = style.get("sfx_enabled", False)
    sfx_vol_scale = 10 ** (float(style.get("sfx_vol_scale_db", 0.0)) / 20)

    log(f"Канал:   {channel_id}")
    log(f"Сессия:  {session}")
    log(f"Стиль:   trans={trans_name}({trans_dur}s)  zone_a={zone_a_end_s}s  zone_b={zone_b_count}  sfx={'✓' if sfx_enabled else '✗'}")

    output_dir = get_output_dir(channel_id, session)
    output_dir.mkdir(parents=True, exist_ok=True)
    temp_dir = output_dir / "temp"
    temp_dir.mkdir(parents=True, exist_ok=True)

    # Загрузка данных
    result_json = get_result_json(channel_id, session)
    try:
        segments, total_dur = load_segments(result_json)
    except FileNotFoundError as e:
        log(f"✗ {e}"); sys.exit(1)
    log(f"Сегментов: {len(segments)}  |  Длительность: {total_dur:.1f}s")

    voiceover = get_audio_path(channel_id, session)
    if not voiceover:
        log(f"✗ Озвучка не найдена"); sys.exit(1)

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

    # ── 1b. VISUAL QUERIES (Haiku) ────────────────────────────────────────────
    # Обогащаем сегменты визуальными описаниями для лучшего CLIP-матчинга.
    # Запускаем только если result_visual.json ещё не создан.
    result_visual_json = get_transcripts_dir(channel_id, session) / "result_visual.json"
    if not result_visual_json.exists() and not args.skip_visual_queries:
        try:
            _vq_dir = BASE_DIR / "agents" / "library"
            if str(_vq_dir) not in sys.path:
                sys.path.insert(0, str(_vq_dir))
            from visual_query_generator import generate_visual_queries
            from paths import get_niche
            niche = get_niche(channel_id)
            log("Генерация visual queries через Claude Haiku...")
            result_visual_json = generate_visual_queries(result_json, niche=niche)
            segments, _ = load_segments(result_visual_json)
            log(f"✅ Visual queries готовы: {result_visual_json.name}")
        except Exception as _vq_err:
            log(f"⚠️  Visual queries пропущены: {_vq_err}")
    elif result_visual_json.exists():
        log("Visual queries: загружаем result_visual.json")
        try:
            segments, _ = load_segments(result_visual_json)
        except Exception:
            pass  # оставляем оригинальные segments

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

    zone_a_segs = sum(1 for t in timings if t["zone"] == "A")
    zone_b_segs = sum(1 for t in timings if t["zone"] == "B")
    detected_boundary = timings[zone_a_segs]["start"] if zone_b_segs > 0 else clips_total_dur
    log(f"Зона A: {zone_a_segs} сег (0–{detected_boundary:.0f}s)  |  Зона B: {zone_b_segs} сег")

    # План переходов вычисляем ДО trim, чтобы знать какие клипы нуждаются в handle
    # (Зона A = каждый стык, Зона B = N рандомных)
    trans_plan = build_transition_plan(
        n_clips         = len(timings),   # len(timings) ≈ len(trimmed_clips)
        timings         = timings,
        zone_a_end_s    = detected_boundary,
        zone_b_count    = zone_b_count,
        clip_trans_name = trans_name,
        clip_trans_dur  = trans_dur,
    )

    # Trim клипов (stream copy, параллельно)
    # tpad генерирует freeze-frame handles синтетически — физический handle не нужен.
    trimmed_clips = trim_clips(segments_main, timings, lib_dir, temp_dir)
    if not trimmed_clips:
        log("✗ Нет обрезанных клипов"); sys.exit(1)

    log(f"[⏱] Timeline+Trim: {time.time()-t_timeline:.1f}s")

    # Времена переходов для SFX (в абсолютном времени аудио: смещаем на intro_dur)
    trans_times = compute_transition_times(segments_main, intro_dur, trans_dur) if sfx_enabled else []

    # SFX события (transition-based)
    sfx_events = _build_sfx_events_for_render(
        trans_plan    = trans_plan,
        trans_times   = trans_times,
        timings       = timings,
        intro_dur     = intro_dur,
        channel_id    = channel_id,
        sfx_enabled   = sfx_enabled,
        sfx_vol_scale = sfx_vol_scale,
        xf_dur        = trans_dur,
    ) if sfx_enabled else []

    # SFX события от Haiku (narrative-based) — мерж с transition SFX
    if sfx_enabled and any(s.get("sfx_cue") for s in segments):
        haiku_events = build_haiku_sfx_events(
            segments       = segments,
            intro_duration = intro_dur,
        )
        sfx_events = sorted(sfx_events + haiku_events, key=lambda e: e["time"])
        log(f"SFX: {len(sfx_events)} событий (transition + {len(haiku_events)} Haiku)")
    elif sfx_events:
        log(f"SFX: {len(sfx_events)} событий")

    # ── 4. RENDER ─────────────────────────────────────────────────────────────
    t_render = time.time()
    log("\n--- RENDER ---")

    videotrack_path  = temp_dir / "videotrack.mp4"
    mixed_audio_path = temp_dir / "audio.m4a"  # M4A = MP4+AAC: точный заголовок длины (не raw .aac)
    ass_path         = get_ass_path(channel_id, session)
    final_output     = get_final_video(channel_id, session)

    # Параллельно: видеоряд + аудио + субтитры
    with concurrent.futures.ThreadPoolExecutor(max_workers=3) as ex:
        fut_video = ex.submit(
            render_videotrack,
            trimmed_clips, trans_plan, trans_name, trans_dur, videotrack_path,
            clips_total_dur,   # ожидаемая длина видеоряда (без интро)
        )
        fut_audio = ex.submit(
            build_audio_track,
            voiceover, intro_path, intro_dur, total_dur,
            style, mixed_audio_path, sfx_events, args.no_music,
        )
        fut_subs = ex.submit(
            generate_subtitles,
            result_json, ass_path, style, intro_dur, args.no_subs,
        )

        video_ok               = fut_video.result()
        audio_ok               = fut_audio.result()
        ass_ok, drawtext_filter = fut_subs.result()

    log(f"Видеоряд: {'✓' if video_ok else '✗'}  |  Аудио: {'✓' if audio_ok else '✗'}  |  Субтитры: {'✓' if ass_ok else '✗'}")
    log(f"[⏱] Render (parallel): {time.time()-t_render:.1f}s")

    if not video_ok and not intro_path:
        log("✗ Нет ни видеоряда, ни интро"); sys.exit(1)

    # Финальный рендер
    render_ok = final_render(
        intro_path   = intro_path,
        videotrack       = videotrack_path,
        audio_path       = mixed_audio_path,
        ass_path         = ass_path,
        ass_ok           = ass_ok,
        drawtext_filter  = drawtext_filter,
        style            = style,
        intro_dur    = intro_dur,
        output       = final_output,
    )

    if not render_ok:
        log("✗ Финальный рендер не удался"); sys.exit(1)

    log(f"[⏱] Final render: {time.time()-t_render:.1f}s total")
    audit_final_audio(final_output, voice_path=voiceover)

    # ── 5. COMMIT & CLEANUP ───────────────────────────────────────────────────
    do_commit_history(selection_result, channel_id, session, final_output)

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
