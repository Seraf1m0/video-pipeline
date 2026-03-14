#!/usr/bin/env python3
"""
Автоматический монтаж видео v2: Пропорциональный маппинг
Синхронизирует видеоклипы с озвучкой.
Длительность каждого клипа пропорциональна количеству слов в его тексте.
Общая длительность видеоряда = длительность аудио.
"""

import io
import json
import sys
import os
import subprocess
import shutil
import argparse
from pathlib import Path

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")


def log(msg):
    print(f"[МОНТАЖ] {msg}", flush=True)


def check_dependencies():
    try:
        subprocess.run(["ffmpeg", "-version"], capture_output=True, check=True)
        log("✓ ffmpeg найден")
    except (subprocess.CalledProcessError, FileNotFoundError):
        log("✗ ffmpeg не найден. Установите: brew install ffmpeg")
        sys.exit(1)


def get_audio_duration(audio_path):
    """Получить длительность аудиофайла через ffprobe"""
    cmd = [
        "ffprobe", "-v", "quiet",
        "-print_format", "json",
        "-show_format",
        str(audio_path),
    ]
    result = subprocess.run(cmd, capture_output=True, text=True)
    info = json.loads(result.stdout)
    return float(info["format"]["duration"])


def build_music_track(music_path, start_time, total_duration, output_path, temp_dir="temp"):
    """Создаёт музыкальный трек: зацикливает до нужной длины, добавляет fade in/out."""
    music_dur = get_audio_duration(music_path)
    needed = total_duration - start_time + 3  # +3 сек запас

    copies = int(needed / music_dur) + 2

    concat_file = os.path.join(temp_dir, "music_concat.txt")
    with open(concat_file, "w") as f:
        for _ in range(copies):
            f.write(f"file '{os.path.abspath(music_path)}'\n")

    raw_music = os.path.join(temp_dir, "music_raw.aac")
    subprocess.run([
        "ffmpeg", "-y",
        "-f", "concat", "-safe", "0",
        "-i", concat_file,
        "-c:a", "aac", "-b:a", "192k",
        "-t", str(needed),
        "-loglevel", "warning",
        raw_music
    ], check=True)

    music_actual_dur = get_audio_duration(raw_music)
    fade_out_start = music_actual_dur - 3.0

    subprocess.run([
        "ffmpeg", "-y",
        "-i", raw_music,
        "-af", f"afade=t=in:st=0:d=1,afade=t=out:st={fade_out_start}:d=3",
        "-c:a", "aac", "-b:a", "192k",
        "-loglevel", "warning",
        output_path
    ], check=True)

    log(f"✅ Музыка готова: {copies} копий, fade in/out")
    return output_path


def compute_proportional_timings(scenes, total_duration):
    """
    Пропорциональное распределение: длительность каждой сцены
    пропорциональна количеству слов в её тексте.
    Сумма всех длительностей = total_duration.
    """
    import re

    # Считаем слова в каждой сцене
    word_counts = []
    for scene in scenes:
        words = re.findall(r"[a-zA-Zа-яА-ЯёЁ0-9']+", scene["text"])
        word_counts.append(max(len(words), 1))  # минимум 1 слово

    total_words = sum(word_counts)
    log(f"Всего слов: {total_words}, длительность аудио: {total_duration:.1f}s")

    # Распределяем время пропорционально словам
    timings = []
    current_time = 0.0

    for si, scene in enumerate(scenes):
        proportion = word_counts[si] / total_words
        duration = proportion * total_duration

        # Минимум 2 секунды, максимум 600 секунд (для длинного интро)
        duration = max(2.0, min(duration, 600.0))

        timings.append({
            "start": current_time,
            "end": current_time + duration,
            "duration": duration,
            "words": word_counts[si],
        })

        current_time += duration

    # Корректируем — нормализуем сумму до total_duration
    actual_total = sum(t["duration"] for t in timings)
    scale = total_duration / actual_total

    current_time = 0.0
    for t in timings:
        t["duration"] *= scale
        t["start"] = current_time
        t["end"] = current_time + t["duration"]
        current_time += t["duration"]

    # Последний клип заканчивается ровно в конце аудио
    timings[-1]["end"] = total_duration
    timings[-1]["duration"] = total_duration - timings[-1]["start"]

    return timings


