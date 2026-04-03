"""
test_channel_highlights.py — комплексный тест всех трёх каналов.

Создаёт ~2-3 мин хайлайт-видео для каждого канала, включающее:
  - Конец интро → переход к контенту (интро-звук, первый субтитр)
  - Первый SFX после интро (проверка: не во время интро)
  - Ранний контент (первые переходы)
  - Середина видео (переходы не сбиваются)
  - Последний SFX (тест к концу видео)
  - Конец видео (последние 30s: субтитры не дрейфуют, аудио затухает)

Запуск:
    python test_channel_highlights.py [de|fr|es|all]
"""
import argparse
import json
import subprocess
import sys
import time
from pathlib import Path

sys.stdout.reconfigure(encoding="utf-8", errors="replace")

# ─── Конфиг каналов ──────────────────────────────────────────────────────────
DATA = Path("D:/Video-pipeline-data/channels")
OUT_ROOT = Path("D:/Video-pipeline-temp/channel_highlights")

CHANNEL_CFG = {
    "de": {
        "label":      "DE (channel_001_cosmos_de)",
        "session":    "Video_20260401_223509",
        "intro_dur":  93.8,
    },
    "fr": {
        "label":      "FR (channel_002_cosmos_fr)",
        "session":    "Video_20260402_180801",
        "intro_dur":  94.5,
    },
    "es": {
        "label":      "ES (channel_003_religion_es)",
        "session":    "Video_20260401_224228",
        "intro_dur":  0.0,
    },
}

# ─── Параметры окон ──────────────────────────────────────────────────────────
WIN_INTRO_PRE  = 20.0   # секунд ДО конца интро
WIN_INTRO_POST = 12.0   # секунд ПОСЛЕ начала контента
WIN_SFX_PRE    = 5.0    # секунд до SFX
WIN_SFX_POST   = 9.0    # секунд после SFX
WIN_EARLY_DUR  = 30.0   # ранний контент (переходы)
WIN_MID_DUR    = 20.0   # середина (переходы)
WIN_END_DUR    = 30.0   # конец видео
CARD_DUR       = 1.2    # длина title card между сегментами

# ─── Утилиты ─────────────────────────────────────────────────────────────────

def probe_dur(path: Path) -> float:
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
    """Re-encode отрезок (NVENC) — stream-copy даёт NAL-ошибки при concat."""
    dur = max(0.1, end - start)
    out.parent.mkdir(parents=True, exist_ok=True)
    r = subprocess.run([
        "ffmpeg", "-hide_banner", "-loglevel", "error", "-y",
        "-ss", f"{start:.3f}", "-t", f"{dur:.3f}", "-i", str(src),
        "-c:v", "h264_nvenc", "-preset", "p4", "-cq", "22",
        "-c:a", "aac", "-b:a", "192k",
        str(out),
    ])
    return r.returncode == 0


def make_title_card(text: str, duration: float, out: Path):
    """Чёрный кадр с белым текстом — разделитель между сегментами."""
    font  = "C\\:/Windows/Fonts/arialbd.ttf"
    safe  = text.replace(":", "\\:").replace("'", "\\'")
    out.parent.mkdir(parents=True, exist_ok=True)
    subprocess.run([
        "ffmpeg", "-hide_banner", "-loglevel", "error", "-y",
        "-f", "lavfi", "-i", f"color=c=black:s=1920x1080:r=25:d={duration}",
        "-f", "lavfi", "-i", f"aevalsrc=0:c=stereo:s=44100:d={duration}",
        "-vf", f"drawtext=fontfile='{font}':fontsize=38:fontcolor=white"
                f":x=(w-tw)/2:y=(h-th)/2:text='{safe}'",
        "-c:v", "h264_nvenc", "-preset", "p1", "-cq", "28",
        "-c:a", "aac", "-b:a", "128k",
        str(out),
    ])


