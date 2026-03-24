#!/usr/bin/env python3
"""
assembler.py — полный монтаж видео для сессии Video Pipeline

Структура сессии (новая, без media/ и prompts/):
  data/channels/{lang}/{session}/input/       ← MP3 озвучка
  data/channels/{lang}/{session}/transcripts/ ← result.json, ASS
  data/channels/{lang}/{session}/intro_clips/ ← нарезанные клипы интро
  data/channels/{lang}/{session}/intro.mp4    ← готовое интро (кладёшь сам)
  data/channels/{lang}/{session}/output/      ← финальное видео

Пайплайн:
  1.  Найти последнюю сессию канала через paths.get_last_session()
  2.  Загрузить сегменты из transcripts/result.json
  3.  Найти озвучку в input/*.mp3
  4.  Найти интро: {session}/intro.mp4
  5.  Подобрать клипы из библиотеки (--use-library)
  6.  Нарезать клипы по пропорциональному таймингу
  7.  Склеить переходы → videotrack.mp4
  8.  Микс аудио: озвучка + интро + музыка → mixed_audio.aac
  9.  Генерация ASS субтитров
  10. Финальный экспорт: цвет + субтитры + аудио → {session}_XX_final.mp4

Запуск:
  py agents/assembler/assembler.py --channel channel_001_cosmos_de
  py agents/assembler/assembler.py --channel channel_002_cosmos_fr --use-library
  py agents/assembler/assembler.py --session Video_20260305_222350 --no-subs
  py agents/assembler/assembler.py --no-music --skip-intro-clips
"""

import argparse
import concurrent.futures
import io
import json
import os
import random
import re
import shutil
import subprocess
import sys
import threading
import time
from pathlib import Path

# Семафор для ограничения одновременных NVENC сессий (RTX 3060 лимит = 3)
_NVENC_SEM = threading.Semaphore(3)
# Счётчик shutil.copy хитов (клипы не требующие перекодирования)
_COPY_COUNTER = [0]

# ─── UTF-8 вывод ─────────────────────────────────────────────────────────────

try:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    elif hasattr(sys.stdout, "buffer"):
        sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
except Exception:
    pass

# ─── Пути ────────────────────────────────────────────────────────────────────

BASE_DIR        = Path(__file__).resolve().parent.parent.parent
DATA_DIR        = BASE_DIR / "data"
MUSIC_DIR       = DATA_DIR / "music"
AGENTS_DIR      = BASE_DIR / "agents"

MONTAGE_SCRIPT  = AGENTS_DIR / "assembler" / "montage.py"
SUBTITLE_SCRIPT = AGENTS_DIR / "assembler" / "subtitle_generator.py"

from dotenv import load_dotenv
load_dotenv(BASE_DIR / "config" / ".env")

# ─── Library ClipSelector ─────────────────────────────────────────────────────

LIBRARY_JSON  = BASE_DIR / "library" / "library.json"

_LIB_SEARCH_DIR = BASE_DIR / "agents" / "library"
if str(_LIB_SEARCH_DIR) not in sys.path:
    sys.path.insert(0, str(_LIB_SEARCH_DIR))
try:
    from clip_selector import select_clips_for_video as _select_clips
    _LIBRARY_OK = LIBRARY_JSON.exists()
    if _LIBRARY_OK:
        print("✅ clip_selector подключён", flush=True)
    else:
        print("⚠ clip_selector OK но library.json не найден", flush=True)
except ImportError as _e:
    _LIBRARY_OK = False
    _select_clips = None  # type: ignore
    print(f"⚠ clip_selector недоступен: {_e}", flush=True)

# ─── Channel Manager ─────────────────────────────────────────────────────────

_BOT_DIR = BASE_DIR / "bot"
if str(_BOT_DIR) not in sys.path:
    sys.path.insert(0, str(_BOT_DIR))
try:
    from channel_manager import (
        get_active_channel, get_channel_config, set_active_channel,
        get_channel_data_dir,
    )
    _CHANNEL_MANAGER_OK = True
except ImportError:
    _CHANNEL_MANAGER_OK = False
    def get_channel_data_dir(base): return Path(base)

# ─── paths.py ────────────────────────────────────────────────────────────────

_UTILS_DIR = BASE_DIR / "agents" / "utils"
if str(_UTILS_DIR) not in sys.path:
    sys.path.insert(0, str(_UTILS_DIR))
from paths import (  # noqa: E402
    get_input_dir, get_transcripts_dir,
    get_intro_clips_dir, get_intro_path,
    get_output_dir, get_audio_path,
    get_result_json, get_ass_path,
    get_final_video, get_last_session,
    get_session_dir,
    ensure_session_dirs, print_session_structure,
    CLIPS_DIR as LIBRARY_CLIPS,
)
_PATHS_OK = True

# ─── Импорт переходов (transitions.py в той же папке) ────────────────────────

_ASSEMBLER_DIR = Path(__file__).resolve().parent
if str(_ASSEMBLER_DIR) not in sys.path:
    sys.path.insert(0, str(_ASSEMBLER_DIR))
from transitions     import (glitch_transition, slide_motionblur_transition,
                              intro_to_main_transition, slide_transition,
                              smooth_zoom_transition, whip_pan_transition,
                              concat_all_with_transitions)
from audio_mixer     import (
    prepare_music_track,
    prepare_voice_track,
    final_mix,
)
from ass_generator   import generate_ass, generate_karaoke_ass
from subtitle_burner import burn_ass
try:
    from style_engine import get_channel_style, get_intro_transition_fn
    _STYLE_OK = True
except Exception:
    _STYLE_OK = False
    def get_channel_style(_): return {}
    def get_intro_transition_fn(_): return intro_to_main_transition
try:
    from sfx_mixer import inject_sfx, compute_transition_times
    _SFX_OK = True
except Exception:
    _SFX_OK = False
ORGANETTO_FONT_PATH = os.environ.get(
    "ORGANETTO_FONT_PATH",
    r"C:\Windows\Fonts\Organetto-Bold.ttf",
).strip()

# ─── Константы монтажа ───────────────────────────────────────────────────────

OUTPUT_RESOLUTION  = "1920x1080"
OUTPUT_BITRATE     = "25M"         # target video bitrate
OUTPUT_MAXRATE     = "30M"
OUTPUT_BUFSIZE     = "60M"
# NVENC: в 5-10x быстрее CPU-энкодинга, RTX 3060 поддерживает
USE_NVENC          = True          # False → libx264 fast
NVENC_PRESET       = "p4"         # p1(быстро)..p7(качество); p4=баланс
EXPORT_PRESET      = "fast"        # fallback для libx264

TRANSITION_DURATION  = float(os.environ.get("TRANSITION_DURATION", "1.0"))  # из .env (legacy)
GLITCH_DURATION      = 0.5    # длительность перехода intro→clip1 (дефолт; переопределяется стилем)
SLIDE_DURATION       = 0.5    # длительность clip-clip перехода (дефолт; переопределяется стилем)

SUBTITLE_FADE_IN_MS  = int(os.environ.get("SUBTITLE_FADE_IN_MS",  "100"))
SUBTITLE_FADE_OUT_MS = int(os.environ.get("SUBTITLE_FADE_OUT_MS", "100"))
SUBTITLE_RISE_PX     = int(os.environ.get("SUBTITLE_RISE_PX",     "15"))
SUBTITLE_FONT_SIZE   = int(os.environ.get("SUBTITLE_FONT_SIZE",   "28"))

MUSIC_START_SEC    = 88.0          # 1 мин 28 сек — старт музыки
MUSIC_FADE_IN_SEC  = 2.0           # fade-in при старте
MUSIC_FADE_OUT_SEC = 4.0           # fade-out в конце видео
MUSIC_XFADE_SEC    = 3.0           # crossfade между треками
MUSIC_MIN_TRACKS   = 3
MUSIC_MAX_TRACKS   = 5

VOICEOVER_DB       = -6.0          # громкость озвучки (диктор)
INTRO_AUDIO_DB     = -37.0         # громкость аудио интро-видео
MUSIC_DB           = -22.0         # громкость фоновой музыки (-22dB ≈ тихий фон, не перебивает озвучку)


# ─── Утилиты ─────────────────────────────────────────────────────────────────

def log(msg: str) -> None:
    try:
        print(f"[ASSEMBLER] {msg}", flush=True)
    except Exception:
        pass


def get_media_duration(path: Path) -> float:
    """Получить длительность медиафайла через ffprobe."""
    cmd = [
        "ffprobe", "-v", "quiet",
        "-print_format", "json",
        "-show_format",
        str(path),
    ]
    result = subprocess.run(cmd, capture_output=True, text=True)
    if result.returncode != 0:
        raise RuntimeError(f"ffprobe failed for {path.name}: {result.stderr}")
    return float(json.loads(result.stdout)["format"]["duration"])


# ─── Rhythm-based timing helpers ─────────────────────────────────────────────

def detect_silence_regions(
    audio_path: Path,
    noise_db: float = -30,
    min_dur:  float = 0.2,
) -> list[tuple[float, float]]:
    """
    Detect silence regions via FFmpeg silencedetect.
    Returns list of (start_sec, end_sec) tuples. Empty list on any error.
    """
    try:
        r = subprocess.run(
            ["ffmpeg", "-y", "-i", str(audio_path),
             "-af", f"silencedetect=n={noise_db}dB:d={min_dur}",
             "-f", "null", "-"],
            capture_output=True, text=True, timeout=60,
        )
        starts = [float(m) for m in re.findall(r"silence_start: ([\d.]+)", r.stderr)]
        ends   = [float(m) for m in re.findall(r"silence_end: ([\d.]+)",   r.stderr)]
        result = list(zip(starts, ends))
        return result
    except Exception:
        return []