def get_video_duration(video_path):
    cmd = [
        "ffprobe", "-v", "quiet",
        "-print_format", "json",
        "-show_format",
        str(video_path),
    ]
    result = subprocess.run(cmd, capture_output=True, text=True)
    info = json.loads(result.stdout)
    return float(info["format"]["duration"])


def trim_or_loop_clip(input_path, output_path, target_duration):
    actual_duration = max(target_duration, 2.0)
    clip_duration = get_video_duration(input_path)

    if actual_duration <= clip_duration:
        cmd = [
            "ffmpeg", "-y", "-i", str(input_path),
            "-t", str(actual_duration),
            "-c:v", "libx264", "-preset", "fast",
            "-an",
            "-loglevel", "warning",
            str(output_path),
        ]
        subprocess.run(cmd, check=True)
    else:
        loops_needed = int(actual_duration / clip_duration) + 1
        concat_file = str(output_path) + ".concat.txt"
        with open(concat_file, "w") as f:
            for _ in range(loops_needed):
                f.write(f"file '{os.path.abspath(input_path)}'\n")

        cmd = [
            "ffmpeg", "-y", "-f", "concat", "-safe", "0",
            "-i", concat_file,
            "-t", str(actual_duration),
            "-c:v", "libx264", "-preset", "fast",
            "-an",
            "-loglevel", "warning",
            str(output_path),
        ]
        subprocess.run(cmd, check=True)
        os.remove(concat_file)

    return actual_duration


def concatenate_clips(clip_paths, output_path, resolution="1280x720"):
    w, h = resolution.split("x")

    concat_file = str(output_path) + ".concat.txt"
    with open(concat_file, "w") as f:
        for clip in clip_paths:
            f.write(f"file '{os.path.abspath(clip)}'\n")

    cmd = [
        "ffmpeg", "-y", "-f", "concat", "-safe", "0",
        "-i", concat_file,
        "-vf", f"scale={w}:{h}:force_original_aspect_ratio=decrease,pad={w}:{h}:(ow-iw)/2:(oh-ih)/2:black",
        "-r", "30",
        "-c:v", "libx264", "-preset", "fast",
        "-pix_fmt", "yuv420p",
        "-an",
        "-loglevel", "warning",
        str(output_path),
    ]
    subprocess.run(cmd, check=True)
    os.remove(concat_file)


def concat_with_transitions(clip_paths, output_path,
                             transition="fade", duration=1.0, resolution="1920x1080"):
    """Склеивает клипы с xfade переходами через FFmpeg filter_complex."""
    w, h = resolution.split("x")
    scale_vf = (f"scale={w}:{h}:force_original_aspect_ratio=decrease,"
                f"pad={w}:{h}:(ow-iw)/2:(oh-ih)/2:black,setsar=1,fps=30")

    if len(clip_paths) == 1:
        subprocess.run([
            "ffmpeg", "-y", "-i", str(clip_paths[0]),
            "-vf", scale_vf,
            "-r", "30", "-c:v", "libx264", "-preset", "fast",
            "-pix_fmt", "yuv420p", "-an", "-loglevel", "warning",
            str(output_path),
        ], check=True)
        return

    durations = [get_video_duration(p) for p in clip_paths]

    filter_parts = []
    # Scale каждый вход
    for i in range(len(clip_paths)):
        filter_parts.append(f"[{i}:v]{scale_vf}[sv{i}]")

    # Первый xfade
    offset = durations[0] - duration
    filter_parts.append(
        f"[sv0][sv1]xfade=transition={transition}:duration={duration}:offset={offset}[v1]"
    )

    # Цепочка для остальных клипов
    for i in range(2, len(clip_paths)):
        prev_offset = sum(durations[:i]) - duration * i
        filter_parts.append(
            f"[v{i-1}][sv{i}]xfade=transition={transition}:duration={duration}:offset={prev_offset}[v{i}]"
        )

    last = f"v{len(clip_paths) - 1}"
    filter_complex = ";".join(filter_parts)

    cmd = ["ffmpeg", "-y"]
    for p in clip_paths:
        cmd += ["-i", str(p)]
    cmd += [
        "-filter_complex", filter_complex,
        "-map", f"[{last}]",
        "-r", "30",
        "-c:v", "libx264", "-preset", "fast",
        "-pix_fmt", "yuv420p",
        "-an",
        "-loglevel", "warning",
        str(output_path),
    ]
    subprocess.run(cmd, check=True)


