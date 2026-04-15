"""
Синтез каунтер-тиков полностью через FFmpeg — без исходных аудиофайлов.

Каждый тик = noise burst с огибающей (attack + exponential decay):
  - body layer:  bandpass ~350 Hz — "плотность и тело"
  - click layer: bandpass ~5 kHz  — "атака и чёткость"

5 пресетов с разной плотностью, характером и балансом слоёв.
"""

import subprocess
import tempfile
import os
from pathlib import Path

ROOT    = Path(__file__).resolve().parent.parent
OUT_DIR = ROOT / "assets" / "sfx" / "counter_presets"
OUT_DIR.mkdir(parents=True, exist_ok=True)

TOTAL = 15.0
TILE  = 2.0
SR    = 44100


def synth_tick(
    body_freq: float, body_bw: float, body_vol: float, body_decay: float,
    click_freq: float, click_bw: float, click_vol: float, click_decay: float,
    duration: float = 0.12,
    seed_body: int = 42, seed_click: int = 99,
) -> str:
    """Синтезирует один тик через FFmpeg, возвращает путь к tmp файлу."""

    # body: низкочастотный шум с медленным затуханием
    body_fo_st = max(0.001, duration - body_decay * 3)
    # click: высокочастотный шум с быстрым затуханием
    click_fo_st = max(0.001, min(duration * 0.3, click_decay * 2))

    body_chain = (
        f"[0:a]"
        f"bandpass=f={body_freq}:width_type=h:w={body_bw},"
        f"volume={body_vol},"
        f"afade=t=in:st=0:d=0.001,"
        f"afade=t=out:st={body_fo_st:.4f}:d={body_decay:.4f},"
        f"aresample={SR},aformat=channel_layouts=stereo"
        f"[body]"
    )
    click_chain = (
        f"[1:a]"
        f"bandpass=f={click_freq}:width_type=h:w={click_bw},"
        f"volume={click_vol},"
        f"afade=t=in:st=0:d=0.0005,"
        f"afade=t=out:st={click_fo_st:.4f}:d={click_decay:.4f},"
        f"aresample={SR},aformat=channel_layouts=stereo"
        f"[click]"
    )
    mix = "[body][click]amix=inputs=2:normalize=0,volume=1.0[out]"

    tmp = tempfile.NamedTemporaryFile(suffix=".wav", delete=False)
    tmp.close()

    cmd = [
        "ffmpeg", "-y",
        "-f", "lavfi", "-i", f"anoisesrc=color=white:seed={seed_body}:duration={duration}",
        "-f", "lavfi", "-i", f"anoisesrc=color=white:seed={seed_click}:duration={duration}",
        "-filter_complex", ";".join([body_chain, click_chain, mix]),
        "-map", "[out]",
        "-t", str(duration), "-ar", str(SR), "-ac", "2",
        tmp.name,
    ]
    r = subprocess.run(cmd, capture_output=True)
    if r.returncode != 0:
        raise RuntimeError(r.stderr.decode(errors="replace")[-400:])
    return tmp.name


def make_tile(tick_path: str, interval: float) -> str:
    """Укладывает тик с заданным интервалом в TILE-секундный файл."""
    times = []
    t = 0.0
    while t < TILE:
        times.append(t)
        t += interval

    inputs, filters, labels = ["-i", tick_path], [], []
    for i, t_hit in enumerate(times):
        delay_ms = int(t_hit * 1000)
        chain = f"[0:a]"
        if delay_ms > 0:
            chain = (
                f"[0:a]aresample={SR},aformat=channel_layouts=stereo,"
                f"adelay={delay_ms}|{delay_ms}"
            )
        else:
            chain = f"[0:a]aresample={SR},aformat=channel_layouts=stereo"
        chain += f"[s{i}]"
        filters.append(chain)
        labels.append(f"[s{i}]")

    n = len(labels)
    filters.append("".join(labels) + f"amix=inputs={n}:normalize=0,volume=0.95[aout]")

    tmp = tempfile.NamedTemporaryFile(suffix=".wav", delete=False)
    tmp.close()
    cmd = [
        "ffmpeg", "-y", *inputs,
        "-filter_complex", ";".join(filters),
        "-map", "[aout]",
        "-t", str(TILE), "-ar", str(SR), "-ac", "2", tmp.name,
    ]
    r = subprocess.run(cmd, capture_output=True)
    if r.returncode != 0:
        raise RuntimeError(r.stderr.decode(errors="replace")[-400:])
    return tmp.name


def tile_to_15s(tile_path: str, out: Path):
    loops = int(TOTAL / TILE) + 2
    size  = int(TILE * SR)
    cmd = [
        "ffmpeg", "-y", "-i", tile_path,
        "-filter_complex", f"aloop=loop={loops}:size={size}",
        "-t", str(TOTAL), "-ar", str(SR), "-ac", "2", str(out),
    ]
    r = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8", errors="replace")
    print("[OK]" if r.returncode == 0 else f"[ERR] {r.stderr[-200:]}", out.name)


def build(tick_params: dict, interval: float, out: Path):
    tick = synth_tick(**tick_params)
    tile = make_tile(tick, interval)
    tile_to_15s(tile, out)
    os.unlink(tick)
    os.unlink(tile)


# ──────────────────────────────────────────────────────────────────
# 5 ПРЕСЕТОВ
# ──────────────────────────────────────────────────────────────────

PRESETS = [
    # 01 — средний темп, уверенный UI-клик
    dict(
        tick_params=dict(
            body_freq=1200, body_bw=1400, body_vol=4.5, body_decay=0.022,
            click_freq=5500, click_bw=5000, click_vol=3.5, click_decay=0.010,
            duration=0.055, seed_body=42, seed_click=99,
        ),
        interval=0.13,
        name="counter_01_synth_medium.wav",
    ),
    # 02 — быстрый, острый
    dict(
        tick_params=dict(
            body_freq=1500, body_bw=1800, body_vol=4.0, body_decay=0.015,
            click_freq=7000, click_bw=6000, click_vol=4.0, click_decay=0.007,
            duration=0.045, seed_body=17, seed_click=55,
        ),
        interval=0.09,
        name="counter_02_synth_fast.wav",
    ),
    # 03 — ультраплотный
    dict(
        tick_params=dict(
            body_freq=1800, body_bw=2000, body_vol=3.5, body_decay=0.012,
            click_freq=8000, click_bw=7000, click_vol=4.5, click_decay=0.006,
            duration=0.038, seed_body=7, seed_click=13,
        ),
        interval=0.065,
        name="counter_03_synth_ultra.wav",
    ),
    # 04 — медленный, чуть теплее (мид-лоу)
    dict(
        tick_params=dict(
            body_freq=900, body_bw=1000, body_vol=5.0, body_decay=0.030,
            click_freq=4500, click_bw=4000, click_vol=3.0, click_decay=0.015,
            duration=0.070, seed_body=88, seed_click=33,
        ),
        interval=0.17,
        name="counter_04_synth_heavy.wav",
    ),
    # 05 — хрустящий высокий клик
    dict(
        tick_params=dict(
            body_freq=1600, body_bw=2000, body_vol=3.0, body_decay=0.018,
            click_freq=9500, click_bw=9000, click_vol=5.5, click_decay=0.006,
            duration=0.050, seed_body=61, seed_click=77,
        ),
        interval=0.11,
        name="counter_05_synth_crisp.wav",
    ),
]

if __name__ == "__main__":
    for p in PRESETS:
        build(
            tick_params=p["tick_params"],
            interval=p["interval"],
            out=OUT_DIR / p["name"],
        )
    print("Done ->", OUT_DIR)
