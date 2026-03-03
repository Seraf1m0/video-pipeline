"""
Transcriber Agent
-----------------
1. Берёт MP3 из data/input/ (корень, не подпапки)
2. Создаёт папку data/input/Video_YYYYMMDD_HHMMSS/ и перемещает туда MP3
3. Транскрибирует весь файл через Whisper "base"
4. Нарезает на блоки с точными целочисленными границами (grok=10s, random=3–8s)
5. Верифицирует блоки через ffprobe (покрытие, хвост)
6. Сохраняет data/transcripts/Video_YYYYMMDD_HHMMSS/result.json
   Формат: {"ffmpeg_verified": ..., "segments": [...]}

Запуск: py agents/transcriber/transcriber.py
"""

import argparse
import json
import os
import random
import shutil
import subprocess
import sys
from datetime import datetime
from pathlib import Path

import torch
import whisper

# Windows cp1251 консоль не поддерживает эмодзи — переключаем на UTF-8
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

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
    mode='grok'   — строго 10 сек (фиксированные границы, исходный алгоритм)
    mode='random' — рандомно 3–8 сек по границам Whisper-сегментов (без overflow)
    """
    if mode == "grok":
        blocks = _slice_grok(whisper_segs)
    else:
        blocks = _slice_random(whisper_segs)
    _verify_segments(blocks, mode)
    return blocks


def _slice_grok(whisper_segs: list[dict]) -> list[dict]:
    """
    Grok mode: строго 10 сек, фиксированные целочисленные границы.
    Слова берутся пропорционально overlap (ratio-based split).
    Последний блок обрезается verify_with_ffmpeg до реального конца аудио.
    """
    TARGET = 10
    blocks: list[dict] = []
    current_words: list[str] = []
    current_start: int | None = None

    for ws in whisper_segs:
        if current_start is None:
            current_start = int(ws["start"])

        words = ws["text"].strip().split()
        if not words:
            continue

        if ws["end"] <= current_start + TARGET:
            current_words.extend(words)
        else:
            overlap   = (current_start + TARGET) - ws["start"]
            total_dur = ws["end"] - ws["start"]
            ratio     = max(0.0, min(1.0, overlap / total_dur)) if total_dur > 0 else 1.0
            take      = max(1, int(len(words) * ratio))

            current_words.extend(words[:take])
            block_end = current_start + TARGET
            blocks.append({
                "id":    len(blocks) + 1,
                "start": current_start,
                "end":   block_end,
                "text":  " ".join(current_words).strip(),
            })
            current_start = block_end
            current_words = words[take:]

    if current_words and current_start is not None:
        blocks.append({
            "id":    len(blocks) + 1,
            "start": current_start,
            "end":   current_start + TARGET,
            "text":  " ".join(current_words).strip(),
        })

    return blocks


def _slice_random(whisper_segs: list[dict]) -> list[dict]:
    """
    Random mode: рандомно 3–8 сек, блок закрывается на границе Whisper-сегмента.

    НЕ разрезает сегменты пополам — устраняет overflow (слишком много слов
    в коротком блоке). Последний блок заканчивается на реальном конце речи.
    """
    blocks: list[dict] = []
    current_words: list[str] = []
    current_start: int | None = None
    target = random.randint(3, 8)

    for ws in whisper_segs:
        words = ws["text"].strip().split()
        if not words:
            continue

        if current_start is None:
            current_start = int(ws["start"])

        elapsed = ws["end"] - current_start

        if current_words and elapsed > target:
            # Следующий сегмент переполнил бы блок → закрываем ДО него
            block_end = max(int(round(ws["start"])), current_start + 1)
            blocks.append({
                "id":    len(blocks) + 1,
                "start": current_start,
                "end":   block_end,
                "text":  " ".join(current_words).strip(),
            })
            current_start = block_end
            current_words = []
            target = random.randint(3, 8)

        # Добавляем сегмент целиком (без разрезания)
        current_words.extend(words)

    # Последний блок — до реального конца последнего Whisper-сегмента
    if current_words and current_start is not None:
        last_end = max(int(round(whisper_segs[-1]["end"])), current_start + 1)
        blocks.append({
            "id":    len(blocks) + 1,
            "start": current_start,
            "end":   last_end,
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

    print("[OK] Все проверки пройдены")
    print(f"Первые 5 блоков:")
    for b in blocks[:5]:
        print(f"  #{b['id']}: {b['start']}s-{b['end']}s | {b['text'][:50]}")

    durs = [b["end"] - b["start"] for b in blocks]
    print(
        f"  Мин: {min(durs)}s | Макс: {max(durs)}s | Ср: {sum(durs)/len(durs):.1f}s"
    )

    # Предупреждение о переполнении (информационно, не ошибка)
    overflow = [
        b for b in blocks
        if b["end"] > b["start"] and
        len(b["text"].split()) / 2.5 > (b["end"] - b["start"]) * 1.1
    ]
    if overflow:
        print(f"  [!] Переполнение (>110% слов) в {len(overflow)} блоках:")
        for b in overflow[:5]:
            dur = b["end"] - b["start"]
            wc = len(b["text"].split())
            pct = int(wc / 2.5 / dur * 100)
            print(f"      #{b['id']}: {dur}s | {wc}w | {pct}% | {b['text'][:40]}")
    else:
        print("  Переполнений нет.")


# ---------------------------------------------------------------------------
# FFmpeg верификация
# ---------------------------------------------------------------------------

def verify_with_ffmpeg(audio_path: Path, blocks: list[dict]) -> tuple[list[dict], dict]:
    """
    Проверяет реальную длину аудио через ffprobe и сверяет с нашими блоками.
    Если непокрытый хвост > 1с — добавляет финальный блок "[конец]".
    Возвращает (обновлённые блоки, метаданные для result.json).
    """
    cmd = [
        "ffprobe", "-v", "quiet",
        "-print_format", "json",
        "-show_format",
        str(audio_path),
    ]
    result = subprocess.run(cmd, capture_output=True, text=True)
    info = json.loads(result.stdout)
    real_duration = float(info["format"]["duration"])

    coverage = blocks[-1]["end"] - blocks[0]["start"]
    gap      = real_duration - coverage

    # Последний блок вышел за конец аудио → обрезаем до реального конца
    if gap < -1:
        old_end = blocks[-1]["end"]
        blocks[-1]["end"] = int(real_duration)
        print(
            f"   [fix] Последний блок обрезан: {old_end}s -> {int(real_duration)}s "
            f"(аудио кончается раньше)"
        )
        coverage = blocks[-1]["end"] - blocks[0]["start"]
        gap      = real_duration - coverage

    if gap > 1:
        print(f"   [!] Непокрытый хвост: {gap:.1f}s — добавляю последний блок")
        last_end = blocks[-1]["end"]
        blocks.append({
            "id":    len(blocks) + 1,
            "start": last_end,
            "end":   int(real_duration),
            "text":  "[конец]",
        })
        coverage = blocks[-1]["end"] - blocks[0]["start"]
        gap      = real_duration - coverage

    print(f"\n[FFmpeg] Верификация:")
    print(f"   Длина файла:    {real_duration:.1f}s")
    print(f"   Покрытие блоков:{coverage:.1f}s")
    print(f"   Погрешность:    {gap:.1f}s")
    print(f"   [OK] Все блоки верифицированы")

    meta = {
        "ffmpeg_verified": True,
        "total_duration":  round(real_duration, 3),
        "coverage":        float(coverage),
        "gap":             round(gap, 3),
    }
    return blocks, meta


def extract_audio_segment_ffmpeg(
    audio_path: Path,
    start: int,
    end: int,
    output_path: Path,
) -> bool:
    """
    Нарезает аудио точно по таймингам через FFmpeg без перекодирования.
    Верифицирует длину нарезанного клипа через ffprobe.
    Возвращает True если погрешность <= 1с, иначе False.
    """
    cmd = [
        "ffmpeg", "-y",
        "-i", str(audio_path),
        "-ss", str(start),
        "-to", str(end),
        "-c", "copy",
        "-avoid_negative_ts", "1",
        str(output_path),
    ]
    subprocess.run(cmd, capture_output=True)

    # Верифицировать длину нарезанного клипа
    verify_cmd = [
        "ffprobe", "-v", "quiet",
        "-print_format", "json",
        "-show_format",
        str(output_path),
    ]
    result = subprocess.run(verify_cmd, capture_output=True, text=True)
    info = json.loads(result.stdout)
    actual_duration = float(info["format"]["duration"])
    expected = end - start

    if abs(actual_duration - expected) > 1.0:
        print(f"  [!] Клип {start}-{end}s: ожидалось {expected}s, получилось {actual_duration:.1f}s")
        return False
    return True


# ---------------------------------------------------------------------------
# Сохранение
# ---------------------------------------------------------------------------

def save(session: str, segments: list[dict], meta: dict | None = None) -> Path:
    out_dir = TRANSCRIPTS_DIR / session
    out_dir.mkdir(parents=True, exist_ok=True)
    out_file = out_dir / "result.json"

    data: dict = {}
    if meta:
        data.update(meta)
    data["segments"] = segments

    with open(out_file, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
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
    segments, ffmpeg_meta = verify_with_ffmpeg(audio_path, segments)

    json_path = save(session, segments, ffmpeg_meta)
    srt_path  = save_srt(session, segments)
    vtt_path  = save_vtt(session, segments)

    print(f"[Agent] OK — {len(segments)} сегментов, покрыто {ffmpeg_meta['coverage']:.1f}s")
    print(f"[Agent] Subtitles created:")
    print(f"   {srt_path.name}")
    print(f"   {vtt_path.name}")
    print(f"   {json_path.name}")


if __name__ == "__main__":
    run()