def merge_audio_video(video_path, audio_path, output_path):
    cmd = [
        "ffmpeg", "-y",
        "-i", str(video_path),
        "-i", str(audio_path),
        "-c:v", "copy",
        "-c:a", "aac", "-b:a", "192k",
        "-map", "0:v:0", "-map", "1:a:0",
        "-loglevel", "warning",
        str(output_path),
    ]
    subprocess.run(cmd, check=True)


def main():
    parser = argparse.ArgumentParser(description="Автоматический монтаж видео v2")
    parser.add_argument("project", help="Путь к project.json")
    args = parser.parse_args()

    project_path = Path(args.project)
    if not project_path.exists():
        log(f"✗ Файл не найден: {project_path}")
        sys.exit(1)

    with open(project_path) as f:
        project = json.load(f)

    base_dir = project_path.parent
    temp_dir = base_dir / "temp"
    output_dir = base_dir / "output"

    temp_dir.mkdir(exist_ok=True)
    output_dir.mkdir(exist_ok=True)

    log("=" * 50)
    log("АВТОМАТИЧЕСКИЙ МОНТАЖ ВИДЕО v2")
    log("Пропорциональный маппинг по словам")
    log("=" * 50)

    check_dependencies()

    # audio_file необязателен — если отсутствует, собираем только видеоряд
    audio_file_val = project.get("audio_file")
    if audio_file_val:
        _ap = Path(audio_file_val)
        audio_path = _ap if _ap.is_absolute() else base_dir / _ap
        if not audio_path.exists():
            log(f"✗ Озвучка не найдена: {audio_path}")
            sys.exit(1)
    else:
        audio_path = None
        log("ℹ audio_file не задан — экспортируется только видеоряд (без звука)")

    music_file_val = project.get("music_file")
    music_start = float(project.get("music_start", 0))
    if music_file_val:
        _mp = Path(music_file_val)
        music_path = _mp if _mp.is_absolute() else base_dir / _mp
    else:
        music_path = None
        log("ℹ music_file не задан — без фоновой музыки")

    resolution = project.get("resolution", "1920x1080")
    clips_dir = Path(project.get("clips_dir", "input/clips"))
    if not clips_dir.is_absolute():
        clips_dir = base_dir / clips_dir
    scenes = project["scenes"]
    log(f"Проект: {len(scenes)} сцен, разрешение {resolution}")
    log(f"Клипы: {clips_dir}")

    # ШАГ 1: Длительность аудио (или суммарное кол-во слов для пропорции)
    log("\n--- ШАГ 1: Анализ аудио ---")
    if audio_path:
        total_duration = get_audio_duration(audio_path)
        log(f"Длительность озвучки: {total_duration:.1f}s ({total_duration/60:.1f} мин)")
    elif project.get("total_duration_hint", 0) > 0:
        total_duration = float(project["total_duration_hint"])
        log(f"Длительность (из подсказки): {total_duration:.1f}s ({total_duration/60:.1f} мин)")
    else:
        # Без аудио — используем суммарное время из сегментов (end последнего)
        import re as _re
        total_duration = max(
            (float(sc.get("end", 0)) for sc in scenes if "end" in sc),
            default=sum(max(len(_re.findall(r"[a-zA-ZА-Яа-яёЁ0-9']+", sc.get("text",""))), 1)
                        for sc in scenes) * 0.5,
        )
        log(f"Длительность (из сегментов): {total_duration:.1f}s")

    # ШАГ 2: Пропорциональный маппинг
    log(f"\n--- ШАГ 2: Пропорциональный маппинг {len(scenes)} сцен ---")
    timings = compute_proportional_timings(scenes, total_duration)

    for scene, timing in zip(scenes, timings):
        log(f"  ✓ Сцена {scene['id']:3d}: {timing['start']:7.2f}s — {timing['end']:7.2f}s "
            f"({timing['duration']:5.2f}s, {timing['words']} слов)")

    total_video = sum(t["duration"] for t in timings)
    log(f"\nОбщая длительность видеоряда: {total_video:.1f}s (аудио: {total_duration:.1f}s)")

    # ШАГ 3: Подготовка клипов
    log(f"\n--- ШАГ 3: Подготовка {len(scenes)} клипов ---")

    trimmed_clips = []
    for i, (scene, timing) in enumerate(zip(scenes, timings)):
        clip_path = clips_dir / scene["clip"]
        if not clip_path.exists():
            log(f"  ⚠ Клип не найден: {clip_path}")
            continue

        out_clip = temp_dir / f"trimmed_{scene['id']:03d}.mp4"
        log(f"  [{i+1}/{len(scenes)}] Сцена {scene['id']}: {timing['duration']:.2f}s")

        trim_or_loop_clip(clip_path, out_clip, timing["duration"])
        trimmed_clips.append(str(out_clip))

    if not trimmed_clips:
        log("✗ Нет подготовленных клипов")
        sys.exit(1)

    # ШАГ 4: Склейка
    log(f"\n--- ШАГ 4: Склейка {len(trimmed_clips)} клипов ---")

    video_only = temp_dir / "video_concat.mp4"
    concat_with_transitions(trimmed_clips, video_only, "fade", 1.0, resolution)
    log(f"✓ Видеоряд: {video_only}")

    video_duration = get_video_duration(video_only)
    log(f"Длительность видеоряда: {video_duration:.1f}s")

    # ШАГ 5: Наложение озвучки (если audio_path задан)
    final_output = Path(project.get("output_file", "output/final_video.mp4"))
    if not final_output.is_absolute():
        final_output = base_dir / final_output
    final_output.parent.mkdir(parents=True, exist_ok=True)

    if audio_path:
        if music_path and music_path.exists():
            log("\n--- ШАГ 5: Озвучка + Фоновая музыка ---")
            music_track_file = temp_dir / "music_track.aac"
            build_music_track(str(music_path), music_start, total_duration,
                              str(music_track_file), str(temp_dir))
            mixed_audio = temp_dir / "mixed_audio.aac"
            music_delay_ms = int(music_start * 1000)
            subprocess.run([
                "ffmpeg", "-y",
                "-i", str(audio_path),
                "-i", str(music_track_file),
                "-filter_complex",
                f"[1:a]adelay={music_delay_ms}|{music_delay_ms}[m];"
                f"[0:a][m]amix=inputs=2:duration=first[a]",
                "-map", "[a]", "-c:a", "aac", "-b:a", "192k",
                "-loglevel", "warning",
                str(mixed_audio),
            ], check=True)
            merge_audio_video(video_only, mixed_audio, final_output)
        else:
            log("\n--- ШАГ 5: Наложение озвучки ---")
            merge_audio_video(video_only, audio_path, final_output)
    else:
        log("\n--- ШАГ 5: Копируем видеоряд (без звука) ---")
        shutil.copy2(str(video_only), str(final_output))
        log(f"✓ Скопировано: {final_output}")

    final_duration = get_video_duration(final_output)
    log(f"\n{'=' * 50}")
    log(f"ГОТОВО! Длительность: {final_duration:.1f}s ({final_duration/60:.1f} мин)")
    log(f"Файл: {final_output}")
    log(f"{'=' * 50}")

    # Очистка
    log("\nОчистка временных файлов...")
    shutil.rmtree(temp_dir)
    log("✓ Готово!")


if __name__ == "__main__":
    main()
