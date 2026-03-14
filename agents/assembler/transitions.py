"""
transitions.py — xfade переходы между клипами через FFmpeg filter_complex.

Переходы:
  intro_to_main_transition — Radial Zoom Blur (интро → видеоряд, БЕЗ чёрного экрана)
  slide_transition         — slideleft + сильный motion blur (между клипами)
  concat_all_with_transitions — chunk-based склейка всех клипов
"""

import json
import os
import shutil
import subprocess
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path


# ── Утилиты ─────────────────────────────────────────────────────────────────

def get_audio_duration(path) -> float:
    cmd = ["ffprobe", "-v", "quiet",
           "-print_format", "json",
           "-show_format", str(path)]
    r = subprocess.run(cmd, capture_output=True, text=True)
    try:
        return float(json.loads(r.stdout)["format"]["duration"])
    except Exception:
        return 0.0


def get_video_duration(path) -> float:
    cmd = ["ffprobe", "-v", "quiet",
           "-print_format", "json",
           "-show_streams", str(path)]
    r = subprocess.run(cmd, capture_output=True, text=True)
    try:
        for s in json.loads(r.stdout).get("streams", []):
            if s.get("codec_type") == "video":
                return float(s.get("duration", 0))
    except Exception:
        pass
    return 0.0


# ── GPU детектор ─────────────────────────────────────────────────────────────

def get_gpu_encoder():
    """Определить доступный GPU-кодек (NVENC или libx264 fallback)."""
    result = subprocess.run(
        ["ffmpeg", "-hide_banner", "-encoders"],
        capture_output=True, text=True
    )
    if "h264_nvenc" in result.stdout:
        print("✅ GPU: NVIDIA NVENC")
        return "h264_nvenc", [
            "-preset", "p4",
            "-tune",   "hq",
            "-rc",     "vbr",
            "-cq",     "19",
            "-b:v",    "0",
        ]
    print("⚠️ CPU fallback: libx264")
    return "libx264", ["-preset", "fast", "-crf", "18"]


GPU_ENCODER, GPU_PARAMS = get_gpu_encoder()


# ─── ПЕРЕХОД 1: Интро → Видеоряд ─────────────────────────────────────────────
# Radial Zoom Blur БЕЗ чёрного экрана

