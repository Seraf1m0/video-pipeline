#!/usr/bin/env python3
"""
assembler.py — полный монтаж видео для сессии Video Pipeline

Пайплайн:
  1.  Найти последнюю сессию (или --session)
  2.  Загрузить сегменты из data/transcripts/{session}/result.json
  3.  Найти озвучку в data/input/{session}/*.mp3
  4.  Найти интро в data/media/{session}/videos_upscaled/Intro_*.mp4
  5.  Определить папку клипов (clips_upscaled → clips → videos_upscaled → videos)
  6.  Собрать project.json для montage.py (только сцены БЕЗ интро)
  7.  Запустить montage.py → scenes_video.mp4  (без интро, video-only)
  8.  Если есть интро: склеить intro + scenes_video → montage_base.mp4
  9.  Подобрать 3–5 треков из data/music/ и собрать музыкальный трек
      (fade-in 2s на 1:28, crossfade 3s между треками, fade-out 4s в конце)
  10. Смикшировать аудио: озвучка -6 dB / интро -37 dB / музыка -33 dB
       → montage_with_audio.mp4
  11. Сгенерировать word-level SRT (если не --no-subs)
  12. Наложить субтитры FFmpeg → final_complete.mp4  (1920×1080, 25 Mbps)

Запуск:
  py agents/assembler/assembler.py
  py agents/assembler/assembler.py --session Video_20260305_222350
  py agents/assembler/assembler.py --no-subs
  py agents/assembler/assembler.py --no-music
  py agents/assembler/assembler.py --no-subs --no-music
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
import time
from pathlib import Path

# ─── UTF-8 вывод ─────────────────────────────────────────────────────────────

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")

# ─── Пути ────────────────────────────────────────────────────────────────────

BASE_DIR        = Path(__file__).resolve().parent.parent.parent
DATA_DIR        = BASE_DIR / "data"
INPUT_DIR       = DATA_DIR / "input"
MEDIA_DIR       = DATA_DIR / "media"
TRANSCRIPTS_DIR = DATA_DIR / "transcripts"
OUTPUT_BASE_DIR = DATA_DIR / "output"
MUSIC_DIR       = DATA_DIR / "music"
AGENTS_DIR      = BASE_DIR / "agents"

MONTAGE_SCRIPT  = AGENTS_DIR / "assembler" / "montage.py"
SUBTITLE_SCRIPT = AGENTS_DIR / "assembler" / "subtitle_generator.py"

from dotenv import load_dotenv
load_dotenv(BASE_DIR / "config" / ".env")

# ─── Импорт переходов (transitions.py в той же папке) ────────────────────────

_ASSEMBLER_DIR = Path(__file__).resolve().parent
if str(_ASSEMBLER_DIR) not in sys.path:
    sys.path.insert(0, str(_ASSEMBLER_DIR))
from transitions     import (glitch_transition, slide_motionblur_transition,
                              intro_to_main_transition, slide_transition,
                              concat_all_with_transitions)
from audio_mixer     import (
    prepare_music_track,
    prepare_voice_track,
    final_mix,
)
from ass_generator   import generate_ass
from subtitle_burner import burn_ass
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
GLITCH_DURATION      = 0.5    # длительность глитч-перехода intro→clip1 (секунд)
SLIDE_DURATION       = 0.5    # длительность slide+blur перехода clip-clip (секунд)

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
    print(f"[АССЕМБЛЕР] {msg}", flush=True)


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


# ─── Обрезка клипов (для новых переходов) ────────────────────────────────────

def _compute_timings(segments: list, total_sec: float) -> list[dict]:
    """
    Пропорциональное распределение по количеству слов (аналог montage.py).
    Возвращает список {start, end, duration, words} для каждого сегмента.
    """
    word_counts = [
        max(len(re.findall(r"[a-zA-Zа-яА-ЯёЁ0-9']+", seg.get("text", ""))), 1)
        for seg in segments
    ]
    total_words = max(sum(word_counts), 1)

    # Пропорциональные длительности, минимум 2 секунды
    raw = [max((wc / total_words) * total_sec, 2.0) for wc in word_counts]

    # Нормализуем сумму ровно до total_sec
    s = sum(raw)
    raw = [d * total_sec / s for d in raw]

    timings: list[dict] = []
    t = 0.0
    for wc, d in zip(word_counts, raw):
        timings.append({"start": t, "end": t + d, "duration": d, "words": wc})
        t += d

    # Последний клип заканчивается ровно в total_sec
    if timings:
        timings[-1]["end"]      = total_sec
        timings[-1]["duration"] = total_sec - timings[-1]["start"]

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

    if clip_dur > 0 and target_dur <= clip_dur:
        cmd = [
            "ffmpeg", "-y", "-i", str(src),
            "-t", f"{target_dur:.3f}",
            "-vf", scale_vf,
            "-c:v", "libx264", "-preset", "fast",
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
        cmd = [
            "ffmpeg", "-y",
            "-f", "concat", "-safe", "0",
            "-i", str(concat_txt),
            "-t", f"{target_dur:.3f}",
            "-vf", scale_vf,
            "-c:v", "libx264", "-preset", "fast",
            "-pix_fmt", "yuv420p", "-an",
            "-loglevel", "warning",
            str(dst),
        ]

    result = subprocess.run(cmd)
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
        dst = temp_dir / f"trimmed_{int(seg['id']):03d}.mp4"
        tasks.append((src, dst, timing["duration"], i + 1, len(segments)))

    if not tasks:
        return []

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

    log(f"  ⏱️ Обрезка: {time.time() - t_trim:.1f}s — {len(trimmed)}/{len(tasks)} клипов")
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

def find_latest_session() -> str | None:
    """Найти последнюю сессию Video_YYYYMMDD_HHMMSS в data/media/."""
    if not MEDIA_DIR.exists():
        return None
    dirs = [
        d for d in MEDIA_DIR.iterdir()
        if d.is_dir() and re.match(r"^Video_\d{8}_\d{6}$", d.name)
    ]
    if not dirs:
        return None
    dirs.sort(key=lambda d: d.name, reverse=True)
    return dirs[0].name


def find_voiceover(session: str) -> Path | None:
    """Найти озвучку (MP3/WAV) в data/input/{session}/. Исключает *.mp4."""
    audio_dir = INPUT_DIR / session
    if not audio_dir.exists():
        return None
    for ext in ("*.mp3", "*.wav", "*.ogg", "*.m4a", "*.aac"):
        files = sorted(audio_dir.glob(ext))
        if files:
            return files[0]
    return None


def find_intro(session: str) -> Path | None:
    """
    Найти интро-видео (Intro_*.mp4 / intro*.mp4) в порядке приоритета:
      1. data/input/{session}/
      2. data/media/{session}/
      3. data/media/{session}/videos/
      4. data/media/{session}/videos_upscaled/
      5. data/media/{session}/clips/
      6. data/media/{session}/clips_upscaled/
    """
    search_dirs = [
        INPUT_DIR  / session,
        MEDIA_DIR  / session,
        MEDIA_DIR  / session / "upscaled",
        MEDIA_DIR  / session / "videos",
        MEDIA_DIR  / session / "videos_upscaled",
        MEDIA_DIR  / session / "clips",
        MEDIA_DIR  / session / "clips_upscaled",
    ]
    patterns = ("Intro_*.mp4", "Intro*.mp4", "intro_*.mp4", "intro*.mp4", "INTRO*.mp4")
    for folder in search_dirs:
        if not folder.exists():
            continue
        for pattern in patterns:
            files = sorted(folder.glob(pattern))
            if files:
                return files[0]
    return None


def find_clips_dir(session: str) -> tuple[Path, str]:
    """
    Возвращает (папка_с_клипами, префикс_файла).
    Приоритет: clips_upscaled/ → clips/ → videos_upscaled/ → videos/
    Исключает Intro_*.mp4 из подсчёта.
    """
    candidates = [
        (MEDIA_DIR / session / "clips_upscaled", "clip_"),
        (MEDIA_DIR / session / "clips",          "clip_"),
        (MEDIA_DIR / session / "upscaled",       "video_"),
        (MEDIA_DIR / session / "videos_upscaled","video_"),
        (MEDIA_DIR / session / "videos",         "video_"),
    ]
    for path, prefix in candidates:
        if path.exists() and any(path.glob(f"{prefix}*.mp4")):
            count = len(list(path.glob(f"{prefix}*.mp4")))
            log(f"Клипы: {path.name}/ ({count} шт., prefix='{prefix}')")
            return path, prefix
    fallback = MEDIA_DIR / session / "videos"
    log("Клипы: videos/ [ни один клип не найден!]")
    return fallback, "video_"


def load_segments(session: str) -> tuple[list, float]:
    """Загрузить сегменты из result.json."""
    result_json = TRANSCRIPTS_DIR / session / "result.json"
    if not result_json.exists():
        raise FileNotFoundError(f"result.json не найден: {result_json}")
    with open(result_json, encoding="utf-8") as f:
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
    На стыке интро/сцен — глитч-переход (pixelize, GLITCH_DURATION сек).
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
            f"[v0][v1]xfade=transition=pixelize:duration={GLITCH_DURATION}:offset={glitch_offset:.3f}[vout]"
        )
        log(f"Глитч-переход: pixelize {GLITCH_DURATION}s на {glitch_offset:.1f}s")
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


# ─── Main ─────────────────────────────────────────────────────────────────────

def main() -> None:
    parser = argparse.ArgumentParser(description="Assembler — полный монтаж сессии")
    parser.add_argument("--session",  help="Имя сессии (по умолчанию: последняя)")
    parser.add_argument("--no-subs",  action="store_true", help="Без субтитров")
    parser.add_argument("--no-music", action="store_true", help="Без фоновой музыки")
    args = parser.parse_args()

    t0 = time.time()
    log("=" * 58)
    log("ASSEMBLER — ПОЛНЫЙ МОНТАЖ ВИДЕО")
    log("=" * 58)

    # ─── 1. Сессия ────────────────────────────────────────────────────────────
    session = args.session or find_latest_session()
    if not session:
        log("✗ Не найдена ни одна сессия в data/media/")
        sys.exit(1)
    log(f"Сессия:    {session}")

    output_dir = OUTPUT_BASE_DIR / session
    output_dir.mkdir(parents=True, exist_ok=True)

    # ─── 2. Сегменты ──────────────────────────────────────────────────────────
    log("\n--- Загрузка данных ---")
    try:
        segments, audio_total_dur = load_segments(session)
    except FileNotFoundError as e:
        log(f"✗ {e}"); sys.exit(1)
    log(f"Сегментов: {len(segments)}, озвучка: {audio_total_dur:.1f}s")

    # ─── 3. Озвучка ───────────────────────────────────────────────────────────
    voiceover_path = find_voiceover(session)
    if not voiceover_path:
        log(f"✗ Озвучка не найдена в data/input/{session}/"); sys.exit(1)
    log(f"Озвучка:   {voiceover_path.name}")

    # ─── 4. Интро ─────────────────────────────────────────────────────────────
    intro_path = find_intro(session)
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
    clips_dir, clip_prefix = find_clips_dir(session)

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

    # Длительность видеоряда = полное аудио минус аудио интро-сегментов
    # Аудио интро-сегментов ≈ intro_dur (по пропорции слов)
    # Для точности используем ratio: слова_интро / всего_слов * audio_total_dur
    if missing_ids and intro_path and segments_for_montage:
        def count_words(text: str) -> int:
            return max(len(re.findall(r"[a-zA-Zа-яА-ЯёЁ0-9']+", text)), 1)

        total_words   = sum(count_words(s.get("text", "")) for s in segments)
        intro_words   = sum(count_words(s.get("text", "")) for s in segments if int(s["id"]) in missing_ids)
        intro_vo_dur  = (intro_words / total_words) * audio_total_dur if total_words else 0
        scenes_vo_dur = audio_total_dur - intro_vo_dur
        log(f"\nРазбивка по озвучке: интро ~{intro_vo_dur:.1f}s, сцены ~{scenes_vo_dur:.1f}s")
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
        # Компенсация переходов:
        #   глитч (intro→clip0): GLITCH_DURATION  (только если есть интро)
        #   слайды (clip-clip):  (N-2 если intro, N-1 если нет) × SLIDE_DURATION
        n_glitch  = 1 if (intro_path and N >= 1) else 0
        n_slides  = max(N - 1 - n_glitch, 0)
        trans_loss = n_glitch * GLITCH_DURATION + n_slides * SLIDE_DURATION
        clips_total = scenes_vo_dur + trans_loss
        log(f"Компенсация: глитч={n_glitch}×{GLITCH_DURATION}s + слайды={n_slides}×{SLIDE_DURATION}s = +{trans_loss:.1f}s → {clips_total:.1f}s")
        log(f"Нарезка {N} клипов ...")
        trimmed_clips = prepare_trimmed_clips(
            segments_for_montage, clips_dir, clip_prefix, clips_total, temp_dir,
        )
        if not trimmed_clips:
            log("✗ Нет обрезанных клипов"); sys.exit(1)
        log(f"✓ Нарезано: {len(trimmed_clips)} клипов")

    # ─── 7–10. Параллельная подготовка ────────────────────────────────────────
    log("\n--- Параллельная подготовка ---")
    parallel_start = time.time()

    videotrack_path  = temp_dir / "videotrack.mp4"
    mixed_audio_path = output_dir / "mixed_audio.aac"
    result_json      = TRANSCRIPTS_DIR / session / "result.json"
    ass_path         = output_dir / "subtitles_animated.ass"
    font_name        = (
        Path(ORGANETTO_FONT_PATH).stem
        if Path(ORGANETTO_FONT_PATH).exists()
        else "Organetto"
    )

    log("⏳ Запуск параллельных потоков: ASS + видеоряд ...")
    with concurrent.futures.ThreadPoolExecutor(max_workers=2) as ex:
        if not args.no_subs:
            future_ass = ex.submit(
                generate_ass,
                str(result_json), str(ass_path),
                font_name, SUBTITLE_FONT_SIZE,
                SUBTITLE_FADE_IN_MS, SUBTITLE_FADE_OUT_MS, SUBTITLE_RISE_PX,
            )
        else:
            future_ass = None

        if trimmed_clips:
            future_video = ex.submit(
                concat_all_with_transitions,
                trimmed_clips,
                videotrack_path,
                "slideleft",
                SLIDE_DURATION,
            )
        else:
            future_video = None

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

    log(f"  ⏱️ {time.time() - parallel_start:.1f}s  "
        f"ASS: {'✓' if ass_ok else '✗'}  "
        f"Видео: {'✓' if video_ok else '✗'}")

    # ─── Аудио (последовательно) ───────────────────────────────────────────────
    log("\n--- Подготовка аудио ---")
    audio_start = time.time()
    voice_out   = output_dir / "voice_track.aac"
    music_out   = output_dir / "music_track.aac"
    intro_audio = temp_dir / "intro_audio.aac"

    # 1. Подготовить озвучку
    log("⏳ Подготовка озвучки...")
    prepare_voice_track(voiceover_path, voice_out)

    # 2. Извлечь аудио интро если есть
    has_intro_audio = False
    if intro_path and intro_path.exists():
        try:
            subprocess.run([
                "ffmpeg", "-y",
                "-i", str(intro_path),
                "-t", "90", "-vn",
                "-c:a", "aac", "-b:a", "192k",
                "-ar", "44100",
                str(intro_audio)
            ], check=True, capture_output=True)
            has_intro_audio = (
                intro_audio.exists()
                and intro_audio.stat().st_size > 1000
            )
            if has_intro_audio:
                log(f"✅ Интро аудио: {get_media_duration(str(intro_audio)):.1f}s")
        except subprocess.CalledProcessError:
            log("⚠ Не удалось извлечь аудио интро")

    # 3. Подготовить музыку
    if not args.no_music:
        video_dur_for_music = get_media_duration(str(voice_out))
        log(f"⏳ Подготовка музыки для {video_dur_for_music:.1f}s видео...")
        music_result = prepare_music_track(
            video_duration=video_dur_for_music,
            output_path=music_out,
            music_start=MUSIC_START_SEC,
        )
    else:
        music_result = None
        log("Музыка: пропущена (--no-music)")

    # 4. Финальный микс
    log("⏳ Финальный микс...")
    _music_vol = 10 ** (MUSIC_DB / 20)
    _intro_vol = 10 ** (INTRO_AUDIO_DB / 20)
    final_mix(
        voice_path=voice_out,
        music_path=music_out if music_result else None,
        output_path=mixed_audio_path,
        music_start=MUSIC_START_SEC,
        music_vol=_music_vol,
        intro_audio_path=str(intro_audio) if has_intro_audio else None,
        intro_vol=_intro_vol,
    )
    log(f"  ⏱️ Аудио: {time.time() - audio_start:.1f}s")

    # ─── 8. Интро + видеоряд → video_with_intro ───────────────────────────────
    step_start = time.time()
    log("\n--- Интро + видеоряд ---")
    video_with_intro = output_dir / "video_with_intro.mp4"

    if intro_path and videotrack_path.exists():
        log("⏳ Переход интро → видеоряд ...")
        if not intro_to_main_transition(intro_path, videotrack_path, video_with_intro, GLITCH_DURATION):
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
        gap = audio_total_dur - video_dur + 0.5
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
    final_output = output_dir / "final_complete.mp4"

    # ── Тёплая кинематичная цветокоррекция (начинается с 1:30, после интро) ──
    _ce = "enable='gte(t,90)'"
    color_filter = (
        f"eq=contrast=1.10:saturation=1.08:gamma=0.95:brightness=0.01:{_ce},"
        f"colorbalance="
        f"rs=0.08:gs=0.03:bs=-0.06:"
        f"rm=0.04:gm=0.01:bm=-0.03:"
        f"rh=0.06:gh=0.02:bh=-0.08:{_ce},"
        f"vignette=PI/5:{_ce}"
    )

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
    log(f"Цвет:         ✓ кинематичная коррекция")
    log(f"Интро аудио:  {'✓' if has_intro_audio else '✗ нет'}")
    log(f"Кодек:        h264_nvenc → {final_output.name}")

    # ── Финальный FFmpeg — аудио уже полностью готово в mixed_audio.aac ──────
    final_cmd = [
        "ffmpeg", "-y",
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
        "-loglevel", "warning",
        str(final_output),
    ]

    try:
        subprocess.run(final_cmd, check=True, capture_output=True)
        log(f"  ⏱️ Финальный экспорт: {time.time() - step_start:.1f}s")
    except subprocess.CalledProcessError:
        log("⚠ NVENC ошибка — CPU fallback")
        # h264_nvenc → libx264, preset p4 → fast, -cq → -crf
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
