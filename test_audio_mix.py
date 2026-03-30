"""
test_audio_mix.py — standalone аудио микс для прослушивания.
Берёт последнюю DE сессию: голос + музыка + интро аудио (без субтитров и видео).
Результат: test_audio_mix_out.m4a
"""
import sys, os
from pathlib import Path

# Добавить папку assembler в path
sys.path.insert(0, str(Path(__file__).parent / "agents" / "assembler"))

# Загрузить .env
from dotenv import load_dotenv
load_dotenv(Path(__file__).parent / "config" / ".env")

from audio_mixer import build_final_audio

SESSION = Path("data/channels/de/Video_20260329_211430")
voice_path       = SESSION / "input" / "audio.mp3"
intro_audio_path = SESSION / "intro.mp4"
output_path      = Path("test_audio_mix_out.m4a")

# DE channel_styles.json settings
music_vol_db    = -22.0
intro_vol_db    = -20.0
intro_dur       = 92.04   # ffprobe
video_duration  = 847.62  # ffprobe final video

print(f"Voice:  {voice_path}")
print(f"Intro:  {intro_audio_path}")
print(f"Output: {output_path}")
print(f"music_start_sec = {intro_dur}s  |  music_vol_db = {music_vol_db}  |  video_duration = {video_duration}s")
print()

build_final_audio(
    voice_path       = voice_path,
    output_path      = output_path,
    sfx_events       = [],        # без SFX — чистый микс голос+музыка+интро
    music_start_sec  = intro_dur,
    music_vol_db     = music_vol_db,
    intro_audio_path = str(intro_audio_path),
    intro_trim_s     = intro_dur + 2.0,
    intro_vol_db     = intro_vol_db,
    video_duration   = video_duration,
)

print(f"\nГотово: {output_path.resolve()}")
