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
import sys
from pathlib import Path

# UTF-8 stdout — фикс emoji на Windows cp1251
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
if hasattr(sys.stderr, "reconfigure"):
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")

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
    border_style:     int   = 1,   # 1=outline+shadow, 3=opaque box (плашка)
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

    # Fallback: нет word-level данных — разбиваем сегменты на псевдо-слова
    if not words:
        for seg in data.get("segments", []):
            seg_start = float(seg.get("start", 0))
            seg_end   = float(seg.get("end", seg_start))
            seg_text  = str(seg.get("text", "")).strip()
            if not seg_text:
                continue
            tokens = seg_text.split()
            if not tokens:
                continue
            dur = (seg_end - seg_start) / len(tokens)
            for i, tok in enumerate(tokens):
                words.append({
                    "word":  tok,
                    "start": round(seg_start + i * dur, 3),
                    "end":   round(seg_start + (i + 1) * dur, 3),
                })

    words_filtered = [
        w for w in words
        if float(w.get("start", 0)) >= intro_duration
        and str(w.get("word", "")).strip()
    ]

    # Мержим части слитных слов (Whisper BPE: токен без leading space = продолжение)
    # Примеры: " C" + "'est" → "C'est",  " années" + "-lumière." → "années-lumière."
    merged: list[dict] = []
    for w in words_filtered:
        raw       = str(w.get("word", ""))
        has_space = raw.startswith(" ")
        word_text = raw.strip()
        if not has_space and merged:
            # Нет пробела в начале → продолжение предыдущего слова
            prev = merged[-1]
            merged[-1] = {**prev,
                          "word": str(prev.get("word", "")).rstrip() + word_text,
                          "end":  w.get("end", prev.get("end", 0))}
        else:
            merged.append({**w, "word": word_text})
    words_filtered = merged

    print(f"  ✅ Слов для ASS: {len(words_filtered)}")

    # Группируем: до 3 слов, жёсткий лимит MAX_HARD_CHARS символов.
    # Логика: пробуем 3 → 2 → 1 слово; берём первое, что влазит в лимит.
    # 1 слово берём всегда (нельзя дробить слова).
    MAX_HARD_CHARS = 15

    def _words_text(ws: list) -> str:
        return " ".join(str(w.get("word", "")).strip().upper() for w in ws
                        if str(w.get("word", "")).strip())

    groups = []
    i = 0
    while i < len(words_filtered):
        group = None
        for count in (3, 2, 1):
            candidate = words_filtered[i:i + count]
            if not candidate:
                break
            if count == 1 or len(_words_text(candidate)) <= MAX_HARD_CHARS:
                group = candidate
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
Style: Default,{font_name},{font_size},&H00FFFFFF,&H000000FF,&H80000000,&H00000000,1,0,0,0,100,100,2,0,{border_style},{3 if border_style==1 else 2},{1 if border_style==1 else 0},2,80,80,30,1