def _adaptive_trans_dur(
    base_dur:       float,
    trans_time:     float,
    silence_regions: list[tuple[float, float]],
    trans_type:     str,
) -> float:
    """
    Adjust transition duration based on speech rhythm / silence proximity.
      - During silence    → extend up to +0.10s (more breathing room)
      - Near silence edge → extend up to +0.06s (clean boundary)
      - Dense speech      → shorten up to -0.03s (tight cut)
    Clamped per-type to avoid artifacts.
    """
    _BOUNDS = {
        "cross_zoom":   (0.16, 0.40),
        "glitch_flash": (0.08, 0.14),
        "smooth_zoom":  (0.20, 0.48),
        "whip_pan":     (0.12, 0.28),
        "slideleft":    (0.20, 0.60),
    }
    lo, hi = _BOUNDS.get(trans_type, (base_dur * 0.6, base_dur * 1.5))

    adj = base_dur
    for sil_s, sil_e in silence_regions:
        if sil_s <= trans_time <= sil_e:
            # Inside silence → longer transition feels natural
            adj = base_dur + random.uniform(0.05, 0.10)
            break
        if abs(trans_time - sil_s) < 0.3 or abs(trans_time - sil_e) < 0.3:
            # Near silence boundary → slight extension for clean feel
            adj = base_dur + random.uniform(0.02, 0.06)
            break
    else:
        # Dense speech — slight reduction for snappier cuts
        adj = base_dur - random.uniform(0.0, 0.03)

    return round(max(lo, min(hi, adj)), 3)


def _apply_anticipation(
    trans_times:     list[float],
    silence_regions: list[tuple[float, float]],
    max_shift:       float = 0.05,
) -> list[float]:
    """
    Shift transition timestamps slightly earlier when silence is imminent.

    Logic:
      - Silence begins within 0–200ms after a transition → shift transition
        earlier by 0.03–0.05s (anticipate the beat before the silence).
      - Transition falls 0–100ms after silence ends → slight pull-in to
        speech restart (0.01–0.03s).

    Simulates human editor intuition: cut just before the breath, not on it.
    No shift applied if no silence is nearby.
    """
    if not silence_regions:
        return trans_times

    adjusted = []
    for t in trans_times:
        shift = 0.0
        for sil_s, sil_e in silence_regions:
            # Upcoming silence within 200ms → anticipate
            if 0.0 <= sil_s - t <= 0.20:
                shift = random.uniform(0.03, max_shift)
                break
            # Just exited silence (within 100ms) → slight pull toward speech start
            if 0.0 <= t - sil_e <= 0.10:
                shift = random.uniform(0.01, 0.03)
                break
        adjusted.append(max(0.1, t - shift))

    return adjusted


def _build_trans_sequence(
    n_clips:         int,
    base_trans:      str,
    base_dur:        float,
    trans_times:     list[float],
    silence_regions: list[tuple[float, float]],
) -> list[dict]:
    """
    Build per-boundary transition sequence with:
      - Rhythm-based duration adjustment (silence-aware)
      - Occasional flash frames (5% chance): cinematic white/black accent
      - Occasional type variety (FR: 10% cross_zoom; DE: stays pure)
      - No two consecutive flash frames (prevents strobing)

    Returns list of {"type": str, "dur": float} for each boundary.
    """
    # Flash types: three variants — white, black, and fade-to-black (less "digital")
    _FLASH_SET = {"fadewhite", "fadeblack", "fade"}

    sequence: list[dict] = []
    n_bdrs = max(0, n_clips - 1)

    for i in range(n_bdrs):
        t_time = trans_times[i] if i < len(trans_times) else 0.0

        # Never two consecutive flash frames
        prev_is_flash = i > 0 and sequence[-1]["type"] in _FLASH_SET

        # ── Flash frame (5% chance) ────────────────────────────────────────
        # Three variants: fadewhite (35%), fadeblack (35%), fade-to-black (30%)
        # "fade" = A→black→B — less "pure digital white", more cinematic
        if not prev_is_flash and random.random() < 0.05:
            flash_type = random.choices(
                ["fadewhite", "fadeblack", "fade"],
                weights=[0.35, 0.35, 0.30],
            )[0]
            sequence.append({
                "type": flash_type,
                "dur":  round(random.uniform(0.04, 0.08), 3),
            })
            continue

        # ── Occasional type variety ────────────────────────────────────────
        if base_trans == "glitch_flash" and random.random() < 0.10:
            adj = _adaptive_trans_dur(0.28, t_time, silence_regions, "cross_zoom")
            adj = round(adj * random.uniform(0.92, 1.08), 3)   # micro-variance
            sequence.append({"type": "cross_zoom", "dur": adj})
            continue

        # ── Base transition: rhythm-adjusted + ±8% micro-variance ─────────
        # The micro-variance breaks mechanical identical timing between cuts
        adj = _adaptive_trans_dur(base_dur, t_time, silence_regions, base_trans)
        adj = round(adj * random.uniform(0.92, 1.08), 3)
        sequence.append({"type": base_trans, "dur": adj})

    return sequence


# ─── Метаданные — реалистичные Adobe Premiere / After Effects ────────────────

def _make_metadata_flags() -> list[str]:
    """
    Generate realistic Adobe workflow metadata for final export.
    5 distinct combos — varies encoder/software/comment presence.
    creation_time is randomized to working hours within last 18 months.
    """
    import datetime

    _COMBOS = [
        # 1. Premiere-only — direct timeline export (lightweight)
        {"encoder": "Adobe Premiere Pro",
         "software": "Adobe Premiere Pro",
         "comment":  None},
        # 2. Media Encoder render — most common professional path (comment varies)
        {"encoder": "Adobe Media Encoder CC",
         "software": "Adobe Premiere Pro",
         "comment":  random.choice([None, "Rendered by Adobe Media Encoder"])},
        # 3. After Effects → AME handoff
        {"encoder": "Adobe Media Encoder",
         "software": "Adobe After Effects",
         "comment":  None},
        # 4. Full Dynamic Link pipeline (AE + Premiere + AME)
        {"encoder": "Adobe Media Encoder CC",
         "software": "Adobe Premiere Pro/After Effects",
         "comment":  "Linked with After Effects via Dynamic Link"},
        # 5. Software only, no encoder (direct Premiere CC render)
        {"encoder": None,
         "software": "Adobe Premiere Pro CC",
         "comment":  None},
        # 6. Rare: only software, slightly different version string
        {"encoder": None,
         "software": "Adobe Premiere Pro 2024",
         "comment":  None},
        # 7. Rare: no encoder, comment only (e.g., export script)
        {"encoder": None,
         "software": "Adobe Premiere Pro",
         "comment":  random.choice([None, "Created in Adobe Premiere Pro"])},
    ]

    combo = random.choice(_COMBOS)

    # Working-hours timestamp: 09:00–21:00, within last 18 months
    base  = datetime.datetime(2024, 6, 1, 9, 0, 0)
    delta = datetime.timedelta(
        days    = random.randint(0, 540),
        hours   = random.randint(0, 12),    # 09:00–21:00
        minutes = random.randint(0, 59),
        seconds = random.randint(0, 59),
    )
    ts = (base + delta).strftime("%Y-%m-%dT%H:%M:%S.000000Z")

    # Rare (8%): no creation_time field at all
    # Simulates export where timestamp was manually cleared or not written
    include_ts = random.random() >= 0.08

    flags = (["-metadata", f"creation_time={ts}"] if include_ts else [])
    if combo.get("software"):
        flags += ["-metadata", f"software={combo['software']}"]
    if combo.get("encoder"):
        flags += ["-metadata", f"encoder={combo['encoder']}"]
    if combo.get("comment"):
        flags += ["-metadata", f"comment={combo['comment']}"]

    # Ensure at least one metadata field is present
    if not flags:
        flags = ["-metadata", "software=Adobe Premiere Pro"]

    return flags


# ─── Обрезка клипов (для новых переходов) ────────────────────────────────────

_FIXED_AFTER_SEC = 300  # сегменты с audio_start >= 300s → строго 5s, без масштабирования

def _compute_timings(segments: list, total_sec: float) -> list[dict]:
    """
    Распределение длительностей клипов:
    - сегменты до 5 мин  : масштабируем пропорционально под оставшееся время
    - сегменты после 5 мин: строго seg.end - seg.start (≈5s) — shutil.copy срабатывает
    """
    pairs = []
    for seg in segments:
        d     = max(float(seg.get("end", 0)) - float(seg.get("start", 0)), 2.0)
        fixed = float(seg.get("start", 0)) >= _FIXED_AFTER_SEC
        pairs.append((d, fixed))

    fixed_total    = sum(d for d, f in pairs if f)
    var_target     = max(total_sec - fixed_total, 0.1)
    var_raw        = [d for d, f in pairs if not f]
    var_sum        = sum(var_raw) or 1.0
    scale          = var_target / var_sum

    vi = 0
    durations: list[float] = []
    for d, is_fixed in pairs:
        if is_fixed:
            durations.append(d)          # точно 5s — shutil.copy
        else:
            durations.append(var_raw[vi] * scale)
            vi += 1

    timings: list[dict] = []
    t = 0.0
    for d in durations:
        timings.append({"start": t, "end": t + d, "duration": d, "words": 1})
        t += d

    # Не растягиваем последний клип — остаток заполнит чёрный паддинг в финальном экспорте

    return timings


