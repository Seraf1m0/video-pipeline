"""
audio_mixer.py — подготовка и микс аудио для Video Pipeline.

Три отдельных функции:
  1. prepare_music_track(video_duration, output_path, music_start)
     Собирает музыкальную дорожку из папки MUSIC_DIR.
     Треки берутся по очереди, склеиваются до нужной длины.

  2. prepare_voice_track(voice_path, output_path)
     Конвертирует озвучку в AAC 44100Hz стерео.

  3. final_mix(voice_path, music_path, output_path, ...)
     Смикшировать озвучку + музыку + (опционально) интро аудио.
"""

import os
import json
import subprocess
from pathlib import Path


# ── Утилиты ──────────────────────────────────────────────────────────────────

def get_audio_duration(path) -> float:
    """Получить длительность аудио/видео через ffprobe."""
    cmd = [
        "ffprobe", "-v", "quiet",
        "-print_format", "json",
        "-show_format",
        str(path)
    ]
    r = subprocess.run(cmd, capture_output=True, text=True)
    try:
        return float(json.loads(r.stdout)["format"]["duration"])
    except Exception:
        return 0.0


# Алиас для совместимости
get_duration = get_audio_duration


# ─── ФУНКЦИЯ 1: Подготовить музыкальную дорожку ──────────────────────────────

def prepare_music_track(video_duration, output_path,
                        music_start=88):
    """
    Собрать музыкальную дорожку из папки треков.
    Длина = video_duration - music_start + 10 сек запас.
    Треки берутся по очереди (не рандомно).
    Никаких loop, никаких разрывов.
    """
    music_dir = Path(os.getenv("MUSIC_DIR", ""))
    if not music_dir.exists():
        print(f"❌ MUSIC_DIR не найден: {music_dir}")
        return None

    # Найти все треки
    tracks = sorted([
        f for f in music_dir.iterdir()
        if f.suffix.lower() in [".mp3", ".wav", ".aac", ".flac"]
    ])
    if not tracks:
        print(f"❌ Треки не найдены в {music_dir}")
        return None

    needed = video_duration - music_start + 10
    print(f"  🎵 Нужно музыки: {needed:.1f}s")
    print(f"  📁 Треков в папке: {len(tracks)}")

    Path("temp").mkdir(exist_ok=True)

    # Конвертировать нужные треки в WAV и склеить
    # пока не наберём нужную длину
    selected_tracks = []
    total_dur = 0.0
    track_idx = 0
    while total_dur < needed:
        track = tracks[track_idx % len(tracks)]
        track_wav = f"temp/track_{track_idx:03d}.wav"
        subprocess.run([
            "ffmpeg", "-y", "-i", str(track),
            "-ar", "44100", "-ac", "2",
            "-c:a", "pcm_s16le",
            track_wav
        ], check=True, capture_output=True)
        dur = get_audio_duration(track_wav)
        selected_tracks.append(track_wav)
        total_dur += dur
        track_idx += 1
        print(f"  ✅ Трек {track_idx}: {track.name} "
              f"({dur:.1f}s) | Итого: {total_dur:.1f}s")

    print(f"  📊 Использовано треков: {track_idx}")

    # Склеить все треки через concat — БЕЗ разрывов
    concat_list = "temp/music_concat.txt"
    with open(concat_list, "w") as f:
        for t in selected_tracks:
            f.write(f"file '{os.path.abspath(t)}'\n")

    # Склеить в один WAV с обрезкой до нужной длины
    merged_wav = "temp/music_merged.wav"
    subprocess.run([
        "ffmpeg", "-y",
        "-f", "concat", "-safe", "0",
        "-i", concat_list,
        "-t", str(needed),
        "-ar", "44100", "-ac", "2",
        "-c:a", "pcm_s16le",
        merged_wav
    ], check=True)

    merged_dur = get_audio_duration(merged_wav)
    print(f"  ✅ Склеено: {merged_dur:.1f}s")

    # Fade in 2s / Fade out 4s
    fo_start = merged_dur - 4.0
    faded_wav = "temp/music_faded.wav"
    subprocess.run([
        "ffmpeg", "-y", "-i", merged_wav,
        "-af", (
            f"afade=t=in:st=0:d=2,"
            f"afade=t=out:st={fo_start:.2f}:d=4"
        ),
        "-ar", "44100", "-ac", "2",
        "-c:a", "pcm_s16le",
        faded_wav
    ], check=True)

    # WAV → AAC финал
    subprocess.run([
        "ffmpeg", "-y", "-i", faded_wav,
        "-c:a", "aac", "-b:a", "192k",
        "-ar", "44100",
        str(output_path)
    ], check=True)

    final_dur = get_audio_duration(str(output_path))
    print(f"✅ Музыкальная дорожка: {final_dur:.1f}s → {output_path}")
    return str(output_path)


