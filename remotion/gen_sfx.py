"""
gen_sfx.py — improved SFX generator for Remotion motion graphics.
Techniques: layered synthesis, Schroeder reverb, tanh saturation,
inharmonic bell partials, filtered noise, comb filters.
"""

import math
import random
import struct
import wave
from pathlib import Path

OUT = Path(__file__).parent / "public" / "sfx"
OUT.mkdir(parents=True, exist_ok=True)

SR = 44100
random.seed(42)


# ──────────────────────────────────────────────────────────────────────────────
# UTILS
# ──────────────────────────────────────────────────────────────────────────────

def write_wav(path: Path, samples: list[float], normalize=True):
    peak = max(abs(s) for s in samples) if samples else 1
    if normalize and peak > 0.001:
        samples = [s / peak * 0.92 for s in samples]
    with wave.open(str(path), "w") as f:
        f.setnchannels(1)
        f.setsampwidth(2)
        f.setframerate(SR)
        raw = struct.pack(
            f"<{len(samples)}h",
            *[int(max(-32767, min(32767, s * 32767))) for s in samples],
        )
        f.writeframes(raw)
    kb = path.stat().st_size // 1024
    ms = len(samples) * 1000 // SR
    print(f"  {path.name:<22} {ms:>4}ms  {kb:>3}KB")


def sine(t, freq):
    return math.sin(2 * math.pi * freq * t)


def noise_sample():
    return random.uniform(-1, 1)


def saturate(x, drive=0.7):
    """Soft-knee tanh saturation."""
    return math.tanh(x * (1 + drive)) / (1 + drive)


def env_adsr(t, dur, a=0.005, d=0.0, s=1.0, r=0.06):
    rel_start = dur - r
    if t < a:             return t / a
    if d > 0 and t < a+d: return 1.0 - (t - a) / d * (1 - s)
    if t < rel_start:     return s
    return s * max(0.0, 1.0 - (t - rel_start) / r)


def schroeder_reverb(samples: list[float], room=0.28, damp=0.55) -> list[float]:
    """Simple Schroeder comb + allpass reverb."""
    # comb filters
    comb_delays = [1557, 1617, 1491, 1422]
    out = [0.0] * len(samples)
    for d in comb_delays:
        buf = [0.0] * d
        idx = 0
        fb  = [0.0] * len(samples)
        for i in range(len(samples)):
            y      = buf[idx]
            buf[idx] = samples[i] + y * room * damp
            fb[i]  = y
            idx    = (idx + 1) % d
        for i in range(len(samples)):
            out[i] += fb[i] * 0.25
    # mix dry + wet
    return [samples[i] * 0.65 + out[i] * 0.35 for i in range(len(samples))]


def lowpass(samples: list[float], cutoff_hz: float) -> list[float]:
    """One-pole IIR lowpass."""
    rc  = 1.0 / (2 * math.pi * cutoff_hz)
    dt  = 1.0 / SR
    a   = dt / (rc + dt)
    out = [0.0] * len(samples)
    prev = 0.0
    for i, s in enumerate(samples):
        prev    = prev + a * (s - prev)
        out[i]  = prev
    return out


def highpass(samples: list[float], cutoff_hz: float) -> list[float]:
    """One-pole IIR highpass."""
    rc  = 1.0 / (2 * math.pi * cutoff_hz)
    dt  = 1.0 / SR
    a   = rc / (rc + dt)
    out = [0.0] * len(samples)
    prev_in = prev_out = 0.0
    for i, s in enumerate(samples):
        out[i]   = a * (prev_out + s - prev_in)
        prev_in  = s
        prev_out = out[i]
    return out


# ──────────────────────────────────────────────────────────────────────────────
# SFX
# ──────────────────────────────────────────────────────────────────────────────

def gen_impact(dur=0.50):
    """
    Soft thud — no click transient, just a gentle sub+mid bloom with reverb.
    """
    n = int(SR * dur)
    samples = []
    for i in range(n):
        t = i / SR
        # Soft sub-bass bloom
        sub_freq = 55 + 30 * math.exp(-t * 6)
        sub = sine(t, sub_freq) * math.exp(-t * 5) * 0.70
        # Warm mid body
        mid = sine(t, 130) * math.exp(-t * 10) * 0.35
        # Gentle ring (no sharp transient)
        ring = (
            sine(t, 520) * math.exp(-t * 14) * 0.14
          + sine(t, 980) * math.exp(-t * 22) * 0.06
        )
        raw = sub + mid + ring
        samples.append(raw)  # no saturation

    samples = schroeder_reverb(samples, room=0.30, damp=0.50)
    write_wav(OUT / "impact.wav", samples)


