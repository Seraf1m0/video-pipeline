"""
compute_phashes.py — вычислить perceptual hash (pHash) для каждого клипа.
Добавляет поле "phash" в library.json.

pHash = 64-битный хэш среднего кадра клипа.
Два клипа визуально идентичны если hamming_distance(h1, h2) < 12.

Запуск:
    py agents/library/compute_phashes.py --channel channel_001_cosmos_de
    py agents/library/compute_phashes.py --channel channel_003_religion_es --rebuild
"""
import argparse
import io
import json
import subprocess
import sys
from pathlib import Path

import numpy as np

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

_utils_dir = Path(__file__).resolve().parent.parent / "utils"
if str(_utils_dir) not in sys.path:
    sys.path.insert(0, str(_utils_dir))
from paths import get_library_json, get_clips_dir


def _extract_middle_frame(clip_path: Path) -> "np.ndarray | None":
    """Извлечь средний кадр клипа как grayscale numpy array (32x32)."""
    r = subprocess.run(
        ["ffprobe", "-v", "quiet", "-show_entries", "format=duration",
         "-of", "csv=p=0", str(clip_path)],
        capture_output=True, text=True,
    )
    try:
        dur = float(r.stdout.strip())
    except (ValueError, AttributeError):
        dur = 5.0

    r2 = subprocess.run(
        ["ffmpeg", "-ss", f"{dur/2:.3f}", "-i", str(clip_path),
         "-vframes", "1", "-vf", "scale=32:32,format=gray",
         "-f", "image2pipe", "-vcodec", "png", "-loglevel", "quiet", "-"],
        capture_output=True,
    )
    if r2.returncode != 0 or not r2.stdout:
        return None
    try:
        from PIL import Image
        img = Image.open(io.BytesIO(r2.stdout)).convert("L").resize((32, 32))
        return np.array(img, dtype=float)
    except Exception:
        return None


def compute_phash(pixels: "np.ndarray") -> int:
    """
    DCT-based pHash (64-bit integer).
    Стандартный алгоритм: DCT 32x32 → взять левый верхний угол 8x8 (низкие частоты)
    → битовый вектор по медиане.
    """
    from scipy.fft import dct as _dct
    # 2D DCT через два прохода 1D DCT
    d = _dct(_dct(pixels, axis=0, norm="ortho"), axis=1, norm="ortho")
    patch = d[:8, :8].flatten()          # 64 коэффициента низких частот
    median = np.median(patch)
    bits = patch > median                # 64 бита
    return int(sum(int(b) << i for i, b in enumerate(bits)))


def hamming_distance(h1: int, h2: int) -> int:
    return bin(h1 ^ h2).count("1")


def build_phashes(channel_id: str, rebuild: bool = False) -> None:
    lib_json  = get_library_json(channel_id)
    clips_dir = get_clips_dir(channel_id)

    with open(lib_json, encoding="utf-8") as f:
        lib = json.load(f)

    clips = lib["clips"]
    todo = [
        cid for cid, entry in clips.items()
        if entry.get("indexed") and not entry.get("rejected")
        and (rebuild or not entry.get("phash"))
    ]

    total = len(todo)
    print(f"pHash для {total} клипов [{channel_id}]...", flush=True)
    if total == 0:
        print("Все клипы уже имеют phash.", flush=True)
        return

    ok = fail = 0
    for i, cid in enumerate(todo):
        clip_path = clips_dir / f"{cid}.mp4"
        if not clip_path.exists():
            fail += 1
            continue
        pixels = _extract_middle_frame(clip_path)
        if pixels is not None:
            try:
                clips[cid]["phash"] = compute_phash(pixels)
                ok += 1
            except Exception:
                fail += 1
        else:
            fail += 1

        if (i + 1) % 100 == 0 or (i + 1) == total:
            print(f"  [{i+1}/{total}] ok={ok} fail={fail}", flush=True)
            with open(lib_json, "w", encoding="utf-8") as f:
                json.dump(lib, f, ensure_ascii=False, indent=2)

    with open(lib_json, "w", encoding="utf-8") as f:
        json.dump(lib, f, ensure_ascii=False, indent=2)
    print(f"✅ pHash готово: {ok}/{total} → {lib_json}", flush=True)


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--channel", default="channel_001_cosmos_de")
    parser.add_argument("--rebuild", action="store_true")
    args = parser.parse_args()
    build_phashes(args.channel, rebuild=args.rebuild)
