"""
Transcription Agent
-------------------
Принимает MP3 из data/input/, транскрибирует через Whisper,
нарезает на фрагменты по 3–8 сегментов в случайном порядке,
сохраняет JSON в data/transcripts/.
"""

import json
import os
import random
import time
from pathlib import Path

import whisper

# Добавляем ffmpeg в PATH если ещё не там
_FFMPEG_DIR = Path(os.environ.get("LOCALAPPDATA", "")) / (
    "Microsoft/WinGet/Packages/"
    "Gyan.FFmpeg_Microsoft.Winget.Source_8wekyb3d8bbwe/"
    "ffmpeg-8.0.1-full_build/bin"
)
if _FFMPEG_DIR.exists():
    os.environ["PATH"] = str(_FFMPEG_DIR) + os.pathsep + os.environ.get("PATH", "")

INPUT_DIR = Path(__file__).parent.parent / "data" / "input"
OUTPUT_DIR = Path(__file__).parent.parent / "data" / "transcripts"
OUTPUT_DIR.mkdir(parents=True, exist_ok=True)

# Модель Whisper: tiny / base / small / medium / large
WHISPER_MODEL = "base"


def load_model():
    print(f"[Whisper] Загружаю модель '{WHISPER_MODEL}'...")
    model = whisper.load_model(WHISPER_MODEL)
    print("[Whisper] Модель загружена.")
    return model


def transcribe_file(model, audio_path: Path) -> list[dict]:
    """Транскрибирует файл и возвращает список Whisper-сегментов."""
    print(f"[Whisper] Транскрибирую: {audio_path.name}")
    result = model.transcribe(str(audio_path), language="de", verbose=False, condition_on_previous_text=False)
    segments = result["segments"]
    print(f"[Whisper] Готово — {len(segments)} сегментов.")
    return segments


def split_into_fragments(segments: list[dict]) -> list[dict]:
    """
    Нарезает сегменты на фрагменты случайной длины (3–8 сегментов)
    в случайном порядке.
    """
    if not segments:
        return []

    # Нарезаем последовательно на куски 3–8 сегментов
    chunks = []
    i = 0
    while i < len(segments):
        size = random.randint(3, 8)
        chunk = segments[i : i + size]
        chunks.append(chunk)
        i += size

    # Перемешиваем порядок фрагментов
    random.shuffle(chunks)

    # Формируем финальные фрагменты с метаданными
    fragments = []
    for idx, chunk in enumerate(chunks):
        text = " ".join(s["text"].strip() for s in chunk)
        fragments.append(
            {
                "fragment_id": idx + 1,
                "segment_count": len(chunk),
                "start": chunk[0]["start"],
                "end": chunk[-1]["end"],
                "duration": round(chunk[-1]["end"] - chunk[0]["start"], 2),
                "text": text,
                "segments": chunk,
            }
        )

    return fragments


def save_fragments(audio_path: Path, fragments: list[dict]) -> Path:
    """Сохраняет фрагменты в JSON-файл."""
    stem = audio_path.stem
    timestamp = int(time.time())
    out_file = OUTPUT_DIR / f"{stem}_{timestamp}.json"

    payload = {
        "source_file": audio_path.name,
        "total_fragments": len(fragments),
        "fragments": fragments,
    }

    with open(out_file, "w", encoding="utf-8") as f:
        json.dump(payload, f, ensure_ascii=False, indent=2)

    print(f"[Agent] Сохранено: {out_file}")
    return out_file


def process_file(model, audio_path: Path) -> Path:
    segments = transcribe_file(model, audio_path)
    fragments = split_into_fragments(segments)
    print(f"[Agent] Нарезано на {len(fragments)} фрагментов.")
    return save_fragments(audio_path, fragments)


def run():
    audio_files = list(INPUT_DIR.glob("*.mp3")) + list(INPUT_DIR.glob("*.wav"))

    if not audio_files:
        print(f"[Agent] Нет аудиофайлов в {INPUT_DIR}")
        print("        Положи MP3/WAV в data/input/ и запусти снова.")
        return

    model = load_model()

    for audio_path in audio_files:
        try:
            out = process_file(model, audio_path)
            print(f"[Agent] OK {audio_path.name} -> {out.name}\n")
        except Exception as e:
            print(f"[Agent] ERR при обработке {audio_path.name}: {e}\n")


if __name__ == "__main__":
    run()
