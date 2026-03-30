"""
audit_downlifter.py — measure duration, RMS, and true peak for all downlifter files.

Prints a sorted table (by rms_db desc) so the user can decide which files to keep.
Does NOT delete anything.

Usage:
  python tools/audit_downlifter.py
  python tools/audit_downlifter.py --sort rms      # loudest first (default)
  python tools/audit_downlifter.py --sort peak     # highest peak first
  python tools/audit_downlifter.py --sort duration # longest first
  python tools/audit_downlifter.py --sort name
"""

import argparse
import subprocess
import sys
from pathlib import Path

_BASE        = Path(__file__).resolve().parent.parent
_DOWNLIFTER  = _BASE / "assets" / "sfx" / "downlifter"
_AUDIO_EXTS  = {".wav", ".mp3", ".ogg", ".flac"}

# Null sink differs by OS
_NULL = "NUL" if sys.platform == "win32" else "/dev/null"


def get_duration(path: Path) -> float:
    """Duration in seconds via ffprobe."""
    try:
        r = subprocess.run(
            ["ffprobe", "-v", "quiet",
             "-show_entries", "format=duration",
             "-of", "csv=p=0", str(path)],
            capture_output=True, text=True,
        )
        return round(float(r.stdout.strip()), 2)
    except Exception:
        return 0.0


def get_rms_and_peak(path: Path) -> tuple[float, float]:
    """
    Return (rms_db, peak_db) in dBFS via a single ffmpeg astats pass.

    astats=metadata=1:reset=1 without ametadata key filter emits ALL metadata
    lines to stderr, including:
      lavfi.astats.Overall.RMS_level=<value>
      lavfi.astats.Overall.Peak_level=<value>

    Falls back to volumedetect for RMS and -99 for peak on parse failure.
    """
    rms_db:  float = -99.0
    peak_db: float = -99.0

    try:
        r = subprocess.run(
            ["ffmpeg", "-i", str(path),
             "-af", "astats=metadata=1:reset=1,ametadata=print",
             "-f", "null", _NULL],
            capture_output=True, text=True,
        )
        for line in r.stderr.splitlines():
            line = line.strip()
            if "lavfi.astats.Overall.RMS_level=" in line:
                val = line.split("=")[-1].strip()
                if val not in ("-inf", "inf", ""):
                    rms_db = round(float(val), 1)
            elif "lavfi.astats.Overall.Peak_level=" in line:
                val = line.split("=")[-1].strip()
                if val not in ("-inf", "inf", ""):
                    peak_db = round(float(val), 1)

        # RMS fallback: volumedetect mean_volume
        if rms_db == -99.0:
            r2 = subprocess.run(
                ["ffmpeg", "-i", str(path), "-af", "volumedetect",
                 "-f", "null", _NULL],
                capture_output=True, text=True,
            )
            for line in r2.stderr.splitlines():
                if "mean_volume:" in line:
                    rms_db = round(
                        float(line.split("mean_volume:")[1].split("dB")[0].strip()), 1
                    )
                    break

    except Exception:
        pass

    return rms_db, peak_db


def main() -> None:
    parser = argparse.ArgumentParser(description="Audit downlifter SFX files.")
    parser.add_argument(
        "--sort",
        choices=["rms", "peak", "duration", "name"],
        default="rms",
        help="Sort column (default: rms — loudest first)",
    )
    args = parser.parse_args()

    if not _DOWNLIFTER.exists():
        print(f"ERROR: downlifter directory not found: {_DOWNLIFTER}", file=sys.stderr)
        sys.exit(1)

    files = sorted([
        f for f in _DOWNLIFTER.iterdir()
        if f.suffix.lower() in _AUDIO_EXTS
    ])

    if not files:
        print("No downlifter files found.")
        return

    print(f"Measuring {len(files)} files in {_DOWNLIFTER.name}/…\n")

    rows: list[tuple[str, float, float, float]] = []
    for f in files:
        print(f"  {f.name}…", end=" ", flush=True)
        dur           = get_duration(f)
        rms_db, peak  = get_rms_and_peak(f)
        rows.append((f.name, dur, rms_db, peak))
        print(f"{dur:.2f}s  rms {rms_db:.1f} dBFS  peak {peak:.1f} dBFS")

    # ── Sort ──────────────────────────────────────────────────────────────────
    if args.sort == "rms":
        rows.sort(key=lambda r: r[2], reverse=True)   # loudest first
    elif args.sort == "peak":
        rows.sort(key=lambda r: r[3], reverse=True)   # highest peak first
    elif args.sort == "duration":
        rows.sort(key=lambda r: r[1], reverse=True)   # longest first
    else:
        rows.sort(key=lambda r: r[0].lower())

    # ── Table ─────────────────────────────────────────────────────────────────
    col_name = max(len(r[0]) for r in rows)
    width    = col_name + 42
    print()
    print("=" * width)
    print(f"  {'filename':<{col_name}}  {'duration':>10}  {'rms_db':>9}  {'peak_db':>9}")
    print("-" * width)
    for name, dur, rms, peak in rows:
        flag = "  ←" if rms > -20 else ""
        print(f"  {name:<{col_name}}  {dur:>8.2f}s  {rms:>8.1f}  {peak:>8.1f}{flag}")
    print("=" * width)
    print(f"\n  Total: {len(rows)} files")
    print(f"\nTip: keep files with duration 1.5–4.0s, RMS > -30 dBFS, peak < -1 dBFS.")
    print("     Move unwanted files to assets/sfx/downlifter/archive/ to exclude them.")


if __name__ == "__main__":
    main()