def intro_to_main_transition(intro_path, main_path,
                              output_path, duration=0.75):
    """
    Radial Zoom Blur переход: интро зумируется вперёд с размытием,
    основной видеоряд зумируется из крупного в нормальный.
    БЕЗ чёрного экрана (dissolve между blur-зонами).
    """
    intro_path  = Path(intro_path)
    main_path   = Path(main_path)
    output_path = Path(output_path)
    output_path.parent.mkdir(parents=True, exist_ok=True)

    intro_dur = get_video_duration(str(intro_path))
    offset    = intro_dur - duration
    fps       = 25
    total_frames = int(duration * fps)

    Path("temp").mkdir(exist_ok=True)

    try:
        # Все сегменты нормализуются до {fps} fps (интро=25fps, клипы=24fps → нужно выровнять)
        fps_filter = f"fps={fps}"

        # Часть интро до зоны перехода
        intro_before = "temp/intro_before.mp4"
        subprocess.run([
            "ffmpeg", "-y", "-i", str(intro_path),
            "-t", str(offset),
            "-vf", fps_filter,
            "-c:v", GPU_ENCODER, *GPU_PARAMS,
            "-pix_fmt", "yuv420p", "-an",
            intro_before
        ], check=True, capture_output=True)

        # Зона перехода из интро
        intro_end = "temp/intro_end.mp4"
        subprocess.run([
            "ffmpeg", "-y", "-i", str(intro_path),
            "-ss", str(offset), "-t", str(duration),
            "-vf", fps_filter,
            "-c:v", GPU_ENCODER, *GPU_PARAMS,
            "-pix_fmt", "yuv420p", "-an",
            intro_end
        ], check=True, capture_output=True)

        # Зона перехода из основного (нормализация до 25fps)
        main_start = "temp/main_start.mp4"
        subprocess.run([
            "ffmpeg", "-y", "-i", str(main_path),
            "-t", str(duration),
            "-vf", fps_filter,
            "-c:v", GPU_ENCODER, *GPU_PARAMS,
            "-pix_fmt", "yuv420p", "-an",
            main_start
        ], check=True, capture_output=True)

        # Основное видео после зоны (нормализация до 25fps)
        main_after = "temp/main_after.mp4"
        subprocess.run([
            "ffmpeg", "-y", "-i", str(main_path),
            "-ss", str(duration),
            "-vf", fps_filter,
            "-c:v", GPU_ENCODER, *GPU_PARAMS,
            "-pix_fmt", "yuv420p", "-an",
            main_after
        ], check=True, capture_output=True)

        # Zoom in + blur на конце интро
        # FFmpeg 8.x: в zoompan переменная для номера кадра — 'in', не 'n'
        intro_zoomed = "temp/intro_zoomed.mp4"
        subprocess.run([
            "ffmpeg", "-y", "-i", intro_end,
            "-vf", (
                f"zoompan="
                f"z='1.001+0.299*in/{total_frames}':"
                f"x='iw/2-(iw/zoom/2)':"
                f"y='ih/2-(ih/zoom/2)':"
                f"d=1:s=1920x1080,"
                f"gblur=sigma=10"
            ),
            "-c:v", "libx264", "-preset", "fast", "-crf", "18",
            "-pix_fmt", "yuv420p", "-an", "-r", str(fps),
            intro_zoomed
        ], check=True, capture_output=True)

        # Zoom out + blur на начале основного
        main_zoomed = "temp/main_zoomed.mp4"
        subprocess.run([
            "ffmpeg", "-y", "-i", main_start,
            "-vf", (
                f"zoompan="
                f"z='1.3-0.299*in/{total_frames}':"
                f"x='iw/2-(iw/zoom/2)':"
                f"y='ih/2-(ih/zoom/2)':"
                f"d=1:s=1920x1080,"
                f"gblur=sigma=10"
            ),
            "-c:v", "libx264", "-preset", "fast", "-crf", "18",
            "-pix_fmt", "yuv420p", "-an", "-r", str(fps),
            main_zoomed
        ], check=True, capture_output=True)

        # Dissolve между двумя blur зонами
        blur_merged  = "temp/blur_merged.mp4"
        merge_dur    = duration / 2
        intro_z_dur  = get_video_duration(intro_zoomed)
        merge_offset = intro_z_dur - merge_dur
        subprocess.run([
            "ffmpeg", "-y",
            "-i", intro_zoomed,
            "-i", main_zoomed,
            "-filter_complex",
            f"[0:v][1:v]xfade=transition=dissolve:"
            f"duration={merge_dur:.3f}:"
            f"offset={merge_offset:.3f}[out]",
            "-map", "[out]",
            "-c:v", GPU_ENCODER, *GPU_PARAMS,
            "-pix_fmt", "yuv420p", "-an",
            blur_merged
        ], check=True, capture_output=True)

        # Финальная склейка
        concat_file = "temp/zoom_concat.txt"
        with open(concat_file, "w") as f:
            f.write(f"file '{os.path.abspath(intro_before)}'\n")
            f.write(f"file '{os.path.abspath(blur_merged)}'\n")
            f.write(f"file '{os.path.abspath(main_after)}'\n")
        subprocess.run([
            "ffmpeg", "-y",
            "-f", "concat", "-safe", "0",
            "-i", concat_file,
            "-c:v", GPU_ENCODER, *GPU_PARAMS,
            "-pix_fmt", "yuv420p", "-an",
            str(output_path)
        ], check=True)

        print(f"  ✅ Radial zoom blur переход: {output_path.name}")
        return True

    except subprocess.CalledProcessError as e:
        print(f"  ⚠️ Zoom blur ошибка — fallback concat")
        concat_f = "temp/fallback_concat.txt"
        with open(concat_f, "w") as f:
            f.write(f"file '{os.path.abspath(str(intro_path))}'\n")
            f.write(f"file '{os.path.abspath(str(main_path))}'\n")
        result2 = subprocess.run([
            "ffmpeg", "-y",
            "-f", "concat", "-safe", "0",
            "-i", concat_f,
            "-c:v", GPU_ENCODER, *GPU_PARAMS,
            "-pix_fmt", "yuv420p", "-an",
            str(output_path)
        ])
        return result2.returncode == 0


# ─── ПЕРЕХОД 2: Между клипами ─────────────────────────────────────────────────
# slideleft + сильный motion blur

