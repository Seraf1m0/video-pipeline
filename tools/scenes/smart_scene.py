"""
SmartScene v2 -- frame-accurate SFX mixing via FFmpeg.

Uses pre-normalized sfx_norm/ library. No on-the-fly measurement.
Per-category volume multipliers applied at mix time.

API:
    self.sfx("whoosh_fast")          # play at current cursor
    self.sfx("tick", at=1.5)         # play at absolute time
    self.sfx("tick", at="+0.3")      # play 0.3s after cursor
    self.go(*anims, dur, rf, sfx, sfx_n)   # animate + sound
    self.hold(t, sfx)                # wait + optional sound
    self.sound(name, at=None)        # alias for self.sfx
"""

import random
import subprocess
from pathlib import Path
from manim import *

SFX_NORM = Path(__file__).resolve().parent.parent.parent / "assets" / "sfx_norm"

# Per-category mix volume multipliers (applied at mix time on top of normalization)
CATEGORY_VOL: dict[str, float] = {
    "whoosh":           0.22,
    "whoosh_fast":      0.16,
    "whoosh_big":       0.28,
    "infographic_tick": 0.18,
    "notification":     0.58,
    "riser":            0.14,
    "boom":             0.26,
}

# (sfx_norm_folder, fade_in, fade_out, max_dur, min_dur)
PROFILES: dict[str, tuple] = {
    "whoosh":      ("whoosh",           0.00, 0.20, 2.5,  0.5),
    "whoosh_fast": ("whoosh_fast",      0.00, 0.10, 0.8,  0.3),
    "whoosh_big":  ("whoosh_big",       0.05, 0.25, 3.0,  0.5),
    "tick":        ("infographic_tick", 0.00, 0.04, 0.5,  None),
    "done":        ("notification",     0.05, 0.20, 2.0,  0.2),
    "keyboard":    ("keyboard",         0.00, 0.04, 0.3,  None),
    "riser":       ("riser",            0.40, 0.50, 5.0,  1.0),
    "boom":        ("boom",             0.00, 0.25, 4.0,  0.5),
}

TICK_TRIM = 0.10   # hard trim for tick sounds (punchy click, no tail)

_dur_cache: dict[str, float] = {}


# ---- utilities ---------------------------------------------------------------

def _sfx_dur(path: str) -> float:
    if path in _dur_cache:
        return _dur_cache[path]
    try:
        r = subprocess.run(
            ["ffprobe", "-v", "quiet", "-show_entries", "format=duration",
             "-of", "csv=p=0", path],
            capture_output=True, text=True, timeout=10,
        )
        v = float(r.stdout.strip())
    except Exception:
        v = 2.0
    _dur_cache[path] = v
    return v


def _pick(folder: str, max_dur: float = None, min_dur: float = None,
          exclude: set = None) -> str | None:
    """Pick a random normalized SFX file from sfx_norm/<folder>/."""
    d = SFX_NORM / folder
    if not d.exists():
        return None
    fs = sorted([f for f in d.iterdir() if f.suffix.lower() in (".mp3", ".wav")])
    if exclude:
        fs = [f for f in fs if str(f) not in exclude]
    if not fs:
        return None
    if max_dur is None and min_dur is None:
        return str(random.choice(fs))
    filtered = [f for f in fs if _dur_in_range(str(f), min_dur, max_dur)]
    return str(random.choice(filtered)) if filtered else str(random.choice(fs))


def _dur_in_range(path: str, mn, mx) -> bool:
    d = _sfx_dur(path)
    if mn and d < mn:
        return False
    if mx and d > mx:
        return False
    return True


# ---- event -------------------------------------------------------------------

class _Ev:
    __slots__ = ("t", "path", "vol", "fi", "fo", "trim")

    def __init__(self, t, path, vol, fi, fo, trim=None):
        self.t    = t       # absolute start time in video (seconds)
        self.path = path
        self.vol  = vol     # linear volume multiplier
        self.fi   = fi      # fade-in duration (s)
        self.fo   = fo      # fade-out duration (s)
        self.trim = trim    # max playback duration (s), or None


# ---- SmartScene --------------------------------------------------------------

