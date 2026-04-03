"""
test_fr_sfx_mini.py — мини-тест видео FR канала.

Нарезает финальный рендер по окнам:
  1. Концовка интро → начало основного контента (10s до + 5s после)
  2. Каждый SFX event: (event_t - 5s) → (event_t + riser_dur + 1s)

Добавляет плашки "Подписаться" (CTA) на нужных позициях.

Результат: ~2-3 мин видео для быстрой проверки:
  - SFX звучат корректно (уровни, тайминги)
  - Субтитры не сбиваются на переходах
  - CTA появляются
  - Переходы + общий флоу

Запуск:
    python test_fr_sfx_mini.py
    python test_fr_sfx_mini.py --no-cta    # без плашек
"""

import argparse
import json
import subprocess
import sys
import time
from pathlib import Path

sys.stdout.reconfigure(encoding="utf-8", errors="replace")

# ─── Пути ────────────────────────────────────────────────────────────────────
SESSION       = Path(r"D:\Video-pipeline-data\channels\fr\Video_20260402_180801")
FINAL_VIDEO   = SESSION / "output" / "Video_20260402_180801_FR_final.mp4"
RESULT_JSON   = SESSION / "transcripts" / "result_visual.json"
CTA_MOV       = Path(r"D:\Video-pipeline-temp\test_cta_fr.mov")
APPLY_CTA     = Path(r"C:\Projects\Video-pipeline\agents\assembler\apply_cta.py")

OUT_DIR       = Path(r"D:\Video-pipeline-temp\test_sfx_mini_fr")

INTRO_DUR_S   = 91.0    # длительность интро в секундах
RISER_DUR_S   = 4.5     # длительность riser-файла (4.5s по spec)
BOOM_DUR_S    = 1.5     # примерная длительность boom
IMPACT_DUR_S  = 1.5
PRE_S         = 5.0     # секунд ДО события
POST_S        = 1.5     # секунд ПОСЛЕ окончания SFX


def ffprobe_dur(path: Path) -> float:
    r = subprocess.run(
        ["ffprobe", "-v", "error", "-select_streams", "v:0",
         "-show_entries", "stream=duration", "-of", "csv=p=0", str(path)],
        capture_output=True, text=True,
    )
    try:
        return float(r.stdout.strip())
    except ValueError:
        return 0.0


def cut_segment(src: Path, start: float, end: float, out: Path) -> bool:
    """
    Вырезает сегмент с re-encode (NVENC).
    Stream-copy (-c copy) при срезке не на ключевых кадрах даёт NAL errors
    и повреждённые первые кадры при последующем concat.
    """
    dur = end - start
    if dur <= 0:
        return False
    out.parent.mkdir(parents=True, exist_ok=True)
    r = subprocess.run([
        "ffmpeg", "-hide_banner", "-loglevel", "error", "-y",
        "-ss", f"{start:.3f}", "-t", f"{dur:.3f}",
        "-i", str(src),
        "-c:v", "h264_nvenc", "-preset", "p4", "-cq", "22",
        "-c:a", "aac", "-b:a", "192k",
        str(out),
    ])
    return r.returncode == 0


