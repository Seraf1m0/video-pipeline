"""
test_index_clips.py — тест индексации конкретных клипов через vision server.
Запуск: py tools/test_index_clips.py clip1.mp4 clip2.mp4 ...
"""
import sys
import time
import subprocess
import tempfile
from pathlib import Path

import requests
from PIL import Image

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

VISION_URL   = "http://localhost:8765"
FRAME_SIZE   = 336
FRAME_POS    = [0.5, 2.5, 4.5]
WAIT_TIMEOUT = 120  # сек ожидания загрузки модели


def wait_for_server(timeout: int = WAIT_TIMEOUT) -> bool:
    print(f"Ожидаю vision server (до {timeout}s)...", flush=True)
    t0 = time.time()
    while time.time() - t0 < timeout:
        try:
            r = requests.get(f"{VISION_URL}/health", timeout=3)
            if r.status_code == 200 and r.json().get("model_loaded"):
                print("Vision server готов.\n", flush=True)
                return True
        except Exception:
            pass
        time.sleep(3)
    return False


def extract_frames(clip_path: Path, tmp_dir: Path) -> list[Image.Image]:
    vf = (
        f"scale={FRAME_SIZE}:{FRAME_SIZE}:"
        f"force_original_aspect_ratio=decrease,"
        f"pad={FRAME_SIZE}:{FRAME_SIZE}:(ow-iw)/2:(oh-ih)/2"
    )
    frames = []
    for i, ss in enumerate(FRAME_POS):
        out = tmp_dir / f"frame_{i}.jpg"
        r = subprocess.run(
            ["ffmpeg", "-y", "-ss", str(ss), "-i", str(clip_path),
             "-vframes", "1", "-vf", vf, "-q:v", "2", "-loglevel", "error", str(out)],
            capture_output=True, timeout=30,
        )
        if r.returncode == 0 and out.exists() and out.stat().st_size > 0:
            try:
                frames.append(Image.open(out).convert("RGB"))
            except Exception:
                pass
    return frames


def analyze_clip(clip_path: Path) -> str:
    """Отправить клип на /analyze (vision server сам достаёт кадры)."""
    try:
        r = requests.post(
            f"{VISION_URL}/analyze",
            json={"video_path": str(clip_path), "segment_text": ""},
            timeout=60,
        )
        r.raise_for_status()
        data = r.json()
        if not data.get("valid"):
            return f"[INVALID] {data.get('reason', 'black/frozen')}"
        return data.get("keywords", "")
    except Exception as e:
        return f"[ERROR] {e}"


def main():
    clips = [Path(p) for p in sys.argv[1:] if p.endswith(".mp4")]
    if not clips:
        print("Укажи пути к .mp4 клипам как аргументы.", flush=True)
        sys.exit(1)

    if not wait_for_server():
        print("Vision server не запустился за 120s — выход.", flush=True)
        sys.exit(1)

    print(f"Тестирую {len(clips)} клипов...\n", flush=True)
    print("=" * 70, flush=True)

    for i, clip in enumerate(clips, 1):
        if not clip.exists():
            print(f"[{i}/{len(clips)}] {clip.name} — файл не найден", flush=True)
            continue

        t0 = time.time()
        desc = analyze_clip(clip)
        elapsed = time.time() - t0

        print(f"[{i}/{len(clips)}] {clip.name}  ({elapsed:.1f}s)", flush=True)
        print(f"  → {desc}", flush=True)
        print(flush=True)

    print("=" * 70, flush=True)
    print("Тест завершён.", flush=True)


if __name__ == "__main__":
    main()
