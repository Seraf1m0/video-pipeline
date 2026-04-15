"""
normalize_sfx.py — One-time SFX normalization to peak = -6 dBFS.

Reads assets/sfx/<category>/*.mp3|wav
Normalizes to peak -6 dBFS via ffmpeg
Saves to assets/sfx_norm/<category>/ (same filenames)
Generates assets/sfx_norm/index.json
"""

import json
import subprocess
import sys
from pathlib import Path

ROOT     = Path(__file__).resolve().parent.parent
SFX_DIR  = ROOT / "assets" / "sfx"
NORM_DIR = ROOT / "assets" / "sfx_norm"

# Only normalize these target categories
CATEGORIES = [
    "whoosh",
    "whoosh_fast",
    "whoosh_big",
    "infographic_tick",
    "notification",
    "riser",
    "boom",
]

TARGET_PEAK_DB = -6.0   # dBFS


def measure_peak(path: Path) -> float | None:
    """Return peak dBFS using volumedetect, or None on failure."""
    cmd = [
        "ffmpeg", "-hide_banner",
        "-i", str(path),
        "-af", "volumedetect",
        "-f", "null",
        "-",
    ]
    try:
        r = subprocess.run(cmd, capture_output=True, text=True,
                           encoding="utf-8", errors="replace", timeout=30)
        for line in r.stderr.splitlines():
            if "max_volume" in line:
                # e.g. "  max_volume: -3.2 dB"
                val = float(line.split(":")[-1].strip().split()[0])
                return val
    except Exception as e:
        print(f"  [WARN] measure failed: {e}")
    return None


def normalize_file(src: Path, dst: Path, peak_db: float) -> bool:
    """
    Normalize src so that peak = TARGET_PEAK_DB, save to dst.
    gain_db = TARGET_PEAK_DB - peak_db
    """
    gain_db = TARGET_PEAK_DB - peak_db
    gain_lin = 10 ** (gain_db / 20.0)

    dst.parent.mkdir(parents=True, exist_ok=True)
    cmd = [
        "ffmpeg", "-y", "-hide_banner",
        "-i", str(src),
        "-af", f"volume={gain_lin:.6f}",
        str(dst),
    ]
    try:
        r = subprocess.run(cmd, capture_output=True, text=True,
                           encoding="utf-8", errors="replace", timeout=60)
        return r.returncode == 0
    except Exception as e:
        print(f"  [WARN] ffmpeg failed: {e}")
        return False


def main():
    NORM_DIR.mkdir(parents=True, exist_ok=True)
    index: dict[str, list] = {}

    total_ok = 0
    total_skip = 0
    total_fail = 0

    for cat in CATEGORIES:
        src_dir = SFX_DIR / cat
        dst_dir = NORM_DIR / cat

        if not src_dir.exists():
            print(f"[SKIP] category not found: {cat}")
            continue

        files = sorted([f for f in src_dir.iterdir()
                        if f.suffix.lower() in (".mp3", ".wav")])
        if not files:
            print(f"[SKIP] no audio files in: {cat}")
            continue

        print(f"\n-- {cat} ({len(files)} files) --")
        dst_dir.mkdir(parents=True, exist_ok=True)
        cat_entries = []

        for src in files:
            dst = dst_dir / src.name
            print(f"  {src.name}", end=" ... ", flush=True)

            peak = measure_peak(src)
            if peak is None:
                print("SKIP (measure failed)")
                total_skip += 1
                continue

            ok = normalize_file(src, dst, peak)
            if ok:
                print(f"OK  (peak {peak:+.1f} dB -> {TARGET_PEAK_DB:+.1f} dB, "
                      f"gain {TARGET_PEAK_DB - peak:+.1f} dB)")
                cat_entries.append({
                    "file": str(dst.relative_to(NORM_DIR).as_posix()),
                    "original_peak": round(peak, 2),
                    "normalized": True,
                })
                total_ok += 1
            else:
                print("FAIL")
                total_fail += 1

        index[cat] = cat_entries

    # Write index.json
    idx_path = NORM_DIR / "index.json"
    with open(idx_path, "w", encoding="utf-8") as f:
        json.dump(index, f, indent=2, ensure_ascii=False)

    print(f"\n{'='*50}")
    print(f"Done. OK={total_ok}  SKIP={total_skip}  FAIL={total_fail}")
    print(f"Index: {idx_path}")


if __name__ == "__main__":
    main()