def concat_files(files: list[Path], out: Path) -> bool:
    """Конкатенация через FFmpeg concat demuxer."""
    txt = out.parent / "_concat.txt"
    txt.write_text("\n".join(f"file '{p.as_posix()}'" for p in files))
    r = subprocess.run([
        "ffmpeg", "-hide_banner", "-loglevel", "error", "-y",
        "-f", "concat", "-safe", "0", "-i", str(txt),
        "-c:v", "h264_nvenc", "-preset", "p2", "-cq", "20",
        "-c:a", "aac", "-b:a", "192k",
        str(out),
    ])
    txt.unlink(missing_ok=True)
    return r.returncode == 0


# ─── Основная логика одного канала ───────────────────────────────────────────

def build_highlights(lang: str) -> Path | None:
    cfg        = CHANNEL_CFG[lang]
    label      = cfg["label"]
    intro_dur  = cfg["intro_dur"]
    sess_dir   = DATA / lang / cfg["session"]
    out_dir    = OUT_ROOT / lang
    out_dir.mkdir(parents=True, exist_ok=True)

    # Финальное видео
    finals = list((sess_dir / "output").glob("*_final.mp4"))
    if not finals:
        print(f"  [{lang.upper()}] ✗ Нет финального видео в {sess_dir/'output'}")
        return None
    final_path = finals[0]
    total_dur  = probe_dur(final_path)
    if total_dur < 30:
        print(f"  [{lang.upper()}] ✗ Видео слишком короткое ({total_dur:.1f}s)")
        return None

    print(f"\n{'='*60}")
    print(f"  {label}")
    print(f"  Видео: {final_path.name}  {total_dur:.1f}s")
    print(f"  Интро: {intro_dur:.1f}s   Контент: {total_dur - intro_dur:.1f}s")
    print(f"{'='*60}")

    # SFX события из result_visual.json
    rv_path = sess_dir / "transcripts" / "result_visual.json"
    sfx_all = []
    if rv_path.exists():
        data = json.loads(rv_path.read_text(encoding="utf-8"))
        sfx_all = [(float(s["start"]), s["sfx_cue"])
                   for s in data.get("segments", []) if s.get("sfx_cue")]

    # Только SFX после интро + буфер 3s
    sfx_min = intro_dur + 3.0
    sfx_post_intro = [(t, c) for t, c in sfx_all if t >= sfx_min]
    print(f"  SFX всего: {len(sfx_all)}  |  после интро: {len(sfx_post_intro)}")
    for t, c in sfx_post_intro[:3]:
        m, s = divmod(t, 60)
        print(f"    {int(m):02d}:{s:05.2f}  {c}")

    # ── Определяем окна ──────────────────────────────────────────────────────
    content_start = intro_dur
    content_end   = total_dur
    content_dur   = content_end - content_start
    mid_point     = content_start + content_dur * 0.5

    windows = []

    # 1. Конец интро → начало контента
    if intro_dur > 5:
        w_start = max(0.0, intro_dur - WIN_INTRO_PRE)
        w_end   = min(total_dur, intro_dur + WIN_INTRO_POST)
        windows.append({
            "label": f"[{lang.upper()}] ИНТРО→КОНТЕНТ  ({w_start:.0f}s→{w_end:.0f}s)",
            "start": w_start, "end": w_end,
        })
    else:
        # ES: нет интро — начало видео
        w_end = min(total_dur, 25.0)
        windows.append({
            "label": f"[{lang.upper()}] НАЧАЛО ВИДЕО  (0→{w_end:.0f}s)",
            "start": 0.0, "end": w_end,
        })

    # 2. Первый SFX после интро
    if sfx_post_intro:
        t1, c1 = sfx_post_intro[0]
        cue_dur = {"riser+boom": 5.0, "boom": 2.0, "impact": 2.0,
                   "riser": 5.0, "downlifter": 3.0}.get(c1, 3.0)
        windows.append({
            "label": f"[{lang.upper()}] SFX ПЕРВЫЙ  @{t1:.0f}s [{c1}]",
            "start": max(0, t1 - WIN_SFX_PRE),
            "end":   min(total_dur, t1 + cue_dur + WIN_SFX_POST),
        })

    # 3. Ранний контент (переходы) — сразу после интро, первые клипы и переходы.
    # Жёсткое правило: окно НЕ пересекается с SFX ПЕРВЫЙ (ни одна секунда).
    early_start = content_start + 3.0
    early_end   = early_start + WIN_EARLY_DUR

    if sfx_post_intro:
        sfx1_win_start = max(0.0, sfx_post_intro[0][0] - WIN_SFX_PRE)
        cue_dur_1 = {"riser+boom": 5.0, "boom": 2.0, "impact": 2.0,
                     "riser": 5.0, "downlifter": 3.0}.get(sfx_post_intro[0][1], 3.0)
        sfx1_win_end = min(total_dur, sfx_post_intro[0][0] + cue_dur_1 + WIN_SFX_POST)

        if early_start < sfx1_win_end and early_end > sfx1_win_start:
            # Есть пересечение — ставим early ПЕРЕД SFX-окном
            early_end   = sfx1_win_start - 2.0
            early_start = max(content_start + 3.0, early_end - WIN_EARLY_DUR)
            if early_end - early_start < 8.0:
                # Слишком узко ДО SFX — ставим ПОСЛЕ SFX-окна
                early_start = sfx1_win_end + 2.0
                early_end   = early_start + WIN_EARLY_DUR

    early_start = max(content_start + 3.0, min(early_start, total_dur - 10.0))
    early_end   = min(total_dur, early_end)   # early_end уже задан выше (не пересчитываем!)
    windows.append({
        "label": f"[{lang.upper()}] РАННИЙ КОНТЕНТ  ({early_start:.0f}s→{early_end:.0f}s)",
        "start": early_start,
        "end":   early_end,
    })

    # 4. Середина видео
    mid_start = max(content_start, mid_point - WIN_MID_DUR / 2)
    windows.append({
        "label": f"[{lang.upper()}] СЕРЕДИНА  ({mid_start:.0f}s→{mid_start+WIN_MID_DUR:.0f}s)",
        "start": mid_start,
        "end":   min(total_dur, mid_start + WIN_MID_DUR),
    })

    # 5. Второй SFX (если есть) — промежуточный
    if len(sfx_post_intro) >= 3:
        mid_idx = len(sfx_post_intro) // 2
        t_mid, c_mid = sfx_post_intro[mid_idx]
        cue_dur = {"riser+boom": 5.0, "boom": 2.0, "impact": 2.0,
                   "riser": 5.0, "downlifter": 3.0}.get(c_mid, 3.0)
        windows.append({
            "label": f"[{lang.upper()}] SFX MID  @{t_mid:.0f}s [{c_mid}]",
            "start": max(0, t_mid - WIN_SFX_PRE),
            "end":   min(total_dur, t_mid + cue_dur + WIN_SFX_POST),
        })

    # 6. Последний SFX
    if sfx_post_intro:
        t_last, c_last = sfx_post_intro[-1]
        cue_dur = {"riser+boom": 5.0, "boom": 2.0, "impact": 2.0,
                   "riser": 5.0, "downlifter": 3.0}.get(c_last, 3.0)
        if abs(t_last - sfx_post_intro[0][0]) > 60:  # не дублировать первый
            windows.append({
                "label": f"[{lang.upper()}] SFX ПОСЛЕДНИЙ  @{t_last:.0f}s [{c_last}]",
                "start": max(0, t_last - WIN_SFX_PRE),
                "end":   min(total_dur, t_last + cue_dur + WIN_SFX_POST),
            })

    # 7. Конец видео
    end_start = max(content_start, total_dur - WIN_END_DUR)
    windows.append({
        "label": f"[{lang.upper()}] КОНЕЦ ВИДЕО  ({end_start:.0f}s→{total_dur:.0f}s)",
        "start": end_start,
        "end":   total_dur,
    })

    # ── Печатаем план ────────────────────────────────────────────────────────
    total_win = sum(w["end"] - w["start"] for w in windows)
    print(f"\n  Окна ({len(windows)} шт, итого {total_win:.0f}s ≈ {total_win/60:.1f} мин):")
    for i, w in enumerate(windows):
        dur = w["end"] - w["start"]
        print(f"    [{i+1:02d}] {w['label']:<50} {dur:.0f}s")

    # ── Нарезка ──────────────────────────────────────────────────────────────
    print(f"\n  [1/3] Нарезка сегментов...")
    parts: list[Path] = []
    for i, w in enumerate(windows):
        # Title card
        card = out_dir / f"card_{i:02d}.mp4"
        make_title_card(w["label"], CARD_DUR, card)
        parts.append(card)

        seg = out_dir / f"seg_{i:02d}.mp4"
        ok  = cut_segment(final_path, w["start"], w["end"], seg)
        if ok and seg.exists() and seg.stat().st_size > 50_000:
            mb = seg.stat().st_size // 1024 // 1024
            print(f"    ✓ seg_{i:02d}  {mb:3d}MB  {w['label'][:55]}")
            parts.append(seg)
        else:
            print(f"    ✗ seg_{i:02d}  FAIL  {w['label']}")

    # ── Конкатенация ─────────────────────────────────────────────────────────
    print(f"\n  [2/3] Concat {len(parts)} частей...")
    out_path = OUT_ROOT / f"highlight_{lang}.mp4"
    ok = concat_files(parts, out_path)

    if ok and out_path.exists():
        final_dur = probe_dur(out_path)
        final_mb  = out_path.stat().st_size // 1024 // 1024
        print(f"  [3/3] ✅ {out_path.name}  {final_dur:.1f}s ({final_dur/60:.1f}мин)  {final_mb}MB")
        return out_path
    else:
        print(f"  [3/3] ✗ Concat FAILED")
        return None