def gen_ping(dur=0.70):
    """
    Airy bell — slow attack, long reverb tail, no click, very soft.
    """
    n = int(SR * dur)
    base = 880.0
    partials = [
        (base * 1.000, 1.00, 6),
        (base * 2.756, 0.40, 9),
        (base * 5.404, 0.18, 14),
        (base * 0.500, 0.50, 4),
    ]
    samples = []
    for i in range(n):
        t = i / SR
        a = env_adsr(t, dur, a=0.025, r=0.35)   # slow attack, long release
        s = sum(
            sine(t, f) * amp * math.exp(-t * decay)
            for f, amp, decay in partials
        )
        samples.append(s * a)

    samples = schroeder_reverb(samples, room=0.48, damp=0.42)
    write_wav(OUT / "ping.wav", samples)


def gen_rise(dur=0.32):
    """
    Gentle upward sweep — soft, airy, no saturation.
    """
    n = int(SR * dur)
    samples = []
    for i in range(n):
        t = i / SR
        p = t / dur
        freq = 320 + 1400 * (p ** 1.4)
        a    = env_adsr(t, dur, a=0.025, r=0.12)
        s    = (
            sine(t, freq)       * 0.55
          + sine(t, freq * 2)   * 0.18
          + sine(t, freq * 0.5) * 0.12
        )
        samples.append(s * a)

    samples = schroeder_reverb(samples, room=0.22, damp=0.60)
    write_wav(OUT / "rise.wav", samples)


def gen_whoosh_in(dur=0.36):
    """
    Soft entrance swoosh — slow fade in, gentle sweep, long tail.
    """
    n = int(SR * dur)
    samples = []
    for i in range(n):
        t = i / SR
        p = t / dur
        freq = 2200 * (1 - p) ** 1.6 + 200
        a    = env_adsr(t, dur, a=0.030, r=0.14)
        s    = sine(t, freq) * 0.55 + sine(t, freq * 0.5) * 0.20
        samples.append(s * a)

    samples = highpass(samples, 100)
    samples = schroeder_reverb(samples, room=0.20, damp=0.65)
    write_wav(OUT / "whoosh_in.wav", samples)


def gen_whoosh_out(dur=0.24):
    """
    Exit swoosh: reverse-style — low → high, fast.
    """
    n = int(SR * dur)
    samples = []
    for i in range(n):
        t = i / SR
        p = t / dur
        freq = 160 + 2600 * (p ** 2.2)
        a    = env_adsr(t, dur, a=0.002, r=0.10) * (1 - p * 0.6)

        s = (
            sine(t, freq)     * 0.50
          + sine(t, freq * 2) * 0.20
          + noise_sample()    * 0.15
        )
        samples.append(saturate(s * a, drive=0.35))

    samples = highpass(samples, 100)
    write_wav(OUT / "whoosh_out.wav", samples)


def gen_tick(dur=0.035):
    """
    Crisp hi-hat tick for number count. Short, clean, no mud.
    """
    n = int(SR * dur)
    samples = []
    for i in range(n):
        t = i / SR
        a = env_adsr(t, dur, a=0.001, r=0.022) * 0.55
        # Metallic noise + high sine transient
        s = noise_sample() * 0.80 + sine(t, 4200) * 0.35 + sine(t, 6800) * 0.15
        samples.append(s * a)

    # highpass so it's crisp not bassy
    samples = highpass(samples, 2800)
    write_wav(OUT / "tick.wav", samples)


def gen_stinger(dur=0.65):
    """
    Soft shimmer sting — gentle fifth harmony, slow bloom, long reverb.
    No dissonance, just airy warmth.
    """
    n = int(SR * dur)
    base = 1047.0  # C6
    partials = [
        (base,       1.00, 5),
        (base * 1.5, 0.55, 7),    # fifth
        (base * 2.0, 0.28, 9),    # octave
        (base * 3.0, 0.12, 13),
    ]
    samples = []
    for i in range(n):
        t = i / SR
        a = env_adsr(t, dur, a=0.040, r=0.30)
        s = sum(
            sine(t, f) * amp * math.exp(-t * decay)
            for f, amp, decay in partials
        )
        samples.append(s * a)

    samples = schroeder_reverb(samples, room=0.50, damp=0.40)
    write_wav(OUT / "stinger.wav", samples)


# ──────────────────────────────────────────────────────────────────────────────
if __name__ == "__main__":
    print("Generating SFX ->", OUT)
    gen_impact()
    gen_ping()
    gen_rise()
    gen_whoosh_in()
    gen_whoosh_out()
    gen_tick()
    gen_stinger()
    print("Done.")
