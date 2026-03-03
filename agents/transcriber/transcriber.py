"""
Transcriber Agent
-----------------
1. Берёт MP3 из data/input/ (корень, не подпапки)
2. Создаёт папку data/input/Video_YYYYMMDD_HHMMSS/ и перемещает туда MP3
3. Транскрибирует весь файл через Whisper "base"
4. Нарезает на блоки с точными целочисленными границами (grok=10s, random=3–8s)
5. Сохраняет data/transcripts/Video_YYYYMMDD_HHMMSS/result.json

Запуск: py agents/transcriber/transcriber.py
"""

import argparse
import json
import os
import random
import shutil
from datetime import datetime
from pathlib import Path

import torch
import whisper

print(f"CUDA доступен: {torch.cuda.is_available()}")
print(f"GPU: {torch.cuda.get_device_name(0) if torch.cuda.is_available() else 'N/A'}")

# ── Модели Whisper ────────────────────────────────────────────────────────────
# GPU (RTX 3060 12GB): large-v2 — высокое качество, ~10x быстрее base на CPU
# CPU фолбэк         : base — быстро, достаточное качество
_GPU_MODEL = "large-v2"
_CPU_MODEL = "base"

# ffmpeg в PATH (winget-установка)
_FFMPEG_DIR = Path(os.environ.get("LOCALAPPDATA", "")) / (
    "Microsoft/WinGet/Packages/"
    "Gyan.FFmpeg_Microsoft.Winget.Source_8wekyb3d8bbwe/"
    "ffmpeg-8.0.1-full_build/bin"
)
if _FFMPEG_DIR.exists():
    os.environ["PATH"] = str(_FFMPEG_DIR) + os.pathsep + os.environ.get("PATH", "")

BASE_DIR = Path(__file__).parent.parent.parent
INPUT_DIR = BASE_DIR / "data" / "input"
TRANSCRIPTS_DIR = BASE_DIR / "data" / "transcripts"


def detect_device() -> tuple[str, str, str]:
    """
    Определяет устройство, GPU-имя и имя модели Whisper.
    Возвращает: (device, gpu_name, model_size)
    """
    if torch.cuda.is_available():
        gpu_name = torch.cuda.get_device_name(0)
        return "cuda", gpu_name, _GPU_MODEL
    return "cpu", "", _CPU_MODEL


def find_mp3() -> Path | None:
    """Ищет первый MP3/WAV прямо в data/input/ (не в подпапках)."""
    for ext in ("*.mp3", "*.wav"):
        files = [f for f in INPUT_DIR.glob(ext) if f.parent == INPUT_DIR]
        if files:
            return files[0]
    return None


def make_session_name() -> str:
    return "Video_" + datetime.now().strftime("%Y%m%d_%H%M%S")


def move_to_session(audio_path: Path, session: str) -> Path:
    """Создаёт data/input/<session>/ и перемещает туда MP3."""
    dest_dir = INPUT_DIR / session
    dest_dir.mkdir(parents=True, exist_ok=True)
    dest = dest_dir / audio_path.name
    shutil.move(str(audio_path), dest)
    print(f"[Agent] Перемещено: {audio_path.name} -> input/{session}/")
    return dest


def transcribe(model, audio_path: Path, use_gpu: bool = False) -> tuple[list[dict], float]:
    """Транскрибирует файл, возвращает сегменты и длину в секундах."""
    print(f"[Whisper] Транскрибирую: {audio_path.name}")
    result = model.transcribe(
        str(audio_path),
        language="de",
        verbose=False,
        condition_on_previous_text=False,
        fp16=use_gpu,   # fp16 на GPU — быстрее; на CPU — False (не поддерживается)
    )
    segments = result["segments"]
    duration = segments[-1]["end"] if segments else 0.0
    print(f"[Whisper] Готово — {len(segments)} сегментов, {duration:.1f}s")
    return segments, duration


