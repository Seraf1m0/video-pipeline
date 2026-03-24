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
    font_name:        str   = "Organetto",
    font_size:        int   = 32,
    fade_in_ms:       int   = 120,
    fade_out_ms:      int   = 120,
    rise_px:          int   = 20,
    intro_duration:   float = INTRO_DURATION,
    max_line_chars:   int   = 28,
) -> Path:
    """
    Генерирует ASS файл из word-level данных result.json.

    Слова группируются по 3-5 штук в одну строку субтитров.
    Слова до intro_duration секунд пропускаются.

    Параметры
    ---------
    result_json_path : путь к result.json
    output_ass_path  : куда сохранить .ass файл
    font_name        : имя шрифта
    font_size        : размер шрифта в пикселях
    fade_in_ms       : длительность fade-in в мс (default 120)
    fade_out_ms      : длительность fade-out в мс (default 120)
    rise_px          : подъём текста при появлении (пикселей, default 20)
    intro_duration   : секунды с начала без субтитров (default INTRO_DURATION=90)
    max_line_chars   : максимальная длина строки в символах (default 28); если 3 слова
                       превышают лимит — используется 2 слова (целые слова, без обрезки)

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
        if float(w.get("start", 0)) >= intro_duration
        and str(w.get("word", "")).strip()
    ]

    # Мержим части слитных слов (Whisper разбивает "l'éclat" → "l" + "'éclat")
    merged: list[dict] = []
    for w in words_filtered:
        word_text = str(w.get("word", "")).strip()
        if word_text.startswith("'") and merged:
            prev = merged[-1]
            merged[-1] = {**prev,
                          "word": str(prev.get("word", "")).rstrip() + word_text,
                          "end":  w.get("end", prev.get("end", 0))}
        else:
            merged.append(w)
    words_filtered = merged

    print(f"  ✅ Слов для ASS: {len(words_filtered)}")

    # Группируем по 3 слова; если длина превышает max_line_chars — используем 2
    def _words_text(ws: list) -> str:
        return " ".join(str(w.get("word", "")).strip().upper() for w in ws
                        if str(w.get("word", "")).strip())

    groups = []
    i = 0
    while i < len(words_filtered):
        # Пробуем взять 3 слова, если 3 есть и они помещаются — берём 3, иначе 2
        for count in (3, 2, 1):
            group = words_filtered[i:i + count]
            if not group:
                break
            if count < 3 or len(_words_text(group)) <= max_line_chars:
                break
        if group:
            groups.append({
                "start": float(group[0].get("start", 0)),
                "end":   float(group[-1].get("end", group[-1].get("start", 0) + 0.3)),
                "text":  _words_text(group),
            })
        i += len(group) if group else 1

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


# ── Karaoke ASS (FR style) ─────────────────────────────────────────────────────

def generate_karaoke_ass(
    result_json_path: "Path | str",
    output_ass_path:  "Path | str",
    font_name:        str   = "Montserrat ExtraBold",
    font_size:        int   = 46,
    fade_ms:          int   = 60,
    rise_px:          int   = 120,
    intro_duration:   float = INTRO_DURATION,
    max_words:        int   = 3,
    col_active:       str   = "&H00FFFFFF",
    col_passive:      str   = "&H80AAAAAA",
) -> Path:
    """
    Генерирует karaoke-style ASS для FR канала.
    max_words слов на строку, word-by-word highlight (\\kf), КАПС, без обводки.
    Слова до intro_duration — пропускаются.
    """
    result_json_path = Path(result_json_path)
    output_ass_path  = Path(output_ass_path)

    with open(result_json_path, encoding="utf-8") as f:
        data = json.load(f)

    words = [w for w in data.get("words", []) if w.get("start", 0) >= intro_duration]
    if not words:
        raise RuntimeError("Нет word-level данных в result.json или все слова до intro_duration")

    # Мёрджим французские апострофные токены: "l'" + "'obscurité" → "l'obscurité"
    merged: list[dict] = []
    for w in words:
        token = w["word"]
        if merged and token.startswith("'"):
            prev = merged[-1]
            merged[-1] = {
                "word":  prev["word"].rstrip() + token,
                "start": prev["start"],
                "end":   w["end"],
            }
        else:
            merged.append(dict(w))
    words = merged

    header = (
        "[Script Info]\n"
        "ScriptType: v4.00+\n"
        "PlayResX: 1920\n"
        "PlayResY: 1080\n"
        "\n"
        "[V4+ Styles]\n"
        "Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, "
        "Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, "
        "Shadow, Alignment, MarginL, MarginR, MarginV, Encoding\n"
        f"Style: Karaoke,{font_name},{font_size},"
        f"{col_active},{col_passive},&H00000000,&H00000000,"
        f"-1,0,0,0,100,100,2,0,1,0,0,2,80,80,{rise_px},1\n"
        "\n"
        "[Events]\n"
        "Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text\n"
    )

    groups = [words[i:i+max_words] for i in range(0, len(words), max_words)]
    entries = []
    for group in groups:
        g_start = group[0]["start"]
        g_end   = group[-1]["end"]

        kara_parts = []
        for wi, w in enumerate(group):
            cs = max(1, int((w["end"] - w["start"]) * 100))
            if wi < len(group) - 1:
                gap = group[wi+1]["start"] - w["end"]
                if gap > 0:
                    cs += int(gap * 100)
            kara_parts.append(f"{{\\kf{cs}}}{w['word'].strip().upper()}")

        text = f"{{\\fad({fade_ms},{fade_ms})}}" + " ".join(kara_parts)
        entries.append(
            f"Dialogue: 0,{seconds_to_ass(g_start)},{seconds_to_ass(g_end)},"
            f"Karaoke,,0,0,0,,{text}"
        )

    output_ass_path.parent.mkdir(parents=True, exist_ok=True)
    output_ass_path.write_text(header + "\n".join(entries), encoding="utf-8")
    print(f"Karaoke ASS: {len(entries)} строк → {output_ass_path}")
    return output_ass_path
