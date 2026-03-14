"""
ass_generator.py — генерация ASS субтитров из word-level данных result.json.

Слова группируются по 3-5 слов, каждая группа — одна строка субтитров.
Слова, начинающиеся до INTRO_DURATION секунд, пропускаются (интро без субтитров).

Использование:
  from ass_generator import generate_ass
  generate_ass("result.json", "subtitles.ass",
               font_name="Organetto", font_size=32,
               fade_in_ms=120, fade_out_ms=120, rise_px=20)
"""

import json
import os
import random
from pathlib import Path

# ── Загрузка .env ─────────────────────────────────────────────────────────────

_BASE_DIR = Path(__file__).resolve().parent.parent.parent
try:
    from dotenv import load_dotenv
    load_dotenv(_BASE_DIR / "config" / ".env")
except ImportError:
    pass

# Интро-зона: слова до этой отметки не отображаются (секунды)
INTRO_DURATION: float = 90.0


# ── Вспомогательные функции ───────────────────────────────────────────────────

def seconds_to_ass(seconds: float) -> str:
    """Конвертировать секунды в формат ASS времени: H:MM:SS.cc (сотые доли)."""
    seconds = max(0.0, seconds)
    h  = int(seconds // 3600)
    m  = int((seconds % 3600) // 60)
    s  = int(seconds % 60)
    cs = int((seconds % 1) * 100)
    return f"{h}:{m:02d}:{s:02d}.{cs:02d}"


# ── Главная функция ───────────────────────────────────────────────────────────

def generate_ass(
    result_json_path: "Path | str",
    output_ass_path:  "Path | str",
    font_name:   str = "Organetto",
    font_size:   int = 32,
    fade_in_ms:  int = 120,
    fade_out_ms: int = 120,
    rise_px:     int = 20,
) -> Path:
    """
    Генерирует ASS файл из word-level данных result.json.

    Слова группируются по 3-5 штук в одну строку субтитров.
    Слова до INTRO_DURATION секунд пропускаются.

    Параметры
    ---------
    result_json_path : путь к result.json
    output_ass_path  : куда сохранить .ass файл
    font_name        : имя шрифта
    font_size        : размер шрифта в пикселях
    fade_in_ms       : длительность fade-in в мс (default 120)
    fade_out_ms      : длительность fade-out в мс (default 120)
    rise_px          : подъём текста при появлении (пикселей, default 20)

    Возвращает Path к созданному .ass файлу.
    """
    result_json_path = Path(result_json_path)
    output_ass_path  = Path(output_ass_path)
    output_ass_path.parent.mkdir(parents=True, exist_ok=True)

    if not result_json_path.exists():
        raise FileNotFoundError(f"result.json не найден: {result_json_path}")

    with open(result_json_path, encoding="utf-8") as f:
        data = json.load(f)

    # Собираем слова: сначала top-level "words", потом из segments
    words = data.get("words", [])
    if not words:
        for seg in data.get("segments", []):
            words.extend(seg.get("words", []))

    words_filtered = [
        w for w in words
        if float(w.get("start", 0)) >= INTRO_DURATION
        and str(w.get("word", "")).strip()
    ]

    print(f"  ✅ Слов для ASS: {len(words_filtered)}")

    # Группируем строго по 3 слова
    groups = []
    i = 0
    while i < len(words_filtered):
        count = 3
        group = words_filtered[i:i + count]
        if group:
            groups.append({
                "start": float(group[0].get("start", 0)),
                "end":   float(group[-1].get("end", group[-1].get("start", 0) + 0.3)),
                "text":  " ".join(
                    str(w.get("word", "")).strip().upper()
                    for w in group
                    if str(w.get("word", "")).strip()
                )
            })
        i += count

    header = f"""[Script Info]
ScriptType: v4.00+
PlayResX: 1920
PlayResY: 1080
Timer: 100.0000
WrapStyle: 0

[V4+ Styles]
Format: Name,Fontname,Fontsize,PrimaryColour,SecondaryColour,OutlineColour,BackColour,Bold,Italic,Underline,StrikeOut,ScaleX,ScaleY,Spacing,Angle,BorderStyle,Outline,Shadow,Alignment,MarginL,MarginR,MarginV,Encoding
Style: Default,{font_name},{font_size},&H00FFFFFF,&H000000FF,&H00000000,&HBB1A0A2E,1,0,0,0,100,100,2,0,4,0,0,2,20,20,30,1

[Events]
Format: Layer,Start,End,Style,Name,MarginL,MarginR,MarginV,Effect,Text
"""

    lines = []
    for g in groups:
        start_s = g["start"]
        end_s   = g["end"]
        if end_s - start_s < 0.2:
            end_s = start_s + 0.2

        if not g["text"]:
            continue

        start_ass = seconds_to_ass(start_s)
        end_ass   = seconds_to_ass(end_s)

        x       = 960
        y_start = 1038 + rise_px
        y_end   = 1038

        tags = (
            f"{{\\an2"
            f"\\fad({fade_in_ms},{fade_out_ms})"
            f"\\move({x},{y_start},{x},{y_end},0,{fade_in_ms})"
            f"}}"
        )

        line = (f"Dialogue: 0,{start_ass},{end_ass},"
                f"Default,,0,0,0,,{tags}{g['text']}")
        lines.append(line)

    ass_content = header + "\n".join(lines) + "\n"

    with open(output_ass_path, "w", encoding="utf-8") as f:
        f.write(ass_content)

    print(f"  ✅ ASS готов: {len(lines)} групп субтитров")
    if lines[:3]:
        print(f"  📝 Первые 3:")
        for l in lines[:3]:
            print(f"    {l[:100]}")

    return output_ass_path


# ── CLI ───────────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    import argparse
    import sys

    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")

    parser = argparse.ArgumentParser(
        description="ass_generator — Групповые ASS субтитры из result.json"
    )
    parser.add_argument("--result-json", required=True, help="Путь к result.json")
    parser.add_argument("--output-ass",  required=True, help="Куда сохранить .ass")
    parser.add_argument("--font",        default="Organetto", help="Имя шрифта")
    parser.add_argument("--font-size",   type=int, default=32,  help="Размер шрифта px")
    parser.add_argument("--fade-in",     type=int, default=120, help="Fade-in мс")
    parser.add_argument("--fade-out",    type=int, default=120, help="Fade-out мс")
    parser.add_argument("--rise",        type=int, default=20,  help="Подъём px")
    args = parser.parse_args()

    try:
        generate_ass(
            result_json_path = args.result_json,
            output_ass_path  = args.output_ass,
            font_name        = args.font,
            font_size        = args.font_size,
            fade_in_ms       = args.fade_in,
            fade_out_ms      = args.fade_out,
            rise_px          = args.rise,
        )
    except Exception as e:
        print(f"❌ {e}", file=sys.stderr)
        sys.exit(1)
