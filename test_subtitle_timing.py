"""
test_subtitle_timing.py — изолированный тест субтитров GPU-рендера.

Тестирует:
  1. Y-позицию: короткий текст (1 строка) vs длинный (2 строки) — должны быть на одном уровне
  2. Ограничение 12 символов
  3. slide_up анимацию — текст не должен уходить за экран
  4. Timing: субтитры появляются в правильный момент относительно начала видео

Запуск:
    python test_subtitle_timing.py [--session SESSION_DIR] [--out OUT_DIR]

Берёт 5 рандомных клипов из DE-сессии, собирает 30-секундный тест,
рисует субтитры с известными тестовыми строками.
"""
import argparse
import random
import subprocess
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent / "agents" / "assembler"))
from gpu_compositor import run, ANIM_SLIDE_UP, ANIM_FADE

# ─── Настройки по умолчанию ───────────────────────────────────────────────────
DEFAULT_SESSIONS_DIR = Path("D:/Video-pipeline-data/channels/de")
OUT_DIR              = Path("D:/Video-pipeline-temp")
FPS                  = 25.0
FONT_PATH            = "C:/Users/Serafim/AppData/Local/Microsoft/Windows/Fonts/MONTSERRAT-EXTRABOLD.TTF"
FONT_SIZE            = 52
FADE_MS              = 120

# ─── Тестовые субтитры ────────────────────────────────────────────────────────
# Намеренно разнообразные по длине, чтобы показать текущие баги:
# - Короткие (< 12 символов) → 1 строка → TOP y=874
# - Длинные (> 12 символов)  → 2 строки → TOP y=874, но bottom = 1004 (прыжок 65px!)
# Временны́е метки в секундах, с привязкой к клипам:
# - Клип 1: 0-5s, Клип 2: 5-10s, ... (зависит от реальных длительностей)
# Пока используем синтетические временны́е метки на фиксированных позициях