class SmartScene(Scene):

    def setup(self):
        self._cur:  float      = 0.0
        self._evs:  list[_Ev]  = []

    @property
    def cursor(self) -> float:
        return self._cur

    # ---- public API ----------------------------------------------------------

    def sfx(self, name: str, at=None):
        """Schedule a sound. at=None -> cursor, at=float -> absolute, at='+N' -> cursor+N."""
        self._sched(name, at=at)

    def sound(self, name: str, at=None):
        """Alias for sfx()."""
        self._sched(name, at=at)

    def go(
        self,
        *anims,
        dur:   float = 1.0,
        rf=smooth,
        sfx:   str   = None,
        sfx_n: int   = None,
        **kw,
    ):
        """Animate + optional sound. sfx_n distributes tick sounds along rf curve."""
        if sfx:
            if sfx == "tick" and sfx_n:
                self._sched_ticks(dur=dur, n=sfx_n, rf=rf)
            else:
                self._sched(sfx)
        self.play(*anims, run_time=dur, rate_func=rf, **kw)
        self._cur += dur

    def hold(self, t: float, sfx: str = None):
        """Wait t seconds, optionally play a sound at the start."""
        if sfx:
            self._sched(sfx)
        self.wait(t)
        self._cur += t

    # ---- internals -----------------------------------------------------------

    def _resolve_t(self, at) -> float:
        if at is None:
            return self._cur
        if isinstance(at, str) and at.startswith("+"):
            return self._cur + float(at[1:])
        return float(at)

    def _sched(self, name: str, at=None):
        t = self._resolve_t(at)
        prof = PROFILES.get(name)
        if prof is None:
            print(f"[WARN] unknown sfx profile: {name}")
            return
        folder, fi, fo, max_dur, min_dur = prof
        cat_key = folder   # folder name is the CATEGORY_VOL key
        vol = CATEGORY_VOL.get(cat_key, 0.20)

        p = _pick(folder, max_dur=max_dur, min_dur=min_dur)
        if not p:
            return
        self._evs.append(_Ev(t, p, vol, fi, fo, trim=None))

    def _sched_ticks(self, dur: float, n: int, rf=None, at: float = None):
        """Distribute n ticks along rf curve, each a different file, hard-trimmed."""
        t0 = self._cur if at is None else at
        folder, fi, fo, max_dur, _ = PROFILES["tick"]
        vol = CATEGORY_VOL.get(folder, 0.18)

        d = SFX_NORM / folder
        if not d.exists():
            return
        all_files = sorted([str(f) for f in d.iterdir()
                            if f.suffix.lower() in (".mp3", ".wav")])
        if not all_files:
            return

        used: set[str] = set()
        for i in range(n):
            if n > 1:
                progress = i / (n - 1)
                t_frac = self._rf_inv(rf, progress) if rf is not None else progress
            else:
                t_frac = 0.0

            # pick a different file each tick
            candidates = [f for f in all_files if f not in used]
            if not candidates:
                used.clear()
                candidates = all_files
            p = random.choice(candidates)
            used.add(p)

            self._evs.append(_Ev(t0 + t_frac * dur, p, vol, fi, fo, trim=TICK_TRIM))

    @staticmethod
    def _rf_inv(rf, v: float, steps: int = 60) -> float:
        """Numerical inversion of rate_func: find t s.t. rf(t) ~= v."""
        lo, hi = 0.0, 1.0
        for _ in range(steps):
            mid = (lo + hi) * 0.5
            (lo if rf(mid) < v else hi).__class__  # dummy — branch below
            if rf(mid) < v:
                lo = mid
            else:
                hi = mid
        return (lo + hi) * 0.5

    # ---- render hook ---------------------------------------------------------

    def render(self, preview=False):
        super().render(preview)
        if not self._evs:
            return
        try:
            fw  = self.renderer.file_writer
            src = Path(
                getattr(fw, "movie_file_path", None)
                or getattr(fw, "_movie_file_path", None)
                or getattr(fw, "final_file_path", None)
                or ""
            )
            if src.exists():
                self._remaster(src)
            else:
                print(f"[WARN] output file not found: {src}")
        except Exception as e:
            import traceback
            print(f"[WARN] remaster failed: {e}")
            traceback.print_exc()

    # ---- ffmpeg mix ----------------------------------------------------------

    def _remaster(self, video: Path):
        vid_dur = _sfx_dur(str(video))
        inputs  = ["-i", str(video)]
        parts, labels = [], []

        for i, ev in enumerate(self._evs):
            sd       = _sfx_dur(ev.path)
            delay_ms = int(ev.t * 1000)

            # effective playback duration (after trim)
            eff_dur = ev.trim if ev.trim else sd

            chain = f"[{i+1}:a]"

            # trim to max playback duration
            if ev.trim:
                chain += f"atrim=duration={ev.trim:.4f},asetpts=PTS-STARTPTS,"

            # fades (skip if file is very short)
            if eff_dur >= 0.05:
                if ev.fi > 0 and eff_dur > ev.fi:
                    chain += f"afade=t=in:st=0:d={ev.fi:.3f},"
                if ev.fo > 0:
                    fo_st = max(0.0, eff_dur - ev.fo)
                    if fo_st > 0:
                        chain += f"afade=t=out:st={fo_st:.3f}:d={ev.fo:.3f},"
            else:
                # anti-click only
                chain += "afade=t=in:st=0:d=0.010,"

            # volume + resample + stereo
            chain += f"volume={ev.vol:.5f},"
            chain += "aresample=44100,aformat=channel_layouts=stereo"

            # delay (insert silence at start)
            if delay_ms > 0:
                chain += f",adelay={delay_ms}|{delay_ms}"

            chain += f"[s{i}]"
            parts.append(chain)
            labels.append(f"[s{i}]")
            inputs += ["-i", ev.path]

        n   = len(labels)
        mix = "".join(labels) + f"amix=inputs={n}:normalize=0[aout]"
        parts.append(mix)

        tmp = video.with_stem(video.stem + "_tmp")
        cmd = [
            "ffmpeg", "-y", *inputs,
            "-filter_complex", ";".join(parts),
            "-map", "0:v", "-map", "[aout]",
            "-c:v", "copy", "-c:a", "aac", "-b:a", "192k",
            "-t", str(vid_dur),
            str(tmp),
        ]
        r = subprocess.run(cmd, capture_output=True, text=True,
                           encoding="utf-8", errors="replace")
        if r.returncode == 0:
            try:
                tmp.replace(video)
            except OSError:
                try:
                    video.unlink(missing_ok=True)
                    tmp.rename(video)
                except Exception as e2:
                    print(f"[WARN] rename failed: {e2}")
                    return
            print(f"[OK] remastered: {video.name}")
        else:
            print(f"[ERR] ffmpeg:\n{r.stderr[-1200:]}")
            if tmp.exists():
                tmp.unlink()
