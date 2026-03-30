"""
generate_riser_variants.py — pitch-shift existing riser files to expand variety.

For each original file in assets/sfx/riser/ that does NOT have a _down/_up/_high suffix:
  _down : asetrate=44100*0.85, aresample=44100  (−2.7 semitones, darker)
  _up   : asetrate=44100*1.10, aresample=44100  (+1.7 semitones, tighter)
  _high : asetrate=44100*1.20, aresample=44100  (+3.3 semitones, urgent)

Output files are WAV, written next to the originals.
Idempotent: skips variants that already exist.

Usage:
  python tools/generate_riser_variants.py
"""

import subprocess
import sys
from pathlib import Path

# ── Paths ─────────────────────────────────────────────────────────────────────

_BASE    = Path(__file__).resolve().parent.parent
_RISER   = _BASE / "assets" / "sfx" / "riser"

# ── Variant definitions ───────────────────────────────────────────────────────

_VARIANTS = {
    "_down": {
        "rate_factor": 0.85,          # −2.7 semitones (12*log2(0.85) ≈ −2.72)
        "label":       "darker / slower nарастание",
    },
    "_up": {
        "rate_factor": 1.10,          # +1.65 semitones
        "label":       "tighter / slightly higher",
    },
    "_high": {
        "rate_factor": 1.20,          # +3.32 semitones
        "label":       "urgent / high pitch",
    },
}

_VARIANT_SUFFIXES = set(_VARIANTS.keys())   # {"_down", "_up", "_high"}
_AUDIO_EXTS       = {".wav", ".mp3", ".ogg", ".flac"}
_BASE_RATE        = 44100


def is_variant(path: Path) -> bool:
    """Return True if filename already ends with one of the variant suffixes."""
    stem = path.stem
    return any(stem.endswith(sfx) for sfx in _VARIANT_SUFFIXES)


def make_variant(src: Path, suffix: str, rate_factor: float, label: str) -> bool:
    """
    Generate one pitch-shifted variant of src.
    Returns True if file was created, False if skipped.
    """
    dst = src.parent / f"{src.stem}{suffix}.wav"

    if dst.exists():
        print(f"  SKIP  {dst.name}  (already exists)")
        return False

    target_rate = int(_BASE_RATE * rate_factor)
    cmd = [
        "ffmpeg", "-y",
        "-i", str(src),
        "-af", f"asetrate={target_rate},aresample={_BASE_RATE}",
        "-ar", str(_BASE_RATE),
        "-ac", "2",
        "-c:a", "pcm_s16le",
        "-loglevel", "warning",
        str(dst),
    ]

    result = subprocess.run(cmd, capture_output=True)
    if result.returncode == 0 and dst.exists():
        size_kb = dst.stat().st_size // 1024
        print(f"  OK    {dst.name}  ({label}, {size_kb} KB)")
        return True

    err = result.stderr[-200:].decode("utf-8", "replace") if result.stderr else "?"
    print(f"  ERROR {dst.name}: {err}", file=sys.stderr)
    return False


def main() -> None:
    if not _RISER.exists():
        print(f"ERROR: riser directory not found: {_RISER}", file=sys.stderr)
        sys.exit(1)

    # Collect original (non-variant) files
    originals = sorted([
        f for f in _RISER.iterdir()
        if f.suffix.lower() in _AUDIO_EXTS and not is_variant(f)
    ])

    if not originals:
        print("No original riser files found.")
        return

    print(f"Riser originals: {len(originals)} files")
    print(f"Variants to generate per file: {len(_VARIANTS)}")
    print(f"Max total files after run: {len(originals)} × {len(_VARIANTS) + 1} = "
          f"{len(originals) * (len(_VARIANTS) + 1)}")
    print()

    created = 0
    skipped = 0

    for src in originals:
        print(f"Source: {src.name}")
        for suffix, cfg in _VARIANTS.items():
            ok = make_variant(src, suffix, cfg["rate_factor"], cfg["label"])
            if ok:
                created += 1
            else:
                skipped += 1
        print()

    # Final tally
    total_now = len(list(_RISER.glob("*.wav")))
    print("=" * 48)
    print(f"  Created : {created} new variant files")
    print(f"  Skipped : {skipped} (already existed)")
    print(f"  Total   : {total_now} WAV files in riser/")
    print("=" * 48)


if __name__ == "__main__":
    main()