def make_test_ass(duration: float, output: Path) -> Path:
    """
    Создаёт ASS с тестовыми субтитрами по всей длине видео.

    Структура теста:
      [0-2s]    KURZ        — 4 символа, 1 строка (короткое слово)
      [2-4s]    AUF         — 3 символа, 1 строка (очень короткое)
      [4-6s]    STERN       — 5 символов, 1 строка
      [6-8s]    MILCH WEG   — 8 символов, 1 строка
      [8-10s]   STERNE AM   — 9 символов, 1 строка
      [10-12s]  DIE ERDE IST — 11 символов, 1 строка (на границе)
      [12-14s]  KOSMISCHE WELT — 14 символов → ДОЛЖНО стать 2 строки при текущем коде
      [14-16s]  SCHWARZES LOCH  — 14 символов → 2 строки сейчас
      [16-18s]  IM UNIVERSUM DER — 16 символов → 2 строки сейчас
      [18-20s]  NEUE WELT   — 9 символов, 1 строка (после "прыгнул" текст)
      [20-22s]  LICHT       — 5 символов, 1 строка
      [22-24s]  GROSSE EXPLOSION — 17 символов → явно 2 строки сейчас

      Временны́е метки выбраны так, чтобы несколько субтитров начинались
      строго на границах клипов (6s, 12s, 18s) — там где slide_up может обрезаться.
    """
    def ass_t(secs: float) -> str:
        h  = int(secs // 3600)
        m  = int((secs % 3600) // 60)
        s  = int(secs % 60)
        cs = int((secs % 1) * 100)
        return f"{h}:{m:02d}:{s:02d}.{cs:02d}"

    # Реалистичные субтитры ≤15 символов — как будет в реальных видео после фикса.
    # Все однострочные, нижний край должен быть на одном уровне.
    subs = [
        (0.5,  2.0,  "IM KOSMOS"),        # 8ch  — 2 слова
        (2.3,  4.0,  "SCHWARZE WELT"),    # 13ch — 2 слова
        (4.3,  6.0,  "LICHT"),            # 5ch  — 1 слово
        (6.1,  8.0,  "IM ALL"),           # 6ch  — 2 слова
        (8.3,  10.0, "STERNE AM"),        # 9ch  — 2 слова
        (10.3, 12.0, "DIE ERDE"),         # 8ch  — 2 слова
        (12.1, 14.0, "NEUE WELT"),        # 9ch  — 2 слова
        (14.3, 16.0, "STERNENLICHT"),     # 12ch — 1 слово (длинное)
        (16.3, 18.0, "AUF"),              # 3ch  — 1 слово
        (18.1, 20.0, "GROSS UND WEIT"),   # 13ch — 3 слова (влезает в 15)
        (20.3, 22.0, "MILCHSTRASSE"),     # 12ch — 1 слово
        (22.3, min(24.0, duration - 0.5), "IM WELTALL"),  # 10ch — 2 слова
    ]
    # Фильтруем по длительности видео
    subs = [(s, e, t) for s, e, t in subs if s < duration - 0.5]

    header = (
        "[Script Info]\n"
        "ScriptType: v4.00+\n"
        "PlayResX: 1920\n"
        "PlayResY: 1080\n\n"
        "[V4+ Styles]\n"
        "Format: Name,Fontname,Fontsize,PrimaryColour,SecondaryColour,OutlineColour,BackColour,"
        "Bold,Italic,Underline,StrikeOut,ScaleX,ScaleY,Spacing,Angle,BorderStyle,Outline,Shadow,"
        "Alignment,MarginL,MarginR,MarginV,Encoding\n"
        "Style: Default,Montserrat Bold,52,&H00FFFFFF,&H000000FF,&H80000000,&H00000000,"
        "-1,0,0,0,100,100,2,0,1,3,1,2,80,80,30,1\n\n"
        "[Events]\n"
        "Format: Layer,Start,End,Style,Name,MarginL,MarginR,MarginV,Effect,Text\n"
    )

    lines = []
    for s, e, text in subs:
        lines.append(
            f"Dialogue: 0,{ass_t(s)},{ass_t(e)},Default,,0,0,0,,{text}"
        )

    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(header + "\n".join(lines) + "\n", encoding="utf-8")
    print(f"  ASS: {len(lines)} субтитров → {output.name}")
    return output


def pick_clips(sessions_dir: Path, count: int = 5, clip_dur: float = 6.0) -> list[Path]:
    """Берёт `count` рандомных клипов из разных сессий."""
    sessions = [d for d in sessions_dir.iterdir() if d.is_dir()]
    if not sessions:
        raise FileNotFoundError(f"Нет сессий в {sessions_dir}")

    random.shuffle(sessions)
    clips = []
    for sess in sessions:
        for clips_dir in [sess / "intro_clips", sess / "input"]:
            if not clips_dir.exists():
                continue
            found = sorted(clips_dir.glob("*.mp4"))
            if found:
                clips.append(random.choice(found))
                break
        if len(clips) >= count:
            break

    if not clips:
        raise FileNotFoundError(f"Нет .mp4 клипов в {sessions_dir}")

    print(f"  Выбрано {len(clips)} клипов:")
    for c in clips:
        print(f"    {c.parent.parent.name}/{c.parent.name}/{c.name}")
    return clips


def concat_clips(clips: list[Path], out: Path, clip_dur: float = 6.0) -> float:
    """
    FFmpeg concat: берём первые clip_dur секунд из каждого клипа,
    склеиваем в один файл без перекодирования (если одинаковый кодек).
    Возвращает реальную длительность.
    """
    # Обрезаем каждый клип до clip_dur
    trimmed = []
    for i, c in enumerate(clips):
        t = out.parent / f"_trim_{i:02d}.mp4"
        subprocess.run([
            "ffmpeg", "-hide_banner", "-loglevel", "error", "-y",
            "-i", str(c),
            "-t", str(clip_dur),
            "-c:v", "h264_nvenc", "-preset", "p1",
            "-pix_fmt", "yuv420p", "-an",
            str(t),
        ], check=True)
        trimmed.append(t)

    # concat через list file
    lst = out.parent / "_concat_list.txt"
    lst.write_text("\n".join(f"file '{p}'" for p in trimmed), encoding="utf-8")

    subprocess.run([
        "ffmpeg", "-hide_banner", "-loglevel", "error", "-y",
        "-f", "concat", "-safe", "0",
        "-i", str(lst),
        "-c", "copy",
        str(out),
    ], check=True)

    # Убираем временные файлы
    for t in trimmed:
        t.unlink(missing_ok=True)
    lst.unlink(missing_ok=True)

    # Узнаём реальную длину
    r = subprocess.run([
        "ffprobe", "-v", "error", "-select_streams", "v:0",
        "-show_entries", "stream=duration", "-of", "csv=p=0",
        str(out)
    ], capture_output=True, text=True)
    dur = float(r.stdout.strip() or 0)
    print(f"  Videotrack: {out.name} ({dur:.1f}s)")
    return dur


def main():
    parser = argparse.ArgumentParser(description="Test GPU subtitle rendering")
    parser.add_argument("--sessions", default=str(DEFAULT_SESSIONS_DIR),
                        help="Папка с DE сессиями")
    parser.add_argument("--out", default=str(OUT_DIR), help="Выходная папка")
    parser.add_argument("--clips", type=int, default=5, help="Кол-во клипов")
    parser.add_argument("--clip-dur", type=float, default=6.0,
                        help="Длина каждого клипа (сек)")
    parser.add_argument("--seed", type=int, default=None, help="Random seed")
    args = parser.parse_args()

    if args.seed is not None:
        random.seed(args.seed)

    out_dir = Path(args.out)
    out_dir.mkdir(parents=True, exist_ok=True)

    print("\n" + "=" * 60)
    print("TEST: GPU subtitle rendering")
    print("=" * 60)

    # 1. Подбираем клипы
    print("\n[1/4] Подбираем клипы...")
    clips = pick_clips(Path(args.sessions), count=args.clips,
                       clip_dur=args.clip_dur)

    # 2. Собираем videotrack
    print("\n[2/4] Собираем videotrack...")
    videotrack = out_dir / "_test_sub_timing_track.mp4"
    duration = concat_clips(clips, videotrack, clip_dur=args.clip_dur)

    # 3. Генерируем ASS
    print("\n[3/4] Генерируем тестовый ASS...")
    ass_path = out_dir / "_test_sub_timing.ass"
    make_test_ass(duration, ass_path)

    # 4. Рендерим: два варианта — slide_up (текущий) и fade (для сравнения)
    print("\n[4/4] Рендер...")

    results = []
    for anim_id, label in [(ANIM_SLIDE_UP, "slide_up"), (ANIM_FADE, "fade")]:
        out = out_dir / f"test_sub_timing_{label}.mp4"
        print(f"\n  [{label}] → {out.name}")
        t0 = time.time()

        ok = run(
            videotrack       = videotrack,
            output           = out,
            intro            = None,
            audio            = None,
            ass_path         = ass_path,
            color_grade_name = "cool_cinematic",
            fps              = FPS,
            intro_trans_dur  = 0.0,
            font_size        = FONT_SIZE,
            fade_ms          = FADE_MS,
            font_path        = FONT_PATH,
            subtitle_style   = "default",
            subtitle_anim    = anim_id,
        )

        elapsed = time.time() - t0
        size_mb = out.stat().st_size // 1024 // 1024 if out.exists() else 0
        status  = "OK" if ok else "FAIL"
        results.append((label, status, elapsed, size_mb, out))
        print(f"  {status} {elapsed:.1f}s | {size_mb}MB")

    # ── Итог ──────────────────────────────────────────────────────────────────
    print("\n" + "=" * 60)
    print("РЕЗУЛЬТАТ:")
    for label, status, t, mb, out in results:
        print(f"  {status}  {label:<12}  {t:.1f}s  {mb}MB  → {out.name}")

    print(f"\nЧТО ПРОВЕРЯТЬ В ВИДЕО:")
    print("  Y-позиция: все субтитры на одном уровне снизу (bottom anchor)")
    print("  Все однострочные, макс. 13 символов")
    print("  slide_up: текст плавно выезжает снизу, не уходит за край экрана")
    print("  [6.1s, 12.1s] — субтитр сразу после склейки клипа (проверь анимацию)")
    print(f"\nФайлы: {out_dir}")

    # Чистим videotrack если успешно
    if all(s == "OK" for _, s, _, _, _ in results):
        videotrack.unlink(missing_ok=True)


if __name__ == "__main__":
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    main()
