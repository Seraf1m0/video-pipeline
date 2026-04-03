"""
test_transitions.py — визуальный тест переходов между клипами.

Берёт 4 реальных клипа из каждого канала и применяет его родной переход.
Собирает одно сравнительное видео:
  [DE title] → clip1→clip2→clip3→clip4 (crossfade 0.6s)
  [FR title] → clip1→clip2→clip3→clip4 (fadeblack 1.0s)
  [ES title] → clip1→clip2→clip3→clip4 (fadebeige 1.0s)

Запуск:
    python test_transitions.py
"""
import sys, subprocess, time
from pathlib import Path

sys.stdout.reconfigure(encoding="utf-8", errors="replace")
sys.path.insert(0, "agents/assembler")

from transitions import _crossfade_chunk, _fadeblack_chunk

OUT_DIR = Path("D:/Video-pipeline-temp/test_transitions")
OUT_DIR.mkdir(parents=True, exist_ok=True)

FONT = "C\\:/Windows/Fonts/arialbd.ttf"
N_CLIPS = 4   # клипов на канал
CLIP_DUR = 4.0  # секунд из каждого клипа (середина)

CHANNELS = [
    {
        "id":         "de",
        "label":      "DE — crossfade 0.6s",
        "final_video": Path("D:/Video-pipeline-data/channels/de/Video_20260401_223509/output/Video_20260401_223509_DE_final.mp4"),
        "clip_starts": [110.0, 150.0, 200.0, 260.0],   # секунды в финальном видео (после интро ~94s)
        "transition": "crossfade",
        "trans_dur":  0.6,
        "color":      "0x3A86FF",   # синий
    },
    {
        "id":         "fr",
        "label":      "FR — fadeblack 1.0s",
        "final_video": Path("D:/Video-pipeline-data/channels/fr/Video_20260402_180801/output/Video_20260402_180801_FR_final.mp4"),
        "clip_starts": [110.0, 150.0, 200.0, 260.0],
        "transition": "fadeblack",
        "trans_dur":  1.0,
        "color":      "0xFF006E",   # розовый
    },
    {
        "id":         "es",
        "label":      "ES — fadebeige 1.0s",
        "final_video": Path("D:/Video-pipeline-data/channels/es/Video_20260401_224228/output/Video_20260401_224228_ES_final.mp4"),
        "clip_starts": [15.0, 60.0, 120.0, 180.0],
        "transition": "fadebeige",
        "trans_dur":  1.0,
        "color":      "0xFFBE0B",   # золотой
    },
]


def probe_dur(p: Path) -> float:
    r = subprocess.run(
        ["ffprobe", "-v", "error", "-select_streams", "v:0",
         "-show_entries", "stream=duration", "-of", "csv=p=0", str(p)],
        capture_output=True, text=True,
    )
    try:
        return float(r.stdout.strip())
    except ValueError:
        return 0.0


def cut_mid(src: Path, dur: float, out: Path) -> bool:
    """Вырезать CLIP_DUR секунд из середины клипа."""
    total = probe_dur(src)
    if total <= 0:
        return False
    ss = max(0.0, (total - dur) / 2)
    r = subprocess.run([
        "ffmpeg", "-hide_banner", "-loglevel", "error", "-y",
        "-ss", f"{ss:.3f}", "-t", f"{dur:.3f}", "-i", str(src),
        "-c:v", "h264_nvenc", "-preset", "p4", "-cq", "22",
        "-an",   # без звука — тест только видео
        str(out),
    ])
    return r.returncode == 0


def make_title(text: str, color: str, dur: float, out: Path):
    safe = text.replace(":", "\\:").replace("'", "\\'")
    subprocess.run([
        "ffmpeg", "-hide_banner", "-loglevel", "error", "-y",
        "-f", "lavfi", "-i", f"color=c={color}:s=1920x1080:r=25:d={dur}",
        "-vf", (
            f"drawtext=fontfile='{FONT}':fontsize=52:fontcolor=white"
            f":x=(w-tw)/2:y=(h-th)/2:text='{safe}'"
            f",drawtext=fontfile='{FONT}':fontsize=28:fontcolor=white@0.7"
            f":x=(w-tw)/2:y=(h-th)/2+70:text='4 клипа с переходами'"
        ),
        "-c:v", "h264_nvenc", "-preset", "p1", "-cq", "28",
        str(out),
    ])