[Events]
Format: Layer,Start,End,Style,Name,MarginL,MarginR,MarginV,Effect,Text
"""

    y_pos = 1038   # bottom anchor (Alignment=2 = bottom-center)
    lines = []
    for g in groups:
        # Content-relative: сдвигаем -intro_duration (0 = конец интро = начало контента)
        start_s = g["start"] - intro_duration
        end_s   = g["end"]   - intro_duration
        if end_s <= 0.02:
            continue  # внутри интро

        if end_s - start_s < 0.2:
            end_s = start_s + 0.2

        if not g["text"]:
            continue

        start_ass = seconds_to_ass(start_s)
        end_ass   = seconds_to_ass(end_s)

        x       = 960
        y_start = y_pos + rise_px   # начальная Y при появлении (снизу вверх)
        y_end   = y_pos             # конечная Y (bottom anchor)

        tags = (
            f"{{\\an2"
            f"\\pos({x},{y_end})"
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
    col_active:       str   = "&H0000FFFF",   # жёлтый (ABGR: AA=00, BB=00, GG=FF, RR=FF)
    col_passive:      str   = "&H00707070",
    border_style:     int   = 1,   # 1=outline+shadow, 3=opaque box (плашка)
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

    if words and words[0]["start"] < intro_duration - 0.1:
        raise RuntimeError(
            f"KARAOKE BUG: first word at {words[0]['start']:.2f}s < intro_duration {intro_duration:.2f}s"
        )

    # Fallback для сегментов без word-level данных — интерполируем по времени.
    # Используем per-segment coverage check вместо naive covered_end pointer,
    # чтобы не пропускать первые слова при частичном word-level выводе Whisper.
    _word_buckets: set[int] = set()
    for w in words:
        for _b in range(int(w["start"] * 10), int(w["end"] * 10) + 1):
            _word_buckets.add(_b)

    for seg in sorted(data.get("segments", []), key=lambda s: float(s.get("start", 0))):
        seg_start = float(seg.get("start", 0))
        seg_end   = float(seg.get("end", seg_start))
        seg_text  = str(seg.get("text", "")).strip()
        if seg_start < intro_duration:
            continue
        if not seg_text or seg_text.startswith("["):
            continue
        # Пропускаем сегмент если его начало уже покрыто word-level данными Whisper.
        # Раньше проверяли seg_mid — это давало overlap: сегмент 01:50-01:53 с
        # midpoint=01:51.70 (не в buckets) генерировал дубликаты поверх реальных слов.
        seg_covered_end = seg_start + (seg_end - seg_start) * 0.4
        if any(b in _word_buckets
               for b in range(int(seg_start * 10), int(seg_covered_end * 10) + 1)):
            continue  # первые 40% сегмента покрыты — не нужен fallback
        tokens = seg_text.split()
        if not tokens:
            continue
        dur = (seg_end - seg_start) / len(tokens)
        for i, tok in enumerate(tokens):
            w_start = round(seg_start + i * dur, 3)
            w_end   = round(seg_start + (i + 1) * dur, 3)
            # Leading space обязателен: BPE merge считает слова без space продолжением
            # предыдущего слова → склеивает их → "EXCLUANTSIGMA,EXCLUANT" и т.п.
            words.append({"word": " " + tok, "start": w_start, "end": w_end})
            for _b in range(int(w_start * 10), int(w_end * 10) + 1):
                _word_buckets.add(_b)

    if not words:
        raise RuntimeError("Нет word-level данных в result.json или все слова до intro_duration")

    # Сортируем по времени — fallback слова добавляются в конец, но должны быть на своём месте
    words.sort(key=lambda w: float(w.get("start", 0)))

    # Фильтруем слова из одних знаков препинания (?, !, ..., , и т.п.)
    # Такие токены Whisper выдаёт как отдельные слова → выглядят как одиночный символ
    # на другой высоте в karaoke-строке и ломают группировку.
    words = [w for w in words if any(c.isalpha() or c.isdigit() for c in w.get("word", ""))]

    # Мёрджим Whisper BPE-токены без leading space = продолжение предыдущего слова
    # " années" + "-lumière." → "années-lumière."   " C" + "'est" → "C'est"
    merged: list[dict] = []
    for w in words:
        raw       = w["word"]
        has_space = raw.startswith(" ")
        token     = raw.strip()
        if not has_space and merged:
            prev = merged[-1]
            merged[-1] = {
                "word":  prev["word"].rstrip() + token,
                "start": prev["start"],
                "end":   w["end"],
            }
        else:
            merged.append({**w, "word": token})
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
        f"{col_active},{col_passive},&H80000000,&H00000000,"
        f"-1,0,0,0,100,100,2,0,{border_style},0,0,2,80,80,{rise_px},1\n"
        "\n"
        "[Events]\n"
        "Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text\n"
    )

    groups = [words[i:i+max_words] for i in range(0, len(words), max_words)]
    entries = []
    pos_y = 1080 - rise_px   # bottom anchor Y (bottom-center, Alignment=2)
    for group in groups:
        # Content-relative times: сдвигаем -intro_duration.
        # GPU compositor читает эти времена как "секунды от начала контента" (t=0 = конец интро).
        # Это устраняет дрейф, который возникал при вычитании intro_frames в compositor-е.
        g_start = group[0]["start"]  - intro_duration
        g_end   = group[-1]["end"]   - intro_duration
        if g_end <= 0.02:
            continue  # группа полностью внутри интро — пропускаем

        # \kf — ДЛИТЕЛЬНОСТИ в сантисекундах, не абсолютные времена → не нуждаются в сдвиге
        kara_parts = []
        for wi, w in enumerate(group):
            cs = max(1, int((w["end"] - w["start"]) * 100))
            if wi < len(group) - 1:
                gap = group[wi+1]["start"] - w["end"]
                if gap > 0:
                    cs += int(gap * 100)
            kara_parts.append(f"{{\\kf{cs}}}{w['word'].strip().upper()}")

        pos   = f"{{\\pos(960,{pos_y})}}"   # bottom-center anchor (Alignment=2 в стиле)
        fad   = f"{{\\fad({fade_ms},{fade_ms})}}" if fade_ms > 0 else ""
        text  = pos + fad + " ".join(kara_parts)
        entries.append(
            f"Dialogue: 0,{seconds_to_ass(g_start)},{seconds_to_ass(g_end)},"
            f"Karaoke,,0,0,0,,{text}"
        )

    output_ass_path.parent.mkdir(parents=True, exist_ok=True)
    output_ass_path.write_text(header + "\n".join(entries), encoding="utf-8")
    print(f"Karaoke ASS: {len(entries)} строк → {output_ass_path}")
    return output_ass_path


# ── Scripture ASS (ES religion style) ─────────────────────────────────────────

def generate_scripture_ass(
    result_json_path: "Path | str",
    output_ass_path:  "Path | str",
    font_name:        str   = "Montserrat ExtraBold",
    font_size:        int   = 54,
    fade_in_ms:       int   = 350,
    fade_out_ms:      int   = 200,
    intro_duration:   float = 0.0,
    max_words:        int   = 3,
    pos_y:            int   = 900,   # bottom anchor Y for \pos(960, pos_y)
) -> Path:
    """
    Scripture-style ASS для religion канала.

    - Группы по 2–3 слова (короче, читабельнее)
    - Позиция: нижняя треть экрана, центр (\\an2, \\pos(960,pos_y))
    - Fade только: нет движения, нет karaoke-highlight
    - Italic, без бокса, только тень
    - Заглавные буквы
    - Тайминги content-relative: время -= intro_duration
    """
    result_json_path = Path(result_json_path)
    output_ass_path  = Path(output_ass_path)

    with open(result_json_path, encoding="utf-8") as f:
        data = json.load(f)

    # Собираем word-level данные (как в generate_ass)
    words = data.get("words", [])
    if not words:
        for seg in data.get("segments", []):
            words.extend(seg.get("words", []))
    if not words:
        for seg in data.get("segments", []):
            seg_s = float(seg.get("start", 0))
            seg_e = float(seg.get("end", seg_s))
            tokens = str(seg.get("text", "")).strip().split()
            if not tokens:
                continue
            dur = (seg_e - seg_s) / len(tokens)
            for i, tok in enumerate(tokens):
                words.append({"word": tok,
                               "start": round(seg_s + i * dur, 3),
                               "end":   round(seg_s + (i + 1) * dur, 3)})

    words = [w for w in words
             if float(w.get("start", 0)) >= intro_duration
             and str(w.get("word", "")).strip()]

    # Content-relative: сдвигаем тайминги на -intro_duration
    if intro_duration > 0:
        words = [{**w,
                  "start": round(float(w["start"]) - intro_duration, 3),
                  "end":   round(float(w["end"])   - intro_duration, 3)}
                 for w in words]

    # Мёрдж BPE-токенов
    merged: list[dict] = []
    for w in words:
        raw = str(w.get("word", ""))
        if not raw.startswith(" ") and merged:
            merged[-1] = {**merged[-1],
                          "word": merged[-1]["word"].rstrip() + raw.strip(),
                          "end":  w.get("end", merged[-1]["end"])}
        else:
            merged.append({**w, "word": raw.strip()})
    words = [w for w in merged if w.get("word", "").strip()]

    # Группировка: до max_words слов, жёсткий лимит 15 символов.
    # 1 слово берётся всегда (нельзя дробить слова).
    MAX_HARD_CHARS = 15
    groups: list[dict] = []
    i = 0
    while i < len(words):
        grp = None
        for n in (max_words, 2, 1):
            candidate = words[i:i + n]
            if not candidate:
                break
            txt_candidate = " ".join(
                w["word"].upper() for w in candidate if w.get("word", "").strip()
            )
            if n == 1 or len(txt_candidate) <= MAX_HARD_CHARS:
                grp = candidate
                break
        if not grp:
            i += 1
            continue
        txt = " ".join(w["word"].upper() for w in grp if w.get("word", "").strip())
        if txt:
            groups.append({
                "start": float(grp[0]["start"]),
                "end":   float(grp[-1]["end"]),
                "text":  txt,
            })
        i += len(grp) if grp else 1

    header = f"""[Script Info]
