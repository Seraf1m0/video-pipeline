"""
Нарезка Pool 3 cosmos.mp4 на точные 5-секундные клипы.
Каждый клип: H.264, 1920x1080, 25fps, ровно 5s.
"""
import subprocess
import sys
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

INPUT   = r"D:\Video-pipeline-library\library_cosmos\Pool 3 cosmos.mp4"
OUT_DIR = Path(r"D:\Video-pipeline-library\library_cosmos\pool3_clips")
CLIP_DUR   = 5.0
TOTAL_DUR  = 4770.28
WORKERS    = 6

OUT_DIR.mkdir(exist_ok=True)

# Генерируем список отрезков
segments = []
t = 0.0
i = 1
while t + CLIP_DUR <= TOTAL_DUR + 0.1:
    segments.append((i, t))
    t += CLIP_DUR
    i += 1

print(f"Всего клипов: {len(segments)}", flush=True)


def cut_clip(idx: int, start: float) -> str:
    out = OUT_DIR / f"pool3_{idx:04d}.mp4"
    cmd = [
        "ffmpeg", "-y",
        "-ss", f"{start:.3f}",
        "-i", INPUT,
        "-t", f"{CLIP_DUR:.3f}",
        "-c:v", "libx264", "-preset", "fast", "-crf", "18",
        "-vf", "scale=1920:1080,fps=25",
        "-an",
        "-loglevel", "error",
        str(out),
    ]
    r = subprocess.run(cmd, capture_output=True)
    if r.returncode != 0:
        return f"ERROR {out.name}: {r.stderr.decode(errors='replace')[:100]}"
    return f"OK {out.name}"


done = 0
errors = []
with ThreadPoolExecutor(max_workers=WORKERS) as ex:
    futures = {ex.submit(cut_clip, idx, start): idx for idx, start in segments}
    for fut in as_completed(futures):
        res = fut.result()
        done += 1
        if "ERROR" in res:
            errors.append(res)
        if done % 50 == 0 or done == len(segments):
            print(f"  [{done}/{len(segments)}] готово", flush=True)

print(f"\nГотово: {done - len(errors)} клипов")
if errors:
    print(f"Ошибки ({len(errors)}):")
    for e in errors:
        print(f"  {e}")