def _trim_or_loop_clip(src: Path, dst: Path, target_dur: float) -> None:
    """
    Обрезать или зациклить клип до target_dur секунд.
    Нормализует до OUTPUT_RESOLUTION @ 25fps yuv420p (без аудио).
    """
    dst.parent.mkdir(parents=True, exist_ok=True)
    w, h = OUTPUT_RESOLUTION.split("x")
    scale_vf = (
        f"scale={w}:{h}:force_original_aspect_ratio=decrease,"
        f"pad={w}:{h}:(ow-iw)/2:(oh-ih)/2:black,"
        f"setsar=1,fps=25,format=yuv420p"
    )

    try:
        clip_dur = get_media_duration(src)
    except Exception:
        clip_dur = 0.0

    concat_txt: Path | None = None

    # Если target > clip (зацикливание), ограничиваем до clip_dur — лучше чем loop одного клипа
    if clip_dur > 0 and target_dur > clip_dur + 0.2:
        target_dur = clip_dur

    if clip_dur > 0 and abs(target_dur - clip_dur) < 0.2:
        # Клип уже нужной длины — просто копируем, без перекодирования
        shutil.copy(str(src), str(dst))
        _COPY_COUNTER[0] += 1
        return
    elif clip_dur > 0 and target_dur <= clip_dur:
        cmd_nvenc = [
            "ffmpeg", "-y", "-i", str(src),
            "-t", f"{target_dur:.3f}",
            "-vf", scale_vf,
            "-c:v", "h264_nvenc", "-preset", "p4", "-cq", "20",
            "-pix_fmt", "yuv420p", "-an",
            "-loglevel", "warning",
            str(dst),
        ]
        cmd_cpu = [
            "ffmpeg", "-y", "-i", str(src),
            "-t", f"{target_dur:.3f}",
            "-vf", scale_vf,
            "-c:v", "libx264", "-preset", "ultrafast",
            "-pix_fmt", "yuv420p", "-an",
            "-loglevel", "warning",
            str(dst),
        ]
    else:
        loops = max(int(target_dur / max(clip_dur, 1.0)) + 2, 2)
        concat_txt = dst.with_suffix(".concat.txt")
        with open(concat_txt, "w", encoding="utf-8") as f:
            for _ in range(loops):
                f.write(f"file '{src.as_posix()}'\n")
        cmd_nvenc = [
            "ffmpeg", "-y",
            "-f", "concat", "-safe", "0",
            "-i", str(concat_txt),
            "-t", f"{target_dur:.3f}",
            "-vf", scale_vf,
            "-c:v", "h264_nvenc", "-preset", "p4", "-cq", "20",
            "-pix_fmt", "yuv420p", "-an",
            "-loglevel", "warning",
            str(dst),
        ]
        cmd_cpu = [
            "ffmpeg", "-y",
            "-f", "concat", "-safe", "0",
            "-i", str(concat_txt),
            "-t", f"{target_dur:.3f}",
            "-vf", scale_vf,
            "-c:v", "libx264", "-preset", "ultrafast",
            "-pix_fmt", "yuv420p", "-an",
            "-loglevel", "warning",
            str(dst),
        ]

    # Пробуем NVENC (с семафором), fallback на CPU
    with _NVENC_SEM:
        result = subprocess.run(cmd_nvenc, capture_output=True)
    if result.returncode != 0:
        result = subprocess.run(cmd_cpu)
    if concat_txt and concat_txt.exists():
        concat_txt.unlink(missing_ok=True)
    if result.returncode != 0:
        raise RuntimeError(f"trim/loop failed for {src.name} (rc={result.returncode})")


def prepare_trimmed_clips(
    segments:    list,
    clips_dir:   Path,
    clip_prefix: str,
    total_sec:   float,
    temp_dir:    Path,
) -> list[Path]:
    """
    Нарезать клипы до пропорциональных длительностей — ПАРАЛЛЕЛЬНО (max_workers=8).
    Возвращает список Path к обрезанным клипам (в том же порядке, что и segments).
    """
    temp_dir.mkdir(parents=True, exist_ok=True)
    timings = _compute_timings(segments, total_sec)

    # Собираем задачи (src, dst, duration)
    tasks: list[tuple[Path, Path, float, int, int]] = []
    for i, (seg, timing) in enumerate(zip(segments, timings)):
        clip_name = f"{clip_prefix}{int(seg['id']):03d}.mp4"
        src = clips_dir / clip_name
        if not src.exists():
            log(f"  ⚠ Клип не найден: {clip_name} — пропущен")
            continue
        dst  = temp_dir / f"trimmed_{int(seg['id']):03d}.mp4"
        tgt  = timing["duration"]

        # Если сегмент фиксированный и длительность >> реальная длина клипа —
        # разбиваем на несколько слотов с РАЗНЫМИ клипами (без зацикливания)
        is_fixed = float(seg.get("start", 0)) >= _FIXED_AFTER_SEC
        if is_fixed and tgt > 10.0:
            try:
                clip_dur = get_media_duration(src)
            except Exception:
                clip_dur = 0.0
            if clip_dur > 0 and tgt > clip_dur + 0.2:
                n_subs = max(1, round(tgt / clip_dur))
                sub_dur = tgt / n_subs
                all_pool = sorted(clips_dir.glob(f"{clip_prefix}*.mp4"))
                other_clips = [c for c in all_pool if c.resolve() != src.resolve()]
                random.shuffle(other_clips)
                log(f"  🔀 Сегмент {seg['id']} ({tgt:.1f}s) → {n_subs} подклипов ×{sub_dur:.2f}s")
                for j in range(n_subs):
                    sub_src = src if j == 0 else other_clips[(j - 1) % len(other_clips)]
                    sub_dst = dst if j == 0 else temp_dir / f"trimmed_{int(seg['id']):03d}_{j:02d}.mp4"
                    tasks.append((sub_src, sub_dst, sub_dur, i + 1, len(segments)))
                continue

        tasks.append((src, dst, tgt, i + 1, len(segments)))

    if not tasks:
        return []

    _COPY_COUNTER[0] = 0
    log(f"  ⏳ Параллельная обрезка {len(tasks)} клипов (workers=8)...")
    t_trim = time.time()

    def _trim_task(args):
        src, dst, dur, idx, total = args
        _trim_or_loop_clip(src, dst, dur)
        return dst

    results: dict[Path, Path | None] = {}
    with concurrent.futures.ThreadPoolExecutor(max_workers=8) as ex:
        fut_map = {ex.submit(_trim_task, t): t[1] for t in tasks}
        for fut in concurrent.futures.as_completed(fut_map):
            dst = fut_map[fut]
            try:
                results[dst] = fut.result()
            except Exception as e:
                log(f"  ⚠ Ошибка обрезки {dst.name}: {e}")
                results[dst] = None

    # Возвращаем в исходном порядке
    trimmed: list[Path] = []
    for _, dst, *_ in tasks:
        if results.get(dst) and dst.exists():
            trimmed.append(dst)

    _copied = _COPY_COUNTER[0]
    _encoded = len(trimmed) - _copied
    log(f"  ⏱️ Обрезка: {time.time() - t_trim:.1f}s — {len(trimmed)}/{len(tasks)} клипов "
        f"(copy={_copied} skip-encode, nvenc={_encoded})")
    return trimmed


def _concat_two_clips(clip1: Path, clip2: Path, output: Path) -> None:
    """Простая склейка двух клипов без перехода (fallback для глитч/слайда)."""
    output.parent.mkdir(parents=True, exist_ok=True)
    concat_txt = output.with_suffix(".concat.txt")
    with open(concat_txt, "w", encoding="utf-8") as f:
        f.write(f"file '{clip1.as_posix()}'\n")
        f.write(f"file '{clip2.as_posix()}'\n")
    result = subprocess.run([
        "ffmpeg", "-y",
        "-f", "concat", "-safe", "0",
        "-i", str(concat_txt),
        "-c:v", "libx264", "-preset", "fast",
        "-pix_fmt", "yuv420p", "-an",
        "-loglevel", "warning",
        str(output),
    ])
    concat_txt.unlink(missing_ok=True)
    if result.returncode != 0:
        raise RuntimeError(f"_concat_two_clips failed (rc={result.returncode})")


# ─── Поиск данных сессии ──────────────────────────────────────────────────────

def find_intro(channel_id: str, session: str) -> Path | None:
    """
    Найти интро-видео для сессии.
    Приоритет: {session}/intro.mp4 → {session}/Intro*.mp4 → {session}/input/Intro*.mp4
    """
    # Приоритет 0: {session}/intro.mp4 (точное имя)
    intro_new = get_intro_path(channel_id, session)
    if intro_new.exists() and intro_new.stat().st_size > 10000:
        return intro_new

    # Приоритет 1: любой файл в корне сессии, начинающийся с "intro" (регистр не важен)
    session_dir = get_session_dir(channel_id, session)
    for f in sorted(session_dir.glob("*.mp4")):
        if f.stem.lower().startswith("intro") and f.stat().st_size > 10000:
            return f

    # Fallback: input/ папка (старая схема)
    input_dir = get_input_dir(channel_id, session)
    patterns  = ("Intro_*.mp4", "Intro*.mp4", "intro_*.mp4", "intro*.mp4")
    for pattern in patterns:
        files = sorted(input_dir.glob(pattern))
        if files:
            return files[0]
    return None


def load_segments(result_path: Path) -> tuple[list, float]:
    """Загрузить сегменты из result.json."""
    if not result_path.exists():
        raise FileNotFoundError(f"result.json не найден: {result_path}")
    with open(result_path, encoding="utf-8") as f:
        data = json.load(f)
    return data.get("segments", []), float(data.get("total_duration", 0))


# ─── project.json ─────────────────────────────────────────────────────────────

def build_project_json(
    session:              str,
    clips_dir:            Path,
    clip_prefix:          str,
    segments:             list,
    output_dir:           Path,
    total_duration_hint:  float = 0.0,
    output_filename:      str   = "scenes_video.mp4",
) -> Path:
    """
    Собирает project.json для montage.py.
    audio_file НЕ включается — весь аудио-микс делает assembler.py.
    total_duration_hint: фактическая длительность озвучки для этих сцен.
    """
    output_dir.mkdir(parents=True, exist_ok=True)

    scenes = []
    for seg in segments:
        seg_id    = int(seg["id"])
        clip_name = f"{clip_prefix}{seg_id:03d}.mp4"
        clip_path = clips_dir / clip_name

        if not clip_path.exists():
            log(f"  ⚠ Клип не найден: {clip_name} — пропущен")
            continue

        scenes.append({"id": seg_id, "clip": clip_name, "text": seg.get("text", "")})

    if not scenes:
        raise RuntimeError("Нет доступных клипов — project.json не создан")

    project = {
        "project_name":        session,
        "output_file":         str(output_dir / output_filename),
        "resolution":          OUTPUT_RESOLUTION,
        "clips_dir":           str(clips_dir),
        "scenes":              scenes,
    }
    if total_duration_hint > 0:
        project["total_duration_hint"] = total_duration_hint

    project_json_path = output_dir / "project.json"
    with open(project_json_path, "w", encoding="utf-8") as f:
        json.dump(project, f, ensure_ascii=False, indent=2)

    log(f"project.json: {len(scenes)} сцен, {OUTPUT_RESOLUTION} → {project_json_path.name}")
    return project_json_path