ScriptType: v4.00+
PlayResX: 1920
PlayResY: 1080
Timer: 100.0000
WrapStyle: 2

[V4+ Styles]
Format: Name,Fontname,Fontsize,PrimaryColour,SecondaryColour,OutlineColour,BackColour,Bold,Italic,Underline,StrikeOut,ScaleX,ScaleY,Spacing,Angle,BorderStyle,Outline,Shadow,Alignment,MarginL,MarginR,MarginV,Encoding
Style: Scripture,{font_name},{font_size},&H00FFFFFF,&H000000FF,&H00000000,&H80000000,1,0,0,0,100,100,3,0,1,0,2,2,80,80,0,1

[Events]
Format: Layer,Start,End,Style,Name,MarginL,MarginR,MarginV,Effect,Text
"""

    lines = []
    for j, g in enumerate(groups):
        s = g["start"]
        # Extend end to next group start (no blank gaps between subtitle frames)
        if j < len(groups) - 1:
            e = min(g["end"] + 1.5, groups[j + 1]["start"] - 0.04)
        else:
            e = g["end"]
        e = max(e, s + 0.25)
        tags = (
            f"{{\\an2"
            f"\\pos(960,{pos_y})"
            f"\\fad({fade_in_ms},{fade_out_ms})"
            f"}}"
        )
        lines.append(
            f"Dialogue: 0,{seconds_to_ass(s)},{seconds_to_ass(e)},"
            f"Scripture,,0,0,0,,{tags}{g['text']}"
        )

    output_ass_path.parent.mkdir(parents=True, exist_ok=True)
    output_ass_path.write_text(header + "\n".join(lines) + "\n", encoding="utf-8")
    print(f"Scripture ASS: {len(lines)} строк → {output_ass_path}")
    return output_ass_path