def ask_cut_mode() -> str:
    """Спрашивает режим нарезки. Возвращает 'random' или 'grok'."""
    print("\nКак нарезать сегменты?")
    print("  1. Рандомно от 3 до 8 секунд (для Nano Banana / Flow)")
    print("  2. Ровно по 10 секунд (для Grok)")
    while True:
        choice = input("Номер (1/2): ").strip()
        if choice == "1":
            print("  Режим: рандомно 3–8 сек")
            return "random"
        if choice == "2":
            print("  Режим: ровно 10 сек (Grok)")
            return "grok"
        print("  Неверный ввод.")


def build_segments(whisper_segs: list[dict], mode: str = "random") -> list[dict]:
    """
    Нарезает whisper_segments на блоки с точными целочисленными границами.
    mode='grok'   — ровно 10 сек на блок
    mode='random' — рандомно 3–8 сек, новый target на каждый блок
    """
    if mode == "grok":
        dur_fn = lambda: 10
    else:
        dur_fn = lambda: random.randint(3, 8)

    blocks = _slice_by_blocks(whisper_segs, dur_fn)
    _verify_segments(blocks, mode)
    return blocks


def _slice_by_blocks(whisper_segs: list[dict], dur_fn) -> list[dict]:
    """
    Нарезка по словам с целочисленными границами.

    - current_start = int(первый старт) — всегда int
    - block_end = current_start + target — всегда int
    - Слова берутся пропорционально overlap при частичном попадании
    - random mode: новый target генерируется для каждого нового блока
    """
    blocks: list[dict] = []
    current_words: list[str] = []
    current_start: int | None = None
    target = dur_fn()

    for ws in whisper_segs:
        if current_start is None:
            current_start = int(ws["start"])  # целое число

        words = ws["text"].strip().split()
        if not words:
            continue

        if ws["end"] <= current_start + target:
            # Сегмент полностью входит в блок
            current_words.extend(words)
        else:
            # Сегмент частично входит в блок
            overlap   = (current_start + target) - ws["start"]
            total_dur = ws["end"] - ws["start"]
            ratio     = max(0.0, min(1.0, overlap / total_dur)) if total_dur > 0 else 1.0
            take      = max(1, int(len(words) * ratio))

            current_words.extend(words[:take])

            # Закрываем блок РОВНО на целое число секунд
            block_end = current_start + target

            blocks.append({
                "id":    len(blocks) + 1,
                "start": current_start,   # int
                "end":   block_end,       # int
                "text":  " ".join(current_words).strip(),
            })

            # Начинаем новый блок
            current_start = block_end
            current_words = words[take:]
            target = dur_fn()  # новый target для каждого нового блока

    # Последний блок
    if current_words and current_start is not None:
        blocks.append({
            "id":    len(blocks) + 1,
            "start": current_start,
            "end":   current_start + target,
            "text":  " ".join(current_words).strip(),
        })

    return blocks


def _verify_segments(blocks: list[dict], mode: str) -> None:
    """Проверяет корректность нарезки через assert, печатает первые 5 блоков."""
    print(f"\n[Check] Режим: {mode} | Итого блоков: {len(blocks)}")

    for i, block in enumerate(blocks):
        assert isinstance(block["start"], int), \
            f"Блок {i+1}: start должен быть int, получен {type(block['start'])}"
        assert isinstance(block["end"], int), \
            f"Блок {i+1}: end должен быть int, получен {type(block['end'])}"
        assert block["text"].strip() != "", f"Блок {i+1} пустой!"
        if i > 0:
            assert block["start"] == blocks[i - 1]["end"], \
                f"Пропуск между блоком {i} и {i+1}: " \
                f"{blocks[i-1]['end']}s → {block['start']}s"

    print("✅ Все проверки пройдены")
    print(f"Первые 5 блоков:")
    for b in blocks[:5]:
        print(f"  #{b['id']}: {b['start']}s-{b['end']}s | {b['text'][:50]}")

    durs = [b["end"] - b["start"] for b in blocks]
    print(
        f"  Мин: {min(durs)}s | Макс: {max(durs)}s | Ср: {sum(durs)/len(durs):.1f}s"
    )


def save(session: str, segments: list[dict]) -> Path:
    out_dir = TRANSCRIPTS_DIR / session
    out_dir.mkdir(parents=True, exist_ok=True)
    out_file = out_dir / "result.json"
    with open(out_file, "w", encoding="utf-8") as f:
        json.dump(segments, f, ensure_ascii=False, indent=2)
    return out_file