# ─── Монтаж (montage.py) ──────────────────────────────────────────────────────

def run_montage(project_json_path: Path) -> None:
    cmd = ["py", str(MONTAGE_SCRIPT), str(project_json_path)]
    log(f"Запуск montage.py ...")
    result = subprocess.run(cmd)
    if result.returncode != 0:
        raise RuntimeError(f"montage.py завершился с кодом {result.returncode}")


# ─── Интро — склейка одним куском ─────────────────────────────────────────────

def prepend_intro(intro_path: Path, scenes_video: Path, output_path: Path,
                  intro_dur: float = 0.0) -> None:
    """
    Склеивает интро (цельный клип) + видеоряд сцен в один файл.
    Оба потока нормализуются до 1920×1080 @ 30fps yuv420p.
    На стыке интро/сцен — dissolve-переход (GLITCH_DURATION сек).
    Только видео — без аудио.
    """
    w, h = OUTPUT_RESOLUTION.split("x")
    scale_pad = (
        f"scale={w}:{h}:force_original_aspect_ratio=decrease,"
        f"pad={w}:{h}:(ow-iw)/2:(oh-ih)/2:black,"
        f"fps=30,format=yuv420p"
    )
    if intro_dur > GLITCH_DURATION:
        glitch_offset = intro_dur - GLITCH_DURATION
        filter_complex = (
            f"[0:v]{scale_pad}[v0];"
            f"[1:v]{scale_pad}[v1];"
            f"[v0][v1]xfade=transition=dissolve:duration={GLITCH_DURATION}:offset={glitch_offset:.3f}[vout]"
        )
        log(f"Глитч-переход: dissolve {GLITCH_DURATION}s на {glitch_offset:.1f}s")
    else:
        filter_complex = (
            f"[0:v]{scale_pad}[v0];"
            f"[1:v]{scale_pad}[v1];"
            f"[v0][v1]concat=n=2:v=1:a=0[vout]"
        )
    cmd = [
        "ffmpeg", "-y",
        "-i", str(intro_path),
        "-i", str(scenes_video),
        "-filter_complex", filter_complex,
        "-map", "[vout]",
        "-c:v", "libx264", "-preset", "fast",
        "-pix_fmt", "yuv420p",
        "-an",
        "-loglevel", "warning",
        str(output_path),
    ]
    log(f"Склейка: интро ({intro_path.name}) + видеоряд сцен → {output_path.name}")
    result = subprocess.run(cmd)
    if result.returncode != 0:
        raise RuntimeError(f"prepend_intro failed (rc={result.returncode})")
    log(f"✓ Интро + сцены: {output_path.name}")


# ─── Музыка ───────────────────────────────────────────────────────────────────

def select_music_tracks(needed_sec: float) -> list[Path]:
    """
    Случайно выбрать 3–5 треков из data/music/.
    Суммарная длительность с учётом кроссфейдов >= needed_sec.
    """
    all_tracks = []
    for ext in ("*.mp3", "*.wav", "*.m4a", "*.ogg"):
        all_tracks += list(MUSIC_DIR.glob(ext))
    if not all_tracks:
        return []

    random.shuffle(all_tracks)

    selected: list[Path] = []
    total_dur = 0.0

    for track in all_tracks:
        if len(selected) >= MUSIC_MAX_TRACKS:
            break
        try:
            dur = get_media_duration(track)
        except Exception:
            continue
        selected.append(track)
        total_dur += dur
        xfade_loss = MUSIC_XFADE_SEC * max(0, len(selected) - 1)
        if len(selected) >= MUSIC_MIN_TRACKS and (total_dur - xfade_loss) >= needed_sec:
            break

    return selected


def build_music_track(
    tracks:      list[Path],
    needed_sec:  float,
    output_path: Path,
) -> None:
    """
    Собирает музыкальный трек из нескольких файлов:
    - Регулировка громкости: MUSIC_DB
    - Crossfade между треками: MUSIC_XFADE_SEC (треугольный)
    - Fade-in: MUSIC_FADE_IN_SEC в начале
    - Fade-out: MUSIC_FADE_OUT_SEC в конце
    - Обрезка до needed_sec
    """
    n = len(tracks)
    cmd = ["ffmpeg", "-y"]
    for t in tracks:
        cmd += ["-i", str(t)]

    parts: list[str] = []

    # Шаг 1: dynaudnorm выравнивает тихие атмосферные секции трека,
    # затем volume для фонового уровня.
    # dynaudnorm(f=200,g=15,p=0.9) — мягкий компрессор без артефактов.
    for i in range(n):
        parts.append(
            f"[{i}:a]"
            f"dynaudnorm=f=200:g=15:p=0.9,"
            f"volume={MUSIC_DB}dB,"
            f"aformat=sample_rates=44100:channel_layouts=stereo"
            f"[a{i}]"
        )

    # Шаг 2: простая склейка треков (concat, без crossfade между ними).
    # Crossfade создавал паузы тишины на стыках — убран.
    # Fade-in/out применяются только в начале и конце всего трека (шаг 3).
    if n == 1:
        last = "a0"
    else:
        inputs = "".join(f"[a{i}]" for i in range(n))
        parts.append(f"{inputs}concat=n={n}:v=0:a=1[cat]")
        last = "cat"

    # Шаг 3: fade-in + fade-out + обрезка до needed_sec
    fade_out_st = max(0.0, needed_sec - MUSIC_FADE_OUT_SEC)
    parts.append(
        f"[{last}]"
        f"afade=t=in:st=0:d={MUSIC_FADE_IN_SEC},"
        f"afade=t=out:st={fade_out_st:.3f}:d={MUSIC_FADE_OUT_SEC},"
        f"atrim=0:{needed_sec:.3f},"
        f"asetpts=PTS-STARTPTS"
        f"[music_out]"
    )

    filter_complex = "; ".join(parts)

    cmd += [
        "-filter_complex", filter_complex,
        "-map", "[music_out]",
        "-c:a", "aac", "-b:a", "320k",
        "-loglevel", "warning",
        str(output_path),
    ]

    log(f"Строю музыкальный трек ({n} треков, {needed_sec:.0f}s) ...")
    result = subprocess.run(cmd)
    if result.returncode != 0:
        raise RuntimeError(f"build_music_track failed (rc={result.returncode})")
    log(f"✓ Музыкальный трек: {output_path.name}")


# ─── Аудио-микс ───────────────────────────────────────────────────────────────

def mix_final_audio(
    video_path:       Path,
    voiceover_path:   Path,
    intro_path:       Path | None,
    music_track_path: Path | None,
    intro_dur:        float,
    output_path:      Path,
) -> None:
    """
    Смикшировать все аудио-треки и объединить с видео:
      - Озвучка (диктор):  VOICEOVER_DB dB, весь ролик
      - Интро аудио:       INTRO_AUDIO_DB dB, первые intro_dur секунд
      - Музыка:            задержка MUSIC_START_SEC, MUSIC_DB уже в треке
    Видео копируется без перекодирования (c:v copy).
    Длительность выхода = длительность видео (-shortest).
    """
    cmd = ["ffmpeg", "-y", "-i", str(video_path), "-i", str(voiceover_path)]
    parts: list[str] = []
    mix_labels: list[str] = []
    next_idx = 2

    # Озвучка
    parts.append(f"[1:a]volume={VOICEOVER_DB}dB[vo]")
    mix_labels.append("[vo]")

    # Интро аудио (из intro.mp4)
    if intro_path and intro_path.exists() and intro_dur > 0.5:
        cmd += ["-i", str(intro_path)]
        parts.append(
            f"[{next_idx}:a]"
            f"volume={INTRO_AUDIO_DB}dB,"
            f"atrim=0:{intro_dur:.3f},"
            f"asetpts=PTS-STARTPTS"
            f"[intro_a]"
        )
        mix_labels.append("[intro_a]")
        next_idx += 1
        log(f"Интро аудио: {intro_path.name} ({intro_dur:.1f}s @ {INTRO_AUDIO_DB} dB)")

    # Музыка (задержка MUSIC_START_SEC)
    if music_track_path and music_track_path.exists():
        cmd += ["-i", str(music_track_path)]
        delay_ms = int(MUSIC_START_SEC * 1000)
        parts.append(
            f"[{next_idx}:a]adelay={delay_ms}|{delay_ms}[music_d]"
        )
        mix_labels.append("[music_d]")
        next_idx += 1
        log(f"Музыка: старт +{MUSIC_START_SEC:.0f}s ({MUSIC_START_SEC/60:.1f} мин)")

    # Финальный микс:
    #   duration=longest  — не обрезать по первому закончившемуся потоку
    #   normalize=0       — не нормализовать автоматически
    #   dropout_transition=0 — НЕ фейдить потоки при окончании (нет пауз в музыке!)
    n      = len(mix_labels)
    mix_in = "".join(mix_labels)
    parts.append(f"{mix_in}amix=inputs={n}:duration=longest:normalize=0:dropout_transition=0[aout]")

    filter_complex = "; ".join(parts)

    cmd += [
        "-filter_complex", filter_complex,
        "-map", "0:v",
        "-map", "[aout]",
        "-c:v", "copy",
        "-c:a", "aac", "-b:a", "320k",
        "-shortest",                   # обрезать до длины видео
        "-loglevel", "warning",
        str(output_path),
    ]

    log(f"Аудио-микс: {n} трека(ов) → {output_path.name}")
    result = subprocess.run(cmd)
    if result.returncode != 0:
        raise RuntimeError(f"mix_final_audio failed (rc={result.returncode})")
    log(f"✓ Аудио смикшировано: {output_path.name}")