def slide_transition(clip1_path, clip2_path,
                     output_path, duration=0.5):
    """
    slideleft с сильным motion blur в зоне перехода.
    При ошибке — fallback на чистый slideleft.
    """
    clip1_path  = Path(clip1_path)
    clip2_path  = Path(clip2_path)
    output_path = Path(output_path)
    output_path.parent.mkdir(parents=True, exist_ok=True)

    clip1_dur = get_video_duration(str(clip1_path))
    offset    = clip1_dur - duration

    filter_complex = (
        f"[0:v][1:v]xfade="
        f"transition=slideleft:"
        f"duration={duration:.3f}:"
        f"offset={offset:.3f}[slide];"
        f"[0:v]trim=start={offset:.3f},"
        f"setpts=PTS-STARTPTS,"
        f"boxblur=luma_radius=22:luma_power=1:"
        f"chroma_radius=11:chroma_power=1[blur0];"
        f"[1:v]trim=end={duration:.3f},"
        f"setpts=PTS-STARTPTS,"
        f"boxblur=luma_radius=22:luma_power=1:"
        f"chroma_radius=11:chroma_power=1[blur1];"
        f"[blur0][blur1]xfade=transition=slideleft:"
        f"duration={duration:.3f}:offset=0[blurred];"
        f"[slide]trim=start={offset:.3f},"
        f"setpts=PTS-STARTPTS[slide_zone];"
        f"[slide_zone][blurred]blend="
        f"all_expr='A*0.25+B*0.75'[zone_final];"
        f"[0:v]trim=end={offset:.3f},"
        f"setpts=PTS-STARTPTS[before];"
        f"[1:v]trim=start={duration:.3f},"
        f"setpts=PTS-STARTPTS[after];"
        f"[before][zone_final][after]"
        f"concat=n=3:v=1:a=0[out]"
    )
    cmd = [
        "ffmpeg", "-y",
        "-i", str(clip1_path),
        "-i", str(clip2_path),
        "-filter_complex", filter_complex,
        "-map", "[out]",
        "-c:v", GPU_ENCODER, *GPU_PARAMS,
        "-pix_fmt", "yuv420p",
        "-r", "25", "-an",
        "-loglevel", "warning",
        str(output_path)
    ]
    result = subprocess.run(cmd, capture_output=True, text=True)

    if result.returncode != 0:
        # Fallback — чистый slideleft
        subprocess.run([
            "ffmpeg", "-y",
            "-i", str(clip1_path),
            "-i", str(clip2_path),
            "-filter_complex",
            f"[0:v][1:v]xfade=transition=slideleft:"
            f"duration={duration:.3f}:offset={offset:.3f}[out]",
            "-map", "[out]",
            "-c:v", GPU_ENCODER, *GPU_PARAMS,
            "-pix_fmt", "yuv420p", "-r", "25", "-an",
            str(output_path)
        ], check=True)

    return True


# ─── CHUNK CONCAT ─────────────────────────────────────────────────────────────

def _simple_concat(clip_paths, output_path):
    """Простая склейка без переходов (fallback)."""
    Path("temp").mkdir(exist_ok=True)
    concat_file = "temp/simple_concat.txt"
    with open(concat_file, "w", encoding="utf-8") as f:
        for p in clip_paths:
            f.write(f"file '{os.path.abspath(str(p))}'\n")
    subprocess.run([
        "ffmpeg", "-y",
        "-f", "concat", "-safe", "0",
        "-i", concat_file,
        "-c:v", GPU_ENCODER, *GPU_PARAMS,
        "-pix_fmt", "yuv420p", "-an",
        "-loglevel", "warning",
        str(output_path),
    ], check=True)