# ---------------------------------------------------------------------------
# Субтитры
# ---------------------------------------------------------------------------

def _srt_time(seconds: float) -> str:
    """Секунды → HH:MM:SS,mmm (формат SRT)."""
    ms  = int(round(seconds * 1000))
    h   = ms // 3_600_000;  ms -= h * 3_600_000
    m   = ms // 60_000;     ms -= m * 60_000
    s   = ms // 1_000;      ms -= s * 1_000
    return f"{h:02d}:{m:02d}:{s:02d},{ms:03d}"


def _vtt_time(seconds: float) -> str:
    """Секунды → HH:MM:SS.mmm (формат VTT)."""
    return _srt_time(seconds).replace(",", ".")


def save_srt(session: str, segments: list[dict]) -> Path:
    out_dir  = TRANSCRIPTS_DIR / session
    out_dir.mkdir(parents=True, exist_ok=True)
    out_file = out_dir / "subtitles.srt"

    blocks = []
    for seg in segments:
        blocks.append(
            f"{seg['id']}\n"
            f"{_srt_time(seg['start'])} --> {_srt_time(seg['end'])}\n"
            f"{seg['text'].strip()}\n"
        )

    out_file.write_text("\n".join(blocks), encoding="utf-8")
    return out_file


def save_vtt(session: str, segments: list[dict]) -> Path:
    out_dir  = TRANSCRIPTS_DIR / session
    out_dir.mkdir(parents=True, exist_ok=True)
    out_file = out_dir / "subtitles.vtt"

    lines = ["WEBVTT", ""]
    for seg in segments:
        lines.append(f"{_vtt_time(seg['start'])} --> {_vtt_time(seg['end'])}")
        lines.append(seg["text"].strip())
        lines.append("")

    out_file.write_text("\n".join(lines), encoding="utf-8")
    return out_file


def run():
    parser = argparse.ArgumentParser(description="Transcriber Agent")
    parser.add_argument("--mode", choices=["random", "grok"], default=None,
                        help="Cut mode: random (3-8s by Whisper pauses) or grok (exactly 10s)")
    parser.add_argument("--input", default=None, metavar="PATH",
                        help="Path to MP3/WAV file (skips auto-search)")
    args = parser.parse_args()

    if args.input:
        audio_path = Path(args.input)
        if not audio_path.is_absolute():
            audio_path = BASE_DIR / audio_path
        if not audio_path.exists():
            print(f"[Agent] Файл не найден: {audio_path}")
            return
    else:
        audio_path = find_mp3()
        if not audio_path:
            print(f"[Agent] Нет MP3/WAV в {INPUT_DIR}")
            print("        Положи файл прямо в data/input/ и запусти снова.")
            return

    session = make_session_name()
    print(f"[Agent] Сессия: {session}")

    audio_path = move_to_session(audio_path, session)

    if args.mode:
        mode = args.mode
        label = "рандомно 3–8 сек" if mode == "random" else "ровно 10 сек (Grok)"
        print(f"  Режим: {label}")
    else:
        mode = ask_cut_mode()

    # ── Определяем устройство ────────────────────────────────────────────
    device, gpu_name, model_size = detect_device()
    if device == "cuda":
        print(f"[Whisper] GPU: {gpu_name}")
    else:
        print(f"[Whisper] CPU (CUDA nedostupna)")
    print(f"[Whisper] Загружаю модель '{model_size}' на {device.upper()}...")

    model = whisper.load_model(model_size, device=device)
    print(f"[Whisper] Модель загружена.")

    whisper_segs, duration = transcribe(model, audio_path, use_gpu=(device == "cuda"))
    segments = build_segments(whisper_segs, mode)

    json_path = save(session, segments)
    srt_path  = save_srt(session, segments)
    vtt_path  = save_vtt(session, segments)

    print(f"[Agent] OK — {len(segments)} сегментов, покрыто {duration:.1f}s")
    print(f"[Agent] Subtitles created:")
    print(f"   {srt_path.name}")
    print(f"   {vtt_path.name}")
    print(f"   {json_path.name}")


if __name__ == "__main__":
    run()
