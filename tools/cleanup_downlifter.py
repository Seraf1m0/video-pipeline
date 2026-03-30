"""
cleanup_downlifter.py — move low-RMS downlifter files to archive/.

Does NOT delete anything. Re-runnable (skips already-moved files).

Usage:
  python tools/cleanup_downlifter.py
"""

import shutil
from pathlib import Path

_BASE       = Path(__file__).resolve().parent.parent
_SRC        = _BASE / "assets" / "sfx" / "downlifter"
_ARCHIVE    = _SRC / "archive"

TO_ARCHIVE = [
    "Басс дроп (2).wav",
    "Басс дроп (3).wav",
    "Басс дроп (4).wav",
    "Басс дроп (6).wav",
    "Басс дроп (1).wav",
    "Басс дроп (10).wav",
    "NGTVST - Downer 1.wav",
    "Басс дроп (8).wav",
    "NGTVST - Downer 2.wav",
]


def main() -> None:
    _ARCHIVE.mkdir(parents=True, exist_ok=True)

    moved   = 0
    skipped = 0
    missing = 0

    for name in TO_ARCHIVE:
        src  = _SRC / name
        dst  = _ARCHIVE / name

        if dst.exists():
            print(f"  SKIPPED  {name}  (already in archive/)")
            skipped += 1
            continue

        try:
            shutil.move(str(src), str(dst))
            print(f"  MOVED    {name}")
            moved += 1
        except FileNotFoundError:
            print(f"  MISSING  {name}  (not found in downlifter/)")
            missing += 1
        except Exception as e:
            print(f"  ERROR    {name}  ({e})")
            missing += 1

    print()
    print(f"Done — moved: {moved}  skipped: {skipped}  not found: {missing}")
    print(f"Archive: {_ARCHIVE}")


if __name__ == "__main__":
    main()