def concat_silent_audio(parts: list[Path], out: Path) -> bool:
    """Concat + добавить тихое аудио для совместимости плеера."""
    total_dur = sum(probe_dur(p) for p in parts)
    txt = OUT_DIR / "_concat.txt"
    txt.write_text("\n".join(f"file '{p.as_posix()}'" for p in parts))
    r = subprocess.run([
        "ffmpeg", "-hide_banner", "-loglevel", "error", "-y",
        "-f", "concat", "-safe", "0", "-i", str(txt),
        "-f", "lavfi", "-i", f"aevalsrc=0:c=stereo:s=44100:d={total_dur:.3f}",
        "-c:v", "h264_nvenc", "-preset", "p2", "-cq", "20",
        "-c:a", "aac", "-b:a", "128k",
        "-shortest",
        str(out),
    ])
    txt.unlink(missing_ok=True)
    return r.returncode == 0


# ─── Главный цикл ────────────────────────────────────────────────────────────

t0 = time.time()
all_parts: list[Path] = []

for ch in CHANNELS:
    lang       = ch["id"]
    label      = ch["label"]
    transition = ch["transition"]
    trans_dur  = ch["trans_dur"]
    color      = ch["color"]

    print(f"\n{'='*55}")
    print(f"  {label}")
    print(f"{'='*55}")

    # Title card
    title_out = OUT_DIR / f"title_{lang}.mp4"
    make_title(label, color, 2.0, title_out)
    all_parts.append(title_out)

    # Нарезаем куски из финального видео по заданным стартовым позициям
    final_video = ch["final_video"]
    clip_starts = ch["clip_starts"]

    if not final_video.exists():
        print(f"  [SKIP] финальное видео не найдено: {final_video}")
        continue

    cut_clips: list[Path] = []
    cut_durs:  list[float] = []
    for i, ss in enumerate(clip_starts):
        cut = OUT_DIR / f"cut_{lang}_{i:02d}.mp4"
        r = subprocess.run([
            "ffmpeg", "-hide_banner", "-loglevel", "error", "-y",
            "-ss", f"{ss:.3f}", "-t", f"{CLIP_DUR:.3f}", "-i", str(final_video),
            "-c:v", "h264_nvenc", "-preset", "p4", "-cq", "22",
            "-an",
            str(cut),
        ])
        if r.returncode == 0 and cut.exists():
            d = probe_dur(cut)
            cut_clips.append(cut)
            cut_durs.append(d)
            print(f"  cut_{lang}_{i:02d}  {d:.1f}s  @{ss:.0f}s")
        else:
            print(f"  [FAIL] @{ss:.0f}s")

    if len(cut_clips) < 2:
        print(f"  [SKIP] Мало нарезанных клипов")
        continue

    # Применяем переход
    trans_out = OUT_DIR / f"trans_{lang}.mp4"
    print(f"  Применяем: {transition} {trans_dur}s на {len(cut_clips)} клипах...")

    if transition == "crossfade":
        ok = _crossfade_chunk(cut_clips, cut_durs, trans_out, trans_dur)
    else:
        color_hex = "0xEAD5B0" if transition == "fadebeige" else "black"
        ok = _fadeblack_chunk(cut_clips, cut_durs, trans_out,
                              fade_dur=trans_dur / 2, color=color_hex)

    if ok and trans_out.exists():
        d = probe_dur(trans_out)
        mb = trans_out.stat().st_size // 1024 // 1024
        print(f"  OK  {d:.1f}s  {mb}MB  → {trans_out.name}")
        all_parts.append(trans_out)
    else:
        print(f"  [FAIL] переход не удался")

# ─── Финальный concat ────────────────────────────────────────────────────────
print(f"\n{'='*55}")
print(f"  Concat {len(all_parts)} частей в финальное видео...")

final = Path("D:/Video-pipeline-temp/test_transitions/test_transitions_all.mp4")
ok = concat_silent_audio(all_parts, final)

elapsed = time.time() - t0
if ok and final.exists():
    dur = probe_dur(final)
    mb  = final.stat().st_size // 1024 // 1024
    print(f"\n  OK  {dur:.1f}s ({dur/60:.1f} мин)  {mb}MB  за {elapsed:.1f}s")
    print(f"  {final}")
else:
    print(f"\n  [FAIL] финальный concat не удался")

print(f"\nЧТО ПРОВЕРЯТЬ:")
print(f"  DE (crossfade 0.6s) — плавный opacity blend, нет вспышек, нет чёрного")
print(f"  FR (fadeblack 1.0s) — 0.5s fade-out в чёрный + 0.5s fade-in из чёрного")
print(f"  ES (fadebeige 1.0s) — как FR но через пастельный бежевый #EAD5B0")