# ─── ФУНКЦИЯ 2: Подготовить озвучку ──────────────────────────────────────────

def prepare_voice_track(voice_path, output_path):
    """
    Конвертировать озвучку в AAC 44100Hz стерео.
    Без изменений громкости.
    """
    subprocess.run([
        "ffmpeg", "-y", "-i", str(voice_path),
        "-c:a", "aac", "-b:a", "192k",
        "-ar", "44100", "-ac", "2",
        str(output_path)
    ], check=True)
    dur = get_audio_duration(str(output_path))
    print(f"✅ Озвучка: {dur:.1f}s → {output_path}")
    return str(output_path)


# ─── ФУНКЦИЯ 3: Финальный микс ───────────────────────────────────────────────

def final_mix(voice_path, music_path, output_path,
              music_start=88, music_vol=0.08,
              intro_audio_path=None, intro_vol=0.8):
    """
    Смикшировать все дорожки в одну.
    Всё уже подготовлено — просто amix.

    Параметры
    ---------
    voice_path       : подготовленная озвучка AAC
    music_path       : подготовленная музыкальная дорожка AAC (или None)
    output_path      : итоговый аудио-файл
    music_start      : задержка старта музыки от начала (сек)
    music_vol        : громкость музыки (линейный коэффициент)
    intro_audio_path : аудио из интро-видео (или None)
    intro_vol        : громкость интро аудио (линейный коэффициент)
    """
    inputs = []
    filter_parts = []
    mix_inputs = []

    # [0] озвучка — полная с 0
    inputs += ["-i", str(voice_path)]
    filter_parts.append(
        "[0:a]volume=1.0,aresample=44100[voice]"
    )
    mix_inputs.append("[voice]")

    n_inputs = 1

    # [1] музыка — с music_start сек (если задана)
    if music_path and Path(str(music_path)).exists():
        inputs += ["-i", str(music_path)]
        filter_parts.append(
            f"[{n_inputs}:a]volume={music_vol},"
            f"adelay={int(music_start * 1000)}|"
            f"{int(music_start * 1000)},"
            f"aresample=44100[music]"
        )
        mix_inputs.append("[music]")
        n_inputs += 1
    else:
        print("  ⚠️ Музыка не задана — только голос")

    # интро аудио — если есть, первые 90 сек
    if intro_audio_path and Path(intro_audio_path).exists():
        inputs += ["-i", str(intro_audio_path)]
        filter_parts.append(
            f"[{n_inputs}:a]atrim=0:90,"
            f"volume={intro_vol},"
            f"aresample=44100[intro]"
        )
        mix_inputs.append("[intro]")
        n_inputs += 1

    # amix всех дорожек
    mix_str = "".join(mix_inputs)
    filter_parts.append(
        f"{mix_str}amix=inputs={n_inputs}:"
        f"duration=first:"
        f"normalize=0:"
        f"dropout_transition=0[out]"
    )

    filter_complex = ";".join(filter_parts)

    cmd = (
        ["ffmpeg", "-y"]
        + inputs
        + [
            "-filter_complex", filter_complex,
            "-map", "[out]",
            "-c:a", "aac", "-b:a", "192k",
            "-ar", "44100",
            str(output_path)
        ]
    )
    subprocess.run(cmd, check=True)
    dur = get_audio_duration(str(output_path))
    print(f"✅ Финальный микс: {dur:.1f}s → {output_path}")
    return str(output_path)