def add_title_card(text: str, duration: float, out: Path):
    """Создаёт чёрный кадр с текстом как разделитель между клипами."""
    font = "C\\:/Windows/Fonts/arialbd.ttf"
    safe_text = text.replace(":", "\\:")
    out.parent.mkdir(parents=True, exist_ok=True)
    subprocess.run([
        "ffmpeg", "-hide_banner", "-loglevel", "error", "-y",
        "-f", "lavfi",
        "-i", f"color=c=black:s=1920x1080:r=25:d={duration}",
        "-f", "lavfi",
        "-i", f"aevalsrc=0:c=stereo:s=44100:d={duration}",
        "-vf", (
            f"drawtext=fontfile='{font}':fontsize=42:fontcolor=white"
            f":x=(w-tw)/2:y=(h-th)/2:text='{safe_text}'"
        ),
        "-c:v", "h264_nvenc", "-preset", "p1", "-cq", "28",
        "-c:a", "aac", "-b:a", "128k",
        str(out),
    ])


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--no-cta", action="store_true", help="Без CTA плашек")
    args = parser.parse_args()

    OUT_DIR.mkdir(parents=True, exist_ok=True)

    print("\n" + "=" * 60)
    print("FR SFX MINI TEST")
    print("=" * 60)

    # ── Загружаем SFX события из result_visual.json ──────────────────────────
    with open(RESULT_JSON, encoding="utf-8") as f:
        data = json.load(f)

    segs = data.get("segments", [])
    sfx_events = [(float(s["start"]), s["sfx_cue"]) for s in segs if s.get("sfx_cue")]
    print(f"\nSFX events: {len(sfx_events)}")
    for t, cue in sfx_events:
        m, s = divmod(t, 60)
        print(f"  {int(m):02d}:{s:05.2f}  {cue}")

    video_dur = ffprobe_dur(FINAL_VIDEO)
    print(f"\nВидео: {FINAL_VIDEO.name}  {video_dur:.1f}s")

    # ── Определяем окна ──────────────────────────────────────────────────────
    windows = []

    # 1. Концовка интро → начало основного контента
    intro_win_start = max(0, INTRO_DUR_S - 10.0)
    intro_win_end   = INTRO_DUR_S + 5.0
    windows.append({
        "label": f"INTRO END  ({intro_win_start:.0f}s–{intro_win_end:.0f}s)",
        "start": intro_win_start,
        "end":   intro_win_end,
    })

    # 2. Окна вокруг SFX событий
    for t, cue in sfx_events:
        if cue in ("riser", "riser+boom"):
            sfx_dur = RISER_DUR_S + POST_S  # riser 4.5s + буфер
            win_start = t - PRE_S
            win_end   = t + sfx_dur
        elif cue == "boom":
            win_start = t - PRE_S
            win_end   = t + BOOM_DUR_S + POST_S
        elif cue == "impact":
            win_start = t - PRE_S
            win_end   = t + IMPACT_DUR_S + POST_S
        elif cue == "downlifter":
            win_start = t - 2.0
            win_end   = t + 3.0 + POST_S
        else:
            win_start = t - PRE_S
            win_end   = t + 3.0 + POST_S

        # Клипуем к границам видео
        win_start = max(0.0, win_start)
        win_end   = min(video_dur, win_end)

        m, s = divmod(t, 60)
        windows.append({
            "label": f"SFX {int(m):02d}:{s:05.2f}  [{cue}]",
            "start": win_start,
            "end":   win_end,
        })

    print(f"\nОкон для нарезки: {len(windows)}")
    for i, w in enumerate(windows):
        print(f"  [{i+1:02d}] {w['label']:<35} {w['start']:.1f}s – {w['end']:.1f}s  ({w['end']-w['start']:.1f}s)")

    # ── Нарезка ──────────────────────────────────────────────────────────────
    print(f"\n[1/3] Нарезка сегментов...")
    segment_files = []

    for i, w in enumerate(windows):
        # Разделитель (title card)
        card = OUT_DIR / f"card_{i:02d}.mp4"
        add_title_card(w['label'], 1.5, card)
        segment_files.append(card)

        seg = OUT_DIR / f"seg_{i:02d}.mp4"
        ok = cut_segment(FINAL_VIDEO, w["start"], w["end"], seg)
        if ok and seg.exists() and seg.stat().st_size > 100_000:
            sz = seg.stat().st_size // 1024 // 1024
            print(f"  ✓ seg_{i:02d}  {sz}MB  {w['label']}")
            segment_files.append(seg)
        else:
            print(f"  ✗ seg_{i:02d}  FAIL  {w['label']}")

    # ── Конкатенация ─────────────────────────────────────────────────────────
    print(f"\n[2/3] Конкатенация {len(segment_files)} частей...")
    concat_list = OUT_DIR / "_concat.txt"
    concat_list.write_text(
        "\n".join(f"file '{p.as_posix()}'" for p in segment_files),
        encoding="utf-8"
    )
    concat_out = OUT_DIR / "mini_test_concat.mp4"
    subprocess.run([
        "ffmpeg", "-hide_banner", "-loglevel", "error", "-y",
        "-f", "concat", "-safe", "0", "-i", str(concat_list),
        "-c:v", "h264_nvenc", "-preset", "p2", "-cq", "20",
        "-c:a", "aac", "-b:a", "192k",
        str(concat_out),
    ], check=True)

    concat_dur = ffprobe_dur(concat_out)
    concat_mb  = concat_out.stat().st_size // 1024 // 1024
    print(f"  OK: {concat_out.name}  {concat_dur:.1f}s  {concat_mb}MB")

    # ── CTA overlay ──────────────────────────────────────────────────────────
    final_out = OUT_DIR / "mini_test_fr.mp4"

    if not args.no_cta and CTA_MOV.exists():
        print(f"\n[3/3] Добавляем CTA плашки...")
        # Для короткого теста ставим плашки каждые ~40s начиная с 30s
        cta_dur = ffprobe_dur(CTA_MOV)
        timestamps = []
        t = 30.0
        while t + cta_dur + 5.0 < concat_dur:
            timestamps.append(t)
            t += 45.0
        print(f"  CTA timestamps: {[f'{x:.0f}s' for x in timestamps]}")

        ts_args = []
        for ts in timestamps:
            ts_args += ["--timestamps", str(ts)]

        subprocess.run([
            sys.executable, str(APPLY_CTA),
            str(concat_out), str(CTA_MOV),
            "--output", str(final_out),
        ] + ts_args, check=True)
        print(f"  OK: {final_out.name}")
    else:
        # Без CTA — просто копируем
        concat_out.rename(final_out)
        print(f"\n[3/3] CTA пропущено — {final_out.name}")

    final_dur = ffprobe_dur(final_out)
    final_mb  = final_out.stat().st_size // 1024 // 1024

    print(f"\n{'='*60}")
    print(f"ГОТОВО!")
    print(f"  Файл:       {final_out}")
    print(f"  Длина:      {final_dur:.1f}s  ({final_dur/60:.1f} мин)")
    print(f"  Размер:     {final_mb} MB")
    print(f"{'='*60}")
    print(f"\nЧТО ПРОВЕРЯТЬ:")
    print(f"  SFX:   слышны ли riser/boom/impact в нужный момент?")
    print(f"  УРОВНИ: riser слишком тихий/громкий?  boom слишком резкий?")
    print(f"  СУБТ:  не прыгают ли строки, попадают ли в тайминг?")
    print(f"  CTA:   плашка подписаться появляется корректно?")
    print(f"  ФЛОУ:  общий ритм и монтаж ок?")


if __name__ == "__main__":
    main()