def _concat_chunk(clip_paths, durations, output_path,
                  transition, duration):
    """Склеить один чанк клипов одной командой FFmpeg (filter_complex xfade)."""
    n = len(clip_paths)
    if n == 1:
        shutil.copy(str(clip_paths[0]), str(output_path))
        return True

    filter_parts = []
    offset = max(0.01, durations[0] - duration)
    filter_parts.append(
        f"[0:v][1:v]xfade=transition={transition}:"
        f"duration={duration:.3f}:offset={offset:.3f}[v1]"
    )
    cumulative = offset + durations[1] - duration

    for i in range(2, n):
        prev = f"v{i - 1}"
        curr = f"v{i}"
        filter_parts.append(
            f"[{prev}][{i}:v]xfade=transition={transition}:"
            f"duration={duration:.3f}:offset={max(0.01, cumulative):.3f}[{curr}]"
        )
        cumulative += durations[i] - duration

    last_label    = f"v{n - 1}"
    filter_complex = ";".join(filter_parts)

    cmd = ["ffmpeg", "-y"]
    for p in clip_paths:
        cmd += ["-i", str(p)]
    cmd += [
        "-filter_complex", filter_complex,
        "-map", f"[{last_label}]",
        "-c:v", GPU_ENCODER, *GPU_PARAMS,
        "-pix_fmt", "yuv420p",
        "-r", "25", "-an",
        "-loglevel", "warning",
        str(output_path),
    ]
    result = subprocess.run(cmd, capture_output=True, text=True)
    if result.returncode != 0:
        print(f"  ⚠️ xfade ошибка — fallback simple_concat")
        _simple_concat(clip_paths, output_path)
    return True


def concat_all_with_transitions(
    clip_paths,
    output_path,
    transition: str   = "slideleft",
    duration:   float = 0.5,
) -> bool:
    """
    Склеить все клипы с xfade-переходами за минимум FFmpeg-вызовов.
    Разбивает на чанки по 20 если клипов больше CHUNK_SIZE.
    """
    clip_paths  = [Path(p) for p in clip_paths]
    output_path = Path(output_path)

    if not clip_paths:
        return False
    if len(clip_paths) == 1:
        shutil.copy(str(clip_paths[0]), str(output_path))
        return True

    n = len(clip_paths)

    # Получить длительности параллельно
    with ThreadPoolExecutor(max_workers=8) as ex:
        durations = list(ex.map(get_video_duration, [str(p) for p in clip_paths]))

    CHUNK_SIZE = 20
    if n <= CHUNK_SIZE:
        return _concat_chunk(clip_paths, durations, output_path, transition, duration)

    # Разбить на чанки
    Path("temp").mkdir(exist_ok=True)
    chunks: list[Path] = []
    n_chunks = (n + CHUNK_SIZE - 1) // CHUNK_SIZE
    for ci, i in enumerate(range(0, n, CHUNK_SIZE)):
        chunk_clips = clip_paths[i:i + CHUNK_SIZE]
        chunk_durs  = durations[i:i + CHUNK_SIZE]
        chunk_out   = Path(f"temp/chunk_{ci:03d}.mp4")
        _concat_chunk(chunk_clips, chunk_durs, chunk_out, transition, duration)
        chunks.append(chunk_out)
        print(f"  ✅ Чанк {ci + 1}/{n_chunks}")

    # Склеить чанки
    if len(chunks) == 1:
        shutil.copy(str(chunks[0]), str(output_path))
    else:
        chunk_durs2 = [get_video_duration(str(c)) for c in chunks]
        _concat_chunk(chunks, chunk_durs2, output_path, transition, duration)

    print(f"  ✅ Все {n} клипов склеены: {Path(output_path).name}")
    return True


# ── Совместимость с assembler.py (старые имена) ──────────────────────────────

def glitch_transition(clip1_path, clip2_path, output_path, duration=0.5):
    """Алиас: теперь используем intro_to_main_transition."""
    return intro_to_main_transition(clip1_path, clip2_path, output_path, duration)


def slide_motionblur_transition(clip1_path, clip2_path, output_path, duration=0.5):
    """Алиас: теперь используем slide_transition с motion blur."""
    return slide_transition(clip1_path, clip2_path, output_path, duration)


def simple_slide(clip1_path, clip2_path, output_path, duration=0.5):
    """Чистый slideleft без motion blur (fallback)."""
    clip1_path  = Path(clip1_path)
    clip2_path  = Path(clip2_path)
    output_path = Path(output_path)
    clip1_dur   = get_video_duration(str(clip1_path))
    offset      = clip1_dur - duration
    subprocess.run([
        "ffmpeg", "-y",
        "-i", str(clip1_path),
        "-i", str(clip2_path),
        "-filter_complex",
        f"[0:v][1:v]xfade=transition=slideleft:"
        f"duration={duration:.3f}:offset={offset:.3f}[out]",
        "-map", "[out]",
        "-c:v", GPU_ENCODER, *GPU_PARAMS,
        "-pix_fmt", "yuv420p", "-r", "25", "-an",
        str(output_path)
    ], check=True)
    return True
