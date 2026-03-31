"""
test_subtitles.py — быстрый тест субтитров для всех трёх каналов.

Генерирует ASS + рендерит 45-секундное тестовое видео (тёмный фон).
Субтитры начинаются с t=0 (intro_duration=0 для теста).

Запуск:
    python test_subtitles.py
"""
import subprocess
import sys
from pathlib import Path

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT / "agents" / "assembler"))

from ass_generator import generate_ass, generate_karaoke_ass, generate_scripture_ass

OUT_DIR = ROOT / "data" / "subtitle_tests"
OUT_DIR.mkdir(parents=True, exist_ok=True)

DURATION = 45  # секунд тестового видео

CHANNELS = [
    {
        "name": "DE (Organetto Bold, rise)",
        "result_json": ROOT / "data/channels/de/Video_20260327_142643/transcripts/result.json",
        "ass": OUT_DIR / "test_de.ass",
        "out": OUT_DIR / "test_de.mp4",
        "generator": lambda r, a: generate_ass(
            r, a,
            font_name   = "Organetto Bold",
            font_size   = 48,
            fade_in_ms  = 120,
            fade_out_ms = 120,
            rise_px     = 50,
            intro_duration = 0.0,
            border_style   = 3,
        ),
    },
    {
        "name": "FR (Montserrat Bold, karaoke)",
        "result_json": ROOT / "data/channels/fr/Video_20260330_174554/transcripts/result.json",
        "ass": OUT_DIR / "test_fr.ass",
        "out": OUT_DIR / "test_fr.mp4",
        "generator": lambda r, a: generate_karaoke_ass(
            str(r), str(a),
            font_name      = "Montserrat Bold",
            font_size      = 56,
            fade_ms        = 60,
            rise_px        = 120,
            intro_duration = 0.0,
            max_words      = 3,
            col_active     = "&H00FFFFFF",
            col_passive    = "&H00707070",
            border_style   = 1,
        ),
    },
    {
        "name": "ES (Arial Bold, scripture, centre)",
        "result_json": ROOT / "data/channels/es/Video_20260327_202923/transcripts/result.json",
        "ass": OUT_DIR / "test_es.ass",
        "out": OUT_DIR / "test_es.mp4",
        "generator": lambda r, a: generate_scripture_ass(
            str(r), str(a),
            font_name      = "Arial Black",
            font_size      = 66,
            fade_in_ms     = 350,
            fade_out_ms    = 200,
            intro_duration = 0.0,
            max_words      = 3,
        ),
    },
]


def render_test(ass_path: Path, out_path: Path, duration: int):
    """Рендерит тёмный градиентный фон + ASS субтитры."""
    # Escape path for ass= filter (Windows backslashes → forward slashes)
    ass_escaped = str(ass_path).replace("\\", "/").replace(":", "\\:")

    cmd = [
        "ffmpeg", "-y",
        "-f", "lavfi",
        "-i", f"color=c=0x111111:size=1920x1080:rate=30:duration={duration}",
        "-vf", f"ass='{ass_escaped}'",
        "-c:v", "libx264", "-preset", "fast", "-crf", "20",
        "-t", str(duration),
        str(out_path),
    ]
    print(f"  🎬 Рендер: {out_path.name} ...", flush=True)
    r = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8", errors="replace")
    if r.returncode != 0:
        print(f"  ❌ FFmpeg error:\n{r.stderr[-800:]}", flush=True)
        return False
    print(f"  ✅ {out_path}", flush=True)
    return True


for ch in CHANNELS:
    print(f"\n{'='*55}\n{ch['name']}", flush=True)
    try:
        ch["generator"](ch["result_json"], ch["ass"])
        render_test(ch["ass"], ch["out"], DURATION)
    except Exception as e:
        print(f"  ❌ {e}", flush=True)

print(f"\n✅ Готово. Тесты в: {OUT_DIR}", flush=True)