# ─── Субтитры ─────────────────────────────────────────────────────────────────

def generate_subtitles(session: str, output_dir: Path) -> Path:
    """Запустить subtitle_generator.py → вернуть путь к .srt."""
    result_json = TRANSCRIPTS_DIR / session / "result.json"
    srt_path    = output_dir / "subtitles.srt"
    cmd = ["py", str(SUBTITLE_SCRIPT),
           "--result-json", str(result_json),
           "--output-srt",  str(srt_path)]
    log("Генерация субтитров ...")
    result = subprocess.run(cmd, capture_output=True, text=True)
    if result.stdout:
        print(result.stdout, end="", flush=True)
    if result.returncode != 0:
        raise RuntimeError(f"subtitle_generator.py: {result.stderr}")
    return srt_path


def overlay_subtitles(
    video_path:  Path,
    srt_path:    Path,
    output_path: Path,
    font_path:   str,
) -> None:
    """
    Наложить субтитры + финальный экспорт:
    - Шрифт Organetto-Bold, 52px, белый, полупрозрачный чёрный бокс, низ
    - Кодек: libx264, 25 Mbps (max 30 Mbps), preset fast
    """
    font_name = Path(font_path).stem

    # Windows fix: FFmpeg subtitles filter не умеет обрабатывать Windows-пути с C:
    # Решение: передаём путь ОТНОСИТЕЛЬНО BASE_DIR и запускаем с cwd=BASE_DIR
    try:
        srt_rel = srt_path.relative_to(BASE_DIR).as_posix()
    except ValueError:
        # SRT не внутри BASE_DIR — делаем копию рядом с видео с коротким именем
        srt_copy = output_path.parent / "sub.srt"
        shutil.copy2(str(srt_path), str(srt_copy))
        srt_rel = srt_copy.relative_to(BASE_DIR).as_posix()

    force_style = (
        f"FontName={font_name},"
        f"FontSize=28,"
        f"PrimaryColour=&H00FFFFFF,"
        f"BackColour=&H80000000,"
        f"BorderStyle=3,"
        f"Outline=0,"
        f"Shadow=0,"
        f"Alignment=2,"
        f"MarginV=25"
    )
    vf = f"subtitles={srt_rel}:force_style='{force_style}'"

    # video_path и output_path тоже как относительные (или абсолютные, FFmpeg сам разберётся)
    enc = "h264_nvenc" if USE_NVENC else f"libx264 {EXPORT_PRESET}"
    cmd = (
        ["ffmpeg", "-y", "-i", str(video_path), "-vf", vf]
        + _video_encode_args()
        + ["-c:a", "copy", "-loglevel", "warning", str(output_path)]
    )
    log(f"Субтитры + экспорт {OUTPUT_BITRATE} ({enc}) → {output_path.name}")
    result = subprocess.run(cmd, cwd=str(BASE_DIR))  # cwd=BASE_DIR для rel. пути SRT
    if result.returncode != 0:
        if USE_NVENC:
            log("⚠ NVENC ошибка при субтитрах, пробую libx264 ...")
            cmd2 = (
                ["ffmpeg", "-y", "-i", str(video_path), "-vf", vf,
                 "-c:v", "libx264", "-preset", EXPORT_PRESET,
                 "-b:v", OUTPUT_BITRATE, "-maxrate", OUTPUT_MAXRATE, "-bufsize", OUTPUT_BUFSIZE,
                 "-c:a", "copy", "-loglevel", "warning", str(output_path)]
            )
            if subprocess.run(cmd2, cwd=str(BASE_DIR)).returncode != 0:
                raise RuntimeError("overlay_subtitles failed")
        else:
            raise RuntimeError(f"overlay_subtitles failed (rc={result.returncode})")


def trim_video_start(input_path: Path, output_path: Path, offset_sec: float) -> None:
    """
    Обрезать начало видео: оставить всё начиная с offset_sec.
    Используется чтобы убрать интро из видео с уже вожжёнными субтитрами.
    Копирует потоки без перекодирования (-c copy).
    """
    log(f"Обрезка: убираем первые {offset_sec:.1f}s → {output_path.name}")
    tmp = output_path.with_suffix(".trim_tmp.mp4")
    cmd = [
        "ffmpeg", "-y",
        "-ss", str(offset_sec),
        "-i", str(input_path),
        "-c", "copy",
        "-loglevel", "warning",
        str(tmp),
    ]
    result = subprocess.run(cmd)
    if result.returncode != 0:
        raise RuntimeError(f"trim_video_start failed (rc={result.returncode})")
    tmp.replace(output_path)


def _video_encode_args() -> list[str]:
    """Параметры видеокодека: NVENC (GPU) или libx264 (CPU)."""
    if USE_NVENC:
        return [
            "-c:v", "h264_nvenc",
            "-preset", NVENC_PRESET,
            "-b:v", OUTPUT_BITRATE,
            "-maxrate", OUTPUT_MAXRATE,
            "-bufsize", OUTPUT_BUFSIZE,
            "-pix_fmt", "yuv420p",
        ]
    else:
        return [
            "-c:v", "libx264",
            "-preset", EXPORT_PRESET,
            "-b:v", OUTPUT_BITRATE,
            "-maxrate", OUTPUT_MAXRATE,
            "-bufsize", OUTPUT_BUFSIZE,
        ]


def export_final(
    video_path:  Path,
    output_path: Path,
) -> None:
    """Финальный экспорт без субтитров (25 Mbps, NVENC или libx264)."""
    enc = "h264_nvenc" if USE_NVENC else f"libx264 {EXPORT_PRESET}"
    cmd = (
        ["ffmpeg", "-y", "-i", str(video_path)]
        + _video_encode_args()
        + ["-c:a", "copy", "-loglevel", "warning", str(output_path)]
    )
    log(f"Финальный экспорт {OUTPUT_BITRATE} ({enc}) → {output_path.name}")
    result = subprocess.run(cmd)
    if result.returncode != 0:
        if USE_NVENC:
            log("⚠ NVENC ошибка, пробую libx264 ...")
            cmd2 = (
                ["ffmpeg", "-y", "-i", str(video_path),
                 "-c:v", "libx264", "-preset", EXPORT_PRESET,
                 "-b:v", OUTPUT_BITRATE, "-maxrate", OUTPUT_MAXRATE, "-bufsize", OUTPUT_BUFSIZE,
                 "-c:a", "copy", "-loglevel", "warning", str(output_path)]
            )
            result2 = subprocess.run(cmd2)
            if result2.returncode != 0:
                raise RuntimeError(f"export_final failed (rc={result2.returncode})")
        else:
            raise RuntimeError(f"export_final failed (rc={result.returncode})")


# ─── Library clip resolver ────────────────────────────────────────────────────

def _trim_clip_nvenc(src: Path, dst: Path, duration: float) -> bool:
    """Обрезать клип до duration секунд через NVENC (fallback libx264)."""
    dst.parent.mkdir(parents=True, exist_ok=True)
    cmd = [
        "ffmpeg", "-y", "-i", str(src),
        "-t", f"{duration:.3f}",
        "-c:v", "h264_nvenc", "-preset", "p4", "-cq", "19",
        "-b:v", "8M", "-maxrate", "10M", "-bufsize", "20M",
        "-pix_fmt", "yuv420p", "-an",
        "-loglevel", "warning",
        str(dst),
    ]
    if subprocess.run(cmd).returncode == 0 and dst.exists():
        return True
    # CPU fallback
    cmd2 = [
        "ffmpeg", "-y", "-i", str(src),
        "-t", f"{duration:.3f}",
        "-c:v", "libx264", "-preset", "fast", "-crf", "18",
        "-b:v", "8M", "-maxrate", "10M", "-bufsize", "20M",
        "-pix_fmt", "yuv420p", "-an",
        "-loglevel", "warning",
        str(dst),
    ]
    return subprocess.run(cmd2).returncode == 0 and dst.exists()