# ─── Точка входа ─────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(description="Создать highlight-видео по каналам")
    parser.add_argument("channels", nargs="*", default=["all"],
                        help="de|fr|es|all  (default: all)")
    args = parser.parse_args()

    langs = list(CHANNEL_CFG.keys()) if "all" in args.channels else args.channels

    t0 = time.time()
    results: dict[str, Path | None] = {}

    for lang in langs:
        if lang not in CHANNEL_CFG:
            print(f"Неизвестный канал: {lang}")
            continue
        result = build_highlights(lang)
        results[lang] = result

    # Итоговый отчёт
    elapsed = time.time() - t0
    print(f"\n{'='*60}")
    print(f"ГОТОВО!  {elapsed:.0f}s")
    print(f"{'='*60}")
    for lang, path in results.items():
        if path and path.exists():
            dur = probe_dur(path)
            mb  = path.stat().st_size // 1024 // 1024
            print(f"  {lang.upper()}  ✅  {dur:.1f}s ({dur/60:.1f}мин)  {mb}MB")
            print(f"       {path}")
        else:
            print(f"  {lang.upper()}  ✗  FAILED")

    print(f"\nЧТО ПРОВЕРЯТЬ:")
    print("  • Интро→Контент: плавный переход, первый субтитр точно в тайминг")
    print("  • SFX ПЕРВЫЙ: звук ТОЛЬКО после интро, правильный уровень")
    print("  • Ранний контент: переходы (crossfade DE / fadeblack FR / scripture ES)")
    print("  • Середина: субтитры не дрейфуют, стиль правильный")
    print("  • SFX MID/LAST: звук в нужный момент, не перегружает")
    print("  • Конец видео: аудио плавно затухает, последний субтитр не обрезан")


if __name__ == "__main__":
    main()
