"""
test_subtitle_anims.py — тест всех анимаций субтитров.

Берём первые 12 секунд видеоряда + фейковые субтитры,
рендерим отдельный mp4 для каждой анимации.

Запуск:
    python test_subtitle_anims.py
"""
import sys
import time
from pathlib import Path

sys.path.insert(0, "agents/assembler")
from gpu_compositor import (
    run,
    ANIM_FADE, ANIM_FADE_SCALE, ANIM_SLIDE_UP, ANIM_POP, ANIM_WORD_HL,
)

# ─── Пути ────────────────────────────────────────────────────────────────────
VIDEOTRACK = Path("D:/Video-pipeline-data/speed_test/fr/videotrack.mp4")
OUT_DIR    = Path("D:/Video-pipeline-temp")
CLIP_DUR   = 12.0   # секунд для теста

# ─── Генерируем короткий клип (12 секунд) ────────────────────────────────────
import subprocess

CLIP = OUT_DIR / "_test_sub_clip.mp4"
if not CLIP.exists():
    print(f"Обрезаем {CLIP_DUR}s клип...")
    subprocess.run([
        "ffmpeg", "-hide_banner", "-loglevel", "error", "-y",
        "-i", str(VIDEOTRACK),
        "-t", str(CLIP_DUR),
        "-c:v", "h264_nvenc", "-preset", "p1", "-pix_fmt", "yuv420p", "-an",
        str(CLIP),
    ], check=True)
    print(f"  -> {CLIP}")

# ─── Генерируем тестовый ASS файл ────────────────────────────────────────────
# 6 групп по 2 слова, каждая ~2 секунды
ASS_CONTENT = """\
[Script Info]
ScriptType: v4.00+
PlayResX: 1920
PlayResY: 1080

[V4+ Styles]
Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, MarginV, Encoding
Style: Karaoke,Montserrat Bold,52,&H00FFFFFF,&H00707070,&H80000000,&H00000000,-1,0,0,0,100,100,2,0,1,3,1,2,80,80,30,1

[Events]
Format: Layer, Start, End, Style, Name, MarginL, MarginV, Effect, Text
Dialogue: 0,0:00:01.00,0:00:02.80,Karaoke,,0,0,0,,{\\kf90}DANS {\\kf90}L'ESPACE
Dialogue: 0,0:00:03.00,0:00:04.80,Karaoke,,0,0,0,,{\\kf90}LES {\\kf90}ÉTOILES
Dialogue: 0,0:00:05.00,0:00:06.80,Karaoke,,0,0,0,,{\\kf90}SONT {\\kf90}IMMENSES
Dialogue: 0,0:00:07.00,0:00:08.80,Karaoke,,0,0,0,,{\\kf90}LA {\\kf90}LUMIÈRE
Dialogue: 0,0:00:09.00,0:00:10.80,Karaoke,,0,0,0,,{\\kf90}VOYAGE {\\kf90}VITE
"""

ASS = OUT_DIR / "_test_sub.ass"
ASS.write_text(ASS_CONTENT, encoding="utf-8")

# ─── Рендерим по одному mp4 на каждую анимацию ───────────────────────────────
ANIMS = [
    (ANIM_FADE,       "fade"),
    (ANIM_FADE_SCALE, "fade_scale"),
    (ANIM_SLIDE_UP,   "slide_up"),
    (ANIM_POP,        "pop"),
    (ANIM_WORD_HL,    "word_highlight"),
]

FONT_PATH = "C:/Users/Serafim/AppData/Local/Microsoft/Windows/Fonts/Montserrat-Bold.ttf"

print(f"\n{'='*60}")
print(f"Тест анимаций субтитров — {len(ANIMS)} вариантов x {CLIP_DUR}s")
print(f"{'='*60}\n")

results = []
for anim_id, anim_name in ANIMS:
    out = OUT_DIR / f"test_sub_{anim_name}.mp4"
    print(f"[{anim_name}] → {out.name}")
    t0 = time.time()

    ok = run(
        videotrack       = CLIP,
        output           = out,
        intro            = None,
        audio            = None,
        ass_path         = ASS,
        color_grade_name = "cool_cinematic",
        fps              = 25.0,
        intro_trans_dur  = 1.0,
        font_size        = 52,
        fade_ms          = 150,
        font_path        = FONT_PATH,
        subtitle_style   = "karaoke",
        subtitle_anim    = anim_id,
    )

    elapsed = time.time() - t0
    size_mb = out.stat().st_size // 1024 // 1024 if out.exists() else 0
    status  = "OK" if ok else "FAIL"
    results.append((anim_name, status, elapsed, size_mb))
    print(f"  {status} {elapsed:.1f}s | {size_mb}MB\n")

# ─── Итог ────────────────────────────────────────────────────────────────────
print(f"\n{'='*60}")
print("РЕЗУЛЬТАТ:")
for name, status, t, mb in results:
    print(f"  {status} {name:<16} {t:.1f}s  {mb}MB  → test_sub_{name}.mp4")
print(f"\nФайлы в: {OUT_DIR}")