def resolve_library_clips(
    segments:         list[dict],
    clips_dir:        Path,
    clip_prefix:      str,
    temp_dir:         Path,
    channel_id:       str        = "default",
    session:          str        = "",
    intro_clips_dir:  Path | None = None,
    intro_duration:   float      = 90.0,
    skip_intro:       bool       = False,
) -> tuple[Path, str]:
    """
    Для каждого сегмента подобрать лучший клип из библиотеки через ClipSelector.

    Intro-клипы (первые ~intro_duration секунд) обрезаются и кладутся в
    output_dir/intro_clips/. Если intro.mp4 не найден — предупреждение.

    Main-клипы симлинкуются в temp_dir/lib_clips/.

    Возвращает (новый clips_dir, clip_prefix) для main-клипов.
    """
    if not _LIBRARY_OK or _select_clips is None:
        log("⚠ Библиотека недоступна — используются клипы сессии")
        return clips_dir, clip_prefix

    log("\n--- Подбор клипов из библиотеки (ClipSelector) ---")
    lib_dir = temp_dir / "lib_clips"
    lib_dir.mkdir(parents=True, exist_ok=True)
    prefix  = "clip_"

    result = _select_clips(
        session=session or "unknown",
        channel_id=channel_id,
        segments=segments,
        max_repeats_in_video=10,
        max_from_prev=20,
        intro_duration=intro_duration,
    )

    intro_clips_list: list = result.get("intro_clips", [])
    main_clips_list:  list = result.get("main_clips",  [])
    intro_total: float     = result.get("intro_duration", 0.0)
    main_total:  float     = result.get("main_duration",  0.0)

    log(f"\n  Интро:    {len(intro_clips_list)} клипов ({intro_total:.1f}s)")
    log(f"  Основное: {len(main_clips_list)} клипов ({main_total:.1f}s)")

    # ── Симлинк/копирование main-клипов ──────────────────────────────────────
    matched_main = 0
    for seg_id, clip_id, seg_dur in main_clips_list:
        dst = lib_dir / f"{prefix}{int(seg_id):03d}.mp4"
        if clip_id is None:
            log(f"  [{int(seg_id):03d}] ⚠ main клип не найден")
            continue
        src = LIBRARY_CLIPS / f"{clip_id}.mp4"
        if not src.exists():
            log(f"  [{int(seg_id):03d}] ⚠ файл не найден: {src.name}")
            continue
        if dst.exists():
            dst.unlink()
        try:
            dst.symlink_to(src)
        except OSError:
            shutil.copy2(src, dst)
        log(f"  [{int(seg_id):03d}] MAIN → {clip_id}.mp4  (seg={seg_dur:.1f}s)")
        matched_main += 1

    log(f"✅ Main: {matched_main}/{len(main_clips_list)} клипов")

    # ── Интро-клипы ───────────────────────────────────────────────────────────
    if not skip_intro and intro_clips_list:
        intro_out_dir = intro_clips_dir or (temp_dir.parent / "intro_clips")
        intro_out_dir.mkdir(parents=True, exist_ok=True)

        log(f"\n  Генерация интро-клипов → {intro_out_dir}")
        matched_intro = 0
        for idx, (seg_id, clip_id, seg_dur) in enumerate(intro_clips_list, 1):
            dst = intro_out_dir / f"intro_{idx:03d}.mp4"
            if clip_id is None:
                log(f"  [{int(seg_id):03d}] ⚠ intro клип не найден")
                continue
            src = LIBRARY_CLIPS / f"{clip_id}.mp4"
            if not src.exists():
                log(f"  [{int(seg_id):03d}] ⚠ файл не найден: {src.name}")
                continue
            if _trim_clip_nvenc(src, dst, seg_dur):
                log(f"  [{int(seg_id):03d}] INTRO → {clip_id}.mp4 ({seg_dur:.1f}s) → {dst.name}")
                matched_intro += 1
            else:
                log(f"  [{int(seg_id):03d}] ⚠ обрезка не удалась: {clip_id}")

        log(f"✅ Intro: {matched_intro}/{len(intro_clips_list)} клипов → {intro_out_dir}")

        # Проверить наличие intro.mp4 (лежит на уровень выше intro_clips/)
        intro_mp4 = intro_out_dir.parent / "intro.mp4"
        if intro_mp4.exists():
            log(f"✅ intro.mp4 найден: {intro_mp4}")
        else:
            log(
                f"\n⚠️  intro.mp4 НЕ НАЙДЕН: {intro_mp4}\n"
                f"   Интро-клипы сохранены в {intro_out_dir}\n"
                f"   Соберите intro.mp4 вручную или запустите с --skip-intro-clips\n"
            )

    log(f"\n✅ Подобрано {matched_main}/{len(segments)} main-клипов из библиотеки")
    return lib_dir, prefix


# ─── Main ─────────────────────────────────────────────────────────────────────

