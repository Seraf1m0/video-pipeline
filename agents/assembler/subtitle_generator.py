#!/usr/bin/env python3
"""
subtitle_generator.py — генерация word-level SRT субтитров из result.json

Использует точные тайминги каждого слова (word_timestamps=True в Whisper).
Группирует слова по ~max_chars символов с реальными start/end.

Использование:
    py agents/assembler/subtitle_generator.py --session Video_20260307_222353
    py agents/assembler/subtitle_generator.py result.json output.srt
"""

import json
import sys
import io
from pathlib import Path

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")


def _format_srt_time(seconds: float) -> str:
    """float секунды → HH:MM:SS,mmm"""
    h  = int(seconds // 3600)
    m  = int((seconds % 3600) // 60)
    s  = int(seconds % 60)
    ms = int(round((seconds % 1) * 1000))
    if ms >= 1000:
        ms = 999
    return f"{h:02d}:{m:02d}:{s:02d},{ms:03d}"


def generate_word_srt(result_json_path: Path, output_srt_path: Path,
                      max_chars: int = 12, preview: int = 10):
    """
    Генерирует word-level SRT из result.json.

    Берёт поле "words" с реальными таймингами каждого слова (Whisper word_timestamps=True).
    Группирует слова по ~max_chars символов — каждая группа получает точный
    start/end из аудио (не равномерное распределение).

    Возвращает количество субтитров или False если нет word-level данных.
    """
    result_json_path = Path(result_json_path)
    output_srt_path  = Path(output_srt_path)

    with open(result_json_path, encoding="utf-8") as f:
        data = json.load(f)

    words = data.get("words", [])

    if not words:
        session = result_json_path.parent.name
        print("Нет word-level данных в result.json")
        print("Запусти транскрибатор заново с word_timestamps=True:")
        print(f"  py agents/transcriber/transcriber.py \\")
        print(f"     --input data/input/{session}/*.mp3 \\")
        print(f"     --mode grok")
        return False

    # Группируем слова по ~max_chars символов с реальными таймингами
    groups = []
    current_words: list[dict] = []
    current_chars = 0
    current_start = None

    for w in words:
        word_text = w["word"].strip()
        if not word_text:
            continue

        if current_start is None:
            current_start = w["start"]

        added = len(word_text) + (1 if current_words else 0)

        if current_chars + added > max_chars and current_words:
            # Закрываем группу — end = реальный конец последнего слова
            groups.append({
                "start": current_start,
                "end":   current_words[-1]["end"],
                "text":  " ".join(cw["word"].strip() for cw in current_words),
            })
            current_words = [w]
            current_chars = len(word_text)
            current_start = w["start"]
        else:
            current_words.append(w)
            current_chars += added

    # Последняя группа
    if current_words:
        groups.append({
            "start": current_start,
            "end":   current_words[-1]["end"],
            "text":  " ".join(cw["word"].strip() for cw in current_words),
        })

    INTRO_DURATION = 90  # секунд = 1:30
    groups_filtered = [g for g in groups if g["start"] >= INTRO_DURATION]
    print(f"Пропущено до 1:30: {len(groups) - len(groups_filtered)}")
    print(f"Субтитры с 1:30: {len(groups_filtered)}")

    # Записываем SRT (только после интро, нумерация с 1)
    entries: list[str] = []
    for i, g in enumerate(groups_filtered, 1):
        entries.append(
            f"{i}\n"
            f"{_format_srt_time(g['start'])} --> {_format_srt_time(g['end'])}\n"
            f"{g['text'].upper()}\n"
        )
        if i <= preview:
            print(f"  #{i:4d}: {_format_srt_time(g['start'])} | {g['text']}")

    output_srt_path.parent.mkdir(parents=True, exist_ok=True)
    output_srt_path.write_text("\n".join(entries), encoding="utf-8")

    print(f"\nWord-level SRT: {len(groups_filtered)} субтитров → {output_srt_path}")
    return len(groups_filtered)


# ─── CLI ─────────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    import argparse

    BASE_DIR = Path(__file__).parent.parent.parent

    parser = argparse.ArgumentParser(description="Word-level SRT из result.json")
    parser.add_argument("--session", "-s", default=None,
                        help="Сессия (Video_20260307_222353) — автоматически находит пути")
    parser.add_argument("--result-json", default=None, help="Путь к result.json")
    parser.add_argument("--output-srt",  default=None, help="Путь к выходному .srt")
    parser.add_argument("--max-chars",   type=int, default=12,
                        help="Макс. символов на группу (default: 12)")
    parser.add_argument("--preview",     type=int, default=10,
                        help="Первые N субтитров в stdout (default: 10)")
    args = parser.parse_args()

    if args.session:
        result_json = BASE_DIR / "data" / "transcripts" / args.session / "result.json"
        output_srt  = BASE_DIR / "data" / "transcripts" / args.session / "subtitles_words.srt"
    elif args.result_json and args.output_srt:
        result_json = Path(args.result_json)
        output_srt  = Path(args.output_srt)
    else:
        # Берём последнюю сессию автоматически
        transcripts_dir = BASE_DIR / "data" / "transcripts"
        sessions = sorted(transcripts_dir.glob("Video_*"), key=lambda p: p.name)
        if not sessions:
            print("Ошибка: нет сессий в data/transcripts/")
            sys.exit(1)
        last = sessions[-1]
        result_json = last / "result.json"
        output_srt  = last / "subtitles_words.srt"
        print(f"Авто-сессия: {last.name}")

    if not result_json.exists():
        print(f"Ошибка: {result_json} не найден")
        sys.exit(1)

    print(f"Сессия: {result_json.parent.name}")
    print(f"Вход:   {result_json}")
    print(f"Выход:  {output_srt}")
    print(f"Max символов: {args.max_chars}")
    print(f"Первые {args.preview} субтитров:")
    print()

    result = generate_word_srt(result_json, output_srt,
                               max_chars=args.max_chars, preview=args.preview)
    if result is False:
        sys.exit(1)
