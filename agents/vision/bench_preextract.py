"""
Test: pre-extraction vs on-the-fly, batch=4
Run: python agents/vision/bench_preextract.py
"""
import time
import tempfile
import subprocess
import requests
from pathlib import Path
from concurrent.futures import ThreadPoolExecutor

SERVER = "http://localhost:8765"
CLIPS_DIR = Path("C:/Projects/Video-pipeline/library/incoming/cuts/pool_4_48")
FRAME_SIZE = 448
N_CLIPS = 20
N_WORKERS = 16

clips = sorted(CLIPS_DIR.glob("cut_*.mp4"))[:N_CLIPS]


def extract_frames_for_clip(args):
    clip_path, tmp_dir = args
    name = clip_path.stem
    vf = (
        f"scale={FRAME_SIZE}:{FRAME_SIZE}:"
        f"force_original_aspect_ratio=decrease,"
        f"pad={FRAME_SIZE}:{FRAME_SIZE}:(ow-iw)/2:(oh-ih)/2"
    )
    first = f"{tmp_dir}/{name}_first.jpg"
    last  = f"{tmp_dir}/{name}_last.jpg"
    subprocess.run(
        ["ffmpeg", "-y", "-ss", "0.5", "-i", str(clip_path),
         "-vf", vf, "-frames:v", "1", "-q:v", "3", first],
        capture_output=True
    )
    subprocess.run(
        ["ffmpeg", "-y", "-sseof", "-0.5", "-i", str(clip_path),
         "-vf", vf, "-frames:v", "1", "-q:v", "3", last],
        capture_output=True
    )
    result = []
    if Path(first).exists():
        result.append(first)
    if Path(last).exists():
        result.append(last)
    return result


print(f"\n{'='*55}")
print(f"BENCH -- {N_CLIPS} clips | batch=4 | workers={N_WORKERS}")
print(f"{'='*55}")

# Test 1: on-the-fly
print("\n[1] On-the-fly (server extracts frames internally)...")
t0 = time.time()
for i in range(0, N_CLIPS, 4):
    batch = clips[i:i+4]
    resp = requests.post(f"{SERVER}/analyze_batch4", json={
        "video_paths": [str(p) for p in batch]
    }, timeout=120)
    resp.raise_for_status()
t1 = time.time()
elapsed1 = t1 - t0
rate1 = int(3600 / (elapsed1 / N_CLIPS))
print(f"   {N_CLIPS} clips in {elapsed1:.1f}s -> {elapsed1/N_CLIPS:.2f}s/clip  (~{rate1} clips/hr)")

# Test 2: pre-extraction parallel + inference
print(f"\n[2] Pre-extract ({N_WORKERS} threads) + inference...")
with tempfile.TemporaryDirectory() as tmp:
    t_e0 = time.time()
    with ThreadPoolExecutor(max_workers=N_WORKERS) as pool:
        frames_map = dict(zip(
            [c.stem for c in clips],
            pool.map(extract_frames_for_clip, [(c, tmp) for c in clips])
        ))
    t_e1 = time.time()
    extract_time = t_e1 - t_e0
    print(f"   Extract: {extract_time:.1f}s ({extract_time/N_CLIPS:.2f}s/clip)")

    t_i0 = time.time()
    for i in range(0, N_CLIPS, 4):
        batch = clips[i:i+4]
        clip_frames = [frames_map[c.stem] for c in batch]
        resp = requests.post(f"{SERVER}/analyze_batch4_frames", json={
            "clips": clip_frames
        }, timeout=120)
        resp.raise_for_status()
    t_i1 = time.time()
    inf_time = t_i1 - t_i0
    total2 = extract_time + inf_time
    rate2 = int(3600 / (total2 / N_CLIPS))
    print(f"   Infer:   {inf_time:.1f}s ({inf_time/N_CLIPS:.2f}s/clip)")
    print(f"   Total:   {total2:.1f}s -> {total2/N_CLIPS:.2f}s/clip  (~{rate2} clips/hr)")

print(f"\n{'='*55}")
speedup = elapsed1 / total2
print(f"Speedup: {speedup:.2f}x")
print(f"{'='*55}\n")