def main() -> None:
    parser = argparse.ArgumentParser(description="Assembler — полный монтаж сессии")
    parser.add_argument("--session",      help="Имя сессии (по умолчанию: последняя)")
    parser.add_argument("--no-subs",      action="store_true", help="Без субтитров")
    parser.add_argument("--no-music",     action="store_true", help="Без фоновой музыки")
    parser.add_argument("--channel",      help="ID канала (напр. channel_001_cosmos_de). "
                                               "По умолчанию — активный канал из active_channel.json")
    parser.add_argument("--use-library",  action="store_true",
                        help=f"Подобрать клипы из библиотеки {'✅' if _LIBRARY_OK else '❌ (library.json не найден)'}")
    parser.add_argument("--intro-duration", type=float, default=90.0,
                        help="Длительность интро в секундах (default: 90)")
    parser.add_argument("--skip-intro-clips", action="store_true",
                        help="Не генерировать папку intro_clips/ (только main клипы)")
    args = parser.parse_args()

    t0 = time.time()
    log("=" * 58)
    log("ASSEMBLER — ПОЛНЫЙ МОНТАЖ ВИДЕО")
    log("=" * 58)

    # ─── 0. Канал ─────────────────────────────────────────────────────────────
    channel_cfg   = None
    intro_enabled = True

    # Определяем channel_id первым — он нужен для всех путей
    if args.channel:
        channel_id = args.channel
        if _CHANNEL_MANAGER_OK:
            set_active_channel(args.channel)
    elif _CHANNEL_MANAGER_OK:
        channel_cfg = get_active_channel()
        channel_id  = channel_cfg.get("id", "channel_001_cosmos_de") if channel_cfg else "channel_001_cosmos_de"
    else:
        channel_id = "channel_001_cosmos_de"

    # Загрузить конфиг канала если ещё не загружен
    if channel_cfg is None and _CHANNEL_MANAGER_OK:
        channel_cfg = get_active_channel()

    if channel_cfg:
        ch_name = f"{channel_cfg.get('emoji', '')} {channel_cfg.get('name', channel_cfg.get('id', '?'))}".strip()
        ch_lang = channel_cfg.get("language", "?")
        log(f"Канал:     {ch_name}  [{ch_lang}]")
        asm_cfg = channel_cfg.get("assembler", {})
        music_dir_override = asm_cfg.get("music_dir", "").strip()
        if music_dir_override and Path(music_dir_override).exists():
            os.environ["MUSIC_DIR"] = music_dir_override
            log(f"Музыка:    {music_dir_override}  (из конфига канала)")
        intro_enabled = asm_cfg.get("intro_enabled", True)
        if not intro_enabled:
            log("Интро:     отключено в конфиге канала")
    else:
        log(f"Канал:     {channel_id}")

    # ─── 0б. Стиль канала ─────────────────────────────────────────────────────
    ch_style = get_channel_style(channel_id) if _STYLE_OK else {}
    _intro_trans_fn  = get_intro_transition_fn(ch_style) if _STYLE_OK else intro_to_main_transition
    _clip_trans_name = ch_style.get("clip_transition", "slideleft")
    _clip_trans_dur  = ch_style.get("clip_transition_duration", SLIDE_DURATION)
    _intro_trans_dur = ch_style.get("clip_transition_duration", GLITCH_DURATION)
    _sfx_enabled     = ch_style.get("sfx_enabled", False) and _SFX_OK
    _sfx_chance      = ch_style.get("sfx_whoosh_chance", 0.0)
    _sub_size_offset = ch_style.get("subtitle_size_offset", 0)
    _sub_rise_extra  = ch_style.get("subtitle_rise_extra_px", 0)
    _sub_style       = ch_style.get("subtitle_style", "default")
    if ch_style:
        log(f"Стиль:     intro={ch_style.get('intro_transition','?')}  "
            f"clips={_clip_trans_name}({_clip_trans_dur:.2f}s)  "
            f"sfx={'✓' if _sfx_enabled else '✗'}")

    # ─── 1. Сессия ────────────────────────────────────────────────────────────
    session = args.session or get_last_session(channel_id)
    if not session:
        log(f"✗ Нет сессий для канала {channel_id}")
        sys.exit(1)
    log(f"Сессия:    {session}")

    ensure_session_dirs(channel_id, session)
    print_session_structure(channel_id, session)
    output_dir = get_output_dir(channel_id, session)

    # ─── 2. Сегменты ──────────────────────────────────────────────────────────
    log("\n--- Загрузка данных ---")
    try:
        segments, audio_total_dur = load_segments(get_result_json(channel_id, session))
    except FileNotFoundError as e:
        log(f"✗ {e}"); sys.exit(1)
    log(f"Сегментов: {len(segments)}, озвучка: {audio_total_dur:.1f}s")

    # ─── 3. Озвучка ───────────────────────────────────────────────────────────
    voiceover_path = get_audio_path(channel_id, session)
    if not voiceover_path:
        log(f"✗ Озвучка не найдена в {get_input_dir(channel_id, session)}"); sys.exit(1)
    log(f"Озвучка:   {voiceover_path.name}")

    # ─── 3б. Rhythm-based timing: silence detection + transition sequence ────────
    log("⏳ Анализ тишины для rhythm-based timing...")
    _silence_regions = detect_silence_regions(voiceover_path)
    if _silence_regions:
        log(f"  Зоны тишины: {len(_silence_regions)} (rhythm-aware durations активны)")
    else:
        log("  Зоны тишины: не найдены (base durations)")

    # ─── 4. Интро ─────────────────────────────────────────────────────────────
    intro_path = find_intro(channel_id, session) if intro_enabled else None
    intro_dur  = 0.0
    if intro_path:
        try:
            intro_dur = get_media_duration(intro_path)
            log(f"Интро:     {intro_path.name} ({intro_dur:.1f}s) — вставляется ЦЕЛИКОМ")
        except Exception as e:
            log(f"⚠ Интро: ошибка ffprobe — {e}")
            intro_path = None
    else:
        log("Интро:     не найдено")

    # ─── 5. Клипы ─────────────────────────────────────────────────────────────
    # Дефолтная папка клипов (используется только без --use-library)
    clips_dir   = output_dir / "temp" / "lib_clips"
    clip_prefix = "clip_"

    # ─── 5б. Библиотека клипов ────────────────────────────────────────────────
    _temp_dir_early = output_dir / "temp"
    _temp_dir_early.mkdir(parents=True, exist_ok=True)
    if _LIBRARY_OK:
        clips_dir, clip_prefix = resolve_library_clips(
            segments, clips_dir, clip_prefix, _temp_dir_early,
            channel_id=channel_id,
            session=session,
            intro_clips_dir=get_intro_clips_dir(channel_id, session),
            intro_duration=args.intro_duration,
            skip_intro=args.skip_intro_clips,
        )

    # ─── 5в. Стоп если нет интро (после генерации intro_clips) ───────────────
    if intro_enabled and not intro_path:
        intro_clips_dir = get_intro_clips_dir(channel_id, session)
        session_dir = get_session_dir(channel_id, session)
        log(f"\n📁 Интро-клипы готовы: {intro_clips_dir}")
        log(f"   Смонтируй intro.mp4 и положи в: {session_dir}")
        log(f"   Затем запусти снова: py agents/assembler/assembler.py --channel {channel_id}")
        sys.exit(0)

    # ─── 5а. Определяем сцены для montage.py (без интро-сегментов) ───────────
    # Сегменты, для которых НЕТ клипа → будут покрыты интро
    missing_ids = {
        int(seg["id"]) for seg in segments
        if not (clips_dir / f"{clip_prefix}{int(seg['id']):03d}.mp4").exists()
    }

    if missing_ids:
        log(f"\nСегменты без клипа: {sorted(missing_ids)[:10]}"
            f"{'...' if len(missing_ids) > 10 else ''} ({len(missing_ids)} шт.)")
        if intro_path:
            log(f"→ Они будут покрыты интро ({intro_path.name}) одним куском")
        else:
            log("⚠ Интро не найдено — эти сегменты будут пропущены")

    # Сцены для montage.py = все сегменты МИНУС те, что будут в интро
    segments_for_montage = [
        seg for seg in segments
        if int(seg["id"]) not in missing_ids
    ]

    # Длительность видеоряда:
    # Интро занимает ровно intro_dur секунд видео. С учётом глитч-перехода
    # (xfade overlap = GLITCH_DURATION), клипам нужно покрыть:
    #   scenes_vo_dur = audio_total_dur - intro_dur + GLITCH_DURATION
    # Тогда video_with_intro = intro_dur + scenes_vo_dur - GLITCH_DURATION = audio_total_dur ✓
    # Пропорция слов давала погрешность → стоп-кадр в конце.
    if missing_ids and intro_path and segments_for_montage:
        scenes_vo_dur = audio_total_dur - intro_dur + GLITCH_DURATION
        log(f"\nДлительность клипов: {audio_total_dur:.1f}s − {intro_dur:.1f}s + {GLITCH_DURATION:.1f}s = {scenes_vo_dur:.1f}s")
    else:
        scenes_vo_dur = audio_total_dur

    # ─── 6. Обрезка клипов по пропорциональному таймингу ─────────────────────
    log("\n--- Обрезка клипов ---")
    temp_dir = output_dir / "temp"
    temp_dir.mkdir(parents=True, exist_ok=True)

    if not segments_for_montage:
        log("Все сегменты покрыты интро — клипы не нарезаем")
        trimmed_clips: list[Path] = []
    else:
        N = len(segments_for_montage)
        # Компенсация переходов внутри видеоряда клипов:
        #   concat_all_with_transitions делает (N-1) xfade-переходов (slide)
        #   каждый съедает SLIDE_DURATION секунд из суммы клипов
        #   глитч (intro→clip0) — отдельный xfade, не влияет на clips_total:
        #   он откусывает от intro, а не от клипов
        n_slides   = max(N - 1, 0)
        trans_loss = n_slides * _clip_trans_dur   # используем реальный transition duration канала
        clips_total = scenes_vo_dur + trans_loss
        log(f"Компенсация: слайды={n_slides}×{_clip_trans_dur}s = +{trans_loss:.1f}s → {clips_total:.1f}s")
        log(f"Нарезка {N} клипов ...")
        trimmed_clips = prepare_trimmed_clips(
            segments_for_montage, clips_dir, clip_prefix, clips_total, temp_dir,
        )
        if not trimmed_clips:
            log("✗ Нет обрезанных клипов"); sys.exit(1)
        log(f"✓ Нарезано: {len(trimmed_clips)} клипов")


    # ─── 6б. Transition sequence (rhythm-based + variety) ────────────────────
    # Compute raw transition timestamps from segment structure
    _raw_trans_times = compute_transition_times(
        segments_for_montage, intro_dur, _clip_trans_dur
    ) if _SFX_OK else []

    # Anticipation shift: pull transitions slightly earlier when silence is approaching
    # Simulates human editor intuition — cut before the breath, not on it
    _raw_trans_times = _apply_anticipation(_raw_trans_times, _silence_regions)

    # Build per-boundary sequence with silence-aware durations + flash frames
    _trans_sequence: list[dict] = []
    if trimmed_clips:
        _trans_sequence = _build_trans_sequence(
            n_clips         = len(trimmed_clips),
            base_trans      = _clip_trans_name,
            base_dur        = _clip_trans_dur,
            trans_times     = _raw_trans_times,
            silence_regions = _silence_regions,
        )
        _flash_count = sum(1 for t in _trans_sequence if t["type"] in ("fadewhite", "fadeblack"))
        log(f"Transitions:  {len(_trans_sequence)} границ  |  "
            f"flash={_flash_count}  |  "
            f"rhythm={'✓' if _silence_regions else '✗'}")

    # ─── 7–10. Параллельная подготовка ────────────────────────────────────────
    log("\n--- Параллельная подготовка ---")
    parallel_start = time.time()

    videotrack_path  = temp_dir / "videotrack.mp4"
    mixed_audio_path = output_dir / "mixed_audio.aac"
    result_json      = get_result_json(channel_id, session)
    ass_path         = get_ass_path(channel_id, session)
    font_name        = (
        Path(ORGANETTO_FONT_PATH).stem
        if Path(ORGANETTO_FONT_PATH).exists()
        else "Organetto"
    )

    # Применяем поправки из стиля канала к субтитрам
    _eff_font_size = SUBTITLE_FONT_SIZE + _sub_size_offset
    _eff_rise_px   = SUBTITLE_RISE_PX  + _sub_rise_extra

    log("⏳ Запуск параллельных потоков: ASS + видеоряд + аудио ...")
    with concurrent.futures.ThreadPoolExecutor(max_workers=3) as ex:
        if not args.no_subs:
            if _sub_style == "karaoke":
                future_ass = ex.submit(
                    generate_karaoke_ass,
                    str(result_json), str(ass_path),
                    font_name, _eff_font_size,
                    SUBTITLE_FADE_IN_MS, _eff_rise_px,
                    intro_dur,
                )
            else:
                future_ass = ex.submit(
                    generate_ass,
                    str(result_json), str(ass_path),
                    font_name, _eff_font_size,
                    SUBTITLE_FADE_IN_MS, SUBTITLE_FADE_OUT_MS, _eff_rise_px,
                    intro_dur,
                )
        else:
            future_ass = None

        if trimmed_clips:
            future_video = ex.submit(
                concat_all_with_transitions,
                trimmed_clips,
                videotrack_path,
                _clip_trans_name,
                _clip_trans_dur,
                _trans_sequence or None,   # per-boundary sequence with rhythm+variety
            )
        else:
            future_video = None

        # ─── Аудио (параллельно с видеорядом) ────────────────────────────────
        _audio_start_ref = [time.time()]

        def _prepare_audio_parallel():
            _v_out   = output_dir / "voice_track.aac"
            _m_out   = output_dir / "music_track.aac"
            _i_audio = temp_dir / "intro_audio.aac"
            _has_ia  = False

            prepare_voice_track(voiceover_path, _v_out)

            if intro_path and intro_path.exists():
                try:
                    subprocess.run([
                        "ffmpeg", "-y", "-i", str(intro_path),
                        "-t", "90", "-vn",
                        "-c:a", "aac", "-b:a", "192k", "-ar", "44100",
                        str(_i_audio)
                    ], check=True, capture_output=True)
                    _has_ia = _i_audio.exists() and _i_audio.stat().st_size > 1000
                except subprocess.CalledProcessError:
                    pass

            _music_res = None
            if not args.no_music:
                _dur = get_media_duration(str(_v_out))
                _music_res = prepare_music_track(
                    video_duration=_dur,
                    output_path=_m_out,
                    music_start=MUSIC_START_SEC,
                )

            _mv = 10 ** (MUSIC_DB / 20)
            _iv = 10 ** (INTRO_AUDIO_DB / 20)
            final_mix(
                voice_path=_v_out,
                music_path=_m_out if _music_res else None,
                output_path=mixed_audio_path,
                music_start=MUSIC_START_SEC,
                music_vol=_mv,
                intro_audio_path=str(_i_audio) if _has_ia else None,
                intro_vol=_iv,
            )

            if _sfx_enabled:
                _sfx_out   = output_dir / "mixed_audio_sfx.aac"
                _sfx_types = [b["type"] for b in _trans_sequence] if _trans_sequence else []
                return inject_sfx(
                    audio_path       = mixed_audio_path,
                    output_path      = _sfx_out,
                    transition_times = _raw_trans_times,
                    sfx_enabled      = True,
                    whoosh_chance    = _sfx_chance,
                    channel_id       = channel_id,
                    transition_types = _sfx_types or ([_clip_trans_name] * len(_raw_trans_times)),
                )
            return mixed_audio_path

        future_audio = ex.submit(_prepare_audio_parallel)

        ass_ok = False
        if future_ass:
            try:
                ass_result = future_ass.result()
                ass_ok = ass_result is not None
            except Exception as e:
                log(f"✗ generate_ass: {e}")

        video_ok = True
        if future_video:
            try:
                video_ok = bool(future_video.result())
            except Exception as e:
                log(f"✗ concat_transitions: {e}")
                video_ok = False

        try:
            _audio_result = future_audio.result()
            if _sfx_enabled and _audio_result:
                mixed_audio_path = _audio_result
            log(f"  ⏱️ Аудио: {time.time() - _audio_start_ref[0]:.1f}s")
        except Exception as e:
            log(f"✗ audio_parallel: {e}")

    log(f"  ⏱️ {time.time() - parallel_start:.1f}s  "
        f"ASS: {'✓' if ass_ok else '✗'}  "
        f"Видео: {'✓' if video_ok else '✗'}")

    # ─── 8. Интро + видеоряд → video_with_intro ───────────────────────────────
    step_start = time.time()
    log("\n--- Интро + видеоряд ---")
    video_with_intro = output_dir / "video_with_intro.mp4"

    if intro_path and videotrack_path.exists():
        log(f"⏳ Переход интро → видеоряд [{ch_style.get('intro_transition','default')}] ...")
        if not _intro_trans_fn(intro_path, videotrack_path, video_with_intro, _intro_trans_dur):
            log("⚠ fadeblack не удался — простая склейка")
            concat_txt = temp_dir / "intro_concat.txt"
            with open(concat_txt, "w", encoding="utf-8") as f:
                f.write(f"file '{intro_path.as_posix()}'\n")
                f.write(f"file '{videotrack_path.as_posix()}'\n")
            result = subprocess.run(
                ["ffmpeg", "-y", "-f", "concat", "-safe", "0", "-i", str(concat_txt)]
                + _video_encode_args()
                + ["-pix_fmt", "yuv420p", "-an", "-loglevel", "warning", str(video_with_intro)]
            )
            if result.returncode != 0:
                log("✗ Склейка не удалась"); sys.exit(1)
    elif intro_path and not videotrack_path.exists():
        log("Видеоряд = только интро")
        shutil.copy2(str(intro_path), str(video_with_intro))
    elif videotrack_path.exists():
        shutil.copy2(str(videotrack_path), str(video_with_intro))
    else:
        log("✗ Нет ни интро, ни видеоряда!"); sys.exit(1)

    log(f"  ⏱️ Интро→видеоряд: {time.time() - step_start:.1f}s")

    # ─── Паддинг ──────────────────────────────────────────────────────────────
    try:
        video_dur = get_media_duration(video_with_intro)
    except Exception:
        video_dur = audio_total_dur
    log(f"✓ video_with_intro.mp4: {video_dur:.1f}s ({video_dur / 60:.1f} мин)")

    if video_dur < audio_total_dur - 0.3:
        gap = audio_total_dur - video_dur + 0.1
        log(f"⚠ Видео ({video_dur:.1f}s) короче озвучки ({audio_total_dur:.1f}s) — паддинг +{gap:.2f}s (freeze)")
        padded = output_dir / "video_with_intro_padded.mp4"
        pad_cmd = [
            "ffmpeg", "-y", "-i", str(video_with_intro),
            "-vf", f"tpad=stop_mode=clone:stop_duration={gap:.3f}",
            "-c:v", "libx264", "-preset", "fast", "-pix_fmt", "yuv420p",
            "-an", "-loglevel", "warning", str(padded),
        ]
        if subprocess.run(pad_cmd).returncode == 0 and padded.exists():
            video_with_intro.unlink(missing_ok=True)
            padded.rename(video_with_intro)
            video_dur = get_media_duration(video_with_intro)
            log(f"✓ После паддинга: {video_dur:.1f}s ({video_dur / 60:.1f} мин)")
        else:
            log("⚠ Паддинг не удался — продолжаем без него")
    else:
        log(f"✓ Длина видео {video_dur:.1f}s ≥ озвучки {audio_total_dur:.1f}s — паддинг не нужен")

    # ─── 9. Финальный экспорт (цвет + субтитры + аудио одной командой) ──────────
    step_start = time.time()
    log("\n--- Финальный экспорт ---")
    final_output = get_final_video(channel_id, session)

    # ── Цветокоррекция по стилю канала (начинается с 1:30, после интро) ────────
    def _get_color_grade_filter(color_grade: str, start_sec: float = 90) -> str:
        _ce = f"enable='gte(t,{start_sec})'"
        if color_grade == "cool_cinematic":
            return (
                f"eq=contrast=1.08:saturation=0.95:gamma=1.05:brightness=0.00:{_ce},"
                f"colorbalance="
                f"rs=-0.06:gs=-0.02:bs=0.08:"
                f"rm=-0.03:gm=-0.01:bm=0.04:"
                f"rh=-0.05:gh=-0.01:bh=0.06:{_ce},"
                f"vignette=PI/5:{_ce}"
            )
        else:  # warm_cinematic (default)
            return (
                f"eq=contrast=1.10:saturation=1.08:gamma=0.95:brightness=0.01:{_ce},"
                f"colorbalance="
                f"rs=0.08:gs=0.03:bs=-0.06:"
                f"rm=0.04:gm=0.01:bm=-0.03:"
                f"rh=0.06:gh=0.02:bh=-0.08:{_ce},"
                f"vignette=PI/5:{_ce}"
            )

    _color_grade = ch_style.get("color_grade", "warm_cinematic")
    color_filter = _get_color_grade_filter(_color_grade)
    log(f"Цветокоррекция: {_color_grade}")

    # ── ASS субтитры ──────────────────────────────────────────────────────────
    ass_filter = None
    if not args.no_subs and ass_ok and ass_path.exists():
        ass_posix = ass_path.as_posix()
        if len(ass_posix) >= 2 and ass_posix[1] == ":":
            ass_posix = ass_posix[0] + "\\:" + ass_posix[2:]
        font_dir = ""
        if Path(ORGANETTO_FONT_PATH).exists():
            fd = Path(ORGANETTO_FONT_PATH).parent.as_posix()
            if len(fd) >= 2 and fd[1] == ":":
                fd = fd[0] + "\\:" + fd[2:]
            font_dir = fd
        ass_filter = (f"ass='{ass_posix}':fontsdir='{font_dir}'"
                      if font_dir else f"ass='{ass_posix}'")

    # Объединить цветокоррекцию и субтитры в один -vf
    if ass_filter:
        vf_combined = f"{color_filter},{ass_filter}"
    else:
        vf_combined = color_filter

    log(f"Субтитры:     {'✓ ASS' if ass_filter else '✗ пропущены'}")
    log(f"Цвет:         ✓ {_color_grade}")
    log(f"Аудио:        {'✓ ' + mixed_audio_path.name if mixed_audio_path.exists() else '✗ нет'}")
    log(f"Кодек:        h264_nvenc → {final_output.name}")

    # ── Метаданные — реалистичные Adobe workflow (5 комбинаций) ────────────
    _metadata_flags = _make_metadata_flags()
    # Лог: найти encoder/software из флагов для отображения
    _mf_str = " / ".join(
        _metadata_flags[i + 1] for i in range(0, len(_metadata_flags), 2)
        if _metadata_flags[i] == "-metadata"
        and _metadata_flags[i + 1].startswith(("encoder=", "software="))
    )
    log(f"Метаданные:   {_mf_str[:60]}")

    # ── Финальный FFmpeg — один проход с NVDEC + NVENC ───────────────────────
    final_cmd = [
        "ffmpeg", "-y",
        "-hwaccel", "cuda",
        "-i", str(video_with_intro),
        "-i", str(mixed_audio_path),
        "-vf", vf_combined,
        "-map", "0:v:0",
        "-map", "1:a:0",
        "-c:v", "h264_nvenc",
        "-preset", "p4", "-cq", "19",
        "-pix_fmt", "yuv420p",
        "-c:a", "copy",
        "-shortest",
        "-map_metadata", "-1",
        *_metadata_flags,
        "-loglevel", "warning",
        str(final_output),
    ]

    try:
        subprocess.run(final_cmd, check=True, capture_output=True)
        log(f"  ⏱️ Финальный экспорт: {time.time() - step_start:.1f}s")
        try:
            from mutagen.mp4 import MP4
            _mp4 = MP4(str(final_output))
            _enc = next(
                (v.split("=", 1)[1] for v in _metadata_flags
                 if isinstance(v, str) and v.startswith("encoder=")),
                "Adobe Premiere Pro"
            )
            _mp4.tags["©too"] = [_enc]
            _mp4.save()
            log(f"  ✅ Метаданные: ©too = {_enc}")
        except Exception as _me:
            log(f"  ⚠ mutagen: {_me}")
    except subprocess.CalledProcessError:
        log("⚠ NVENC ошибка — CPU fallback")
        cpu_cmd = []
        i = 0
        while i < len(final_cmd):
            tok = final_cmd[i]
            if tok == "h264_nvenc":
                cpu_cmd.append("libx264")
            elif tok == "-cq" and i + 1 < len(final_cmd):
                cpu_cmd += ["-crf", final_cmd[i + 1]]; i += 1
            elif tok == "p4":
                cpu_cmd.append("fast")
            elif tok == "-hwaccel" and i + 1 < len(final_cmd):
                i += 2; continue
            elif tok == "cuda":
                i += 1; continue
            else:
                cpu_cmd.append(tok)
            i += 1
        try:
            subprocess.run(cpu_cmd, check=True)
            log(f"  ⏱️ Финальный экспорт (CPU): {time.time() - step_start:.1f}s")
        except subprocess.CalledProcessError as e:
            log(f"✗ Финальный экспорт не удался: {e}")
            if video_with_intro.exists():
                shutil.copy2(str(video_with_intro), str(final_output))

    # ─── Очистка промежуточных файлов ─────────────────────────────────────────
    if temp_dir.exists():
        shutil.rmtree(temp_dir, ignore_errors=True)
        log("✓ Временные файлы удалены")

    # ─── Готово ───────────────────────────────────────────────────────────────
    elapsed = time.time() - t0
    m, s    = divmod(int(elapsed), 60)

    log("")
    log("=" * 58)
    log(f"✅ ГОТОВО! Время: {m}м {s}с")
    log(f"📁 {final_output}")
    if final_output.exists():
        size_mb = final_output.stat().st_size / 1024 / 1024
        log(f"📦 Размер: {size_mb:.1f} МБ")
    log("=" * 58)


if __name__ == "__main__":
    main()
