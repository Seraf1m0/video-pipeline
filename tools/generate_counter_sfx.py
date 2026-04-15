"""
5 пресетов уверенных плотных тиков каунтера.
Слой 1: клавиатура соло (тело, 0.16s)
Слой 2: мышь клик (атака, 0.12s)
Быстро, плотно, насыщенно.
"""

import subprocess, tempfile, os
from pathlib import Path

ROOT    = Path(__file__).resolve().parent.parent
KBD_DIR = ROOT / "assets" / "sfx" / "keyboard"
TICK_N  = ROOT / "assets" / "sfx_norm" / "infographic_tick"
OUT_DIR = ROOT / "assets" / "sfx" / "counter_presets"
OUT_DIR.mkdir(parents=True, exist_ok=True)

KBD   = str(KBD_DIR / "клавиатура соло.wav")
CLICK = str(KBD_DIR / "мышь клик.wav")
DIG   = [str(f) for f in sorted(TICK_N.glob("dig_click_*.wav"))[:8]]

TOTAL = 15.0
TILE  = 2.0


def make_tile(layers: list[tuple[str, float, float, float]], interval: float) -> str:
    """
    layers: [(file, trim_dur, vol, delay_offset), ...]
    Генерирует TILE секунд плотных тиков.
    """
    times = []
    t = 0.0
    while t < TILE:
        times.append(t)
        t += interval

    inputs, filters, labels = [], [], []
    idx = 0

    for t_hit in times:
        for (fpath, trim, vol, offset) in layers:
            delay_ms = int((t_hit + offset) * 1000)
            inputs += ["-i", fpath]
            fo_st = max(0.001, trim - 0.02)
            chain = (
                f"[{idx}:a]"
                f"atrim=duration={trim},"
                f"asetpts=PTS-STARTPTS,"
                f"afade=t=in:st=0:d=0.003,"
                f"afade=t=out:st={fo_st:.3f}:d=0.02,"
                f"volume={vol},"
                f"aresample=44100,aformat=channel_layouts=stereo"
            )
            if delay_ms > 0:
                chain += f",adelay={delay_ms}|{delay_ms}"
            chain += f"[s{idx}]"
            filters.append(chain)
            labels.append(f"[s{idx}]")
            idx += 1

    n = len(labels)
    filters.append("".join(labels) + f"amix=inputs={n}:normalize=0,volume=0.9[aout]")

    tmp = tempfile.NamedTemporaryFile(suffix=".wav", delete=False)
    tmp.close()
    cmd = [
        "ffmpeg", "-y", *inputs,
        "-filter_complex", ";".join(filters),
        "-map", "[aout]",
        "-t", str(TILE), "-ar", "44100", "-ac", "2", tmp.name,
    ]
    r = subprocess.run(cmd, capture_output=True)
    if r.returncode != 0:
        raise RuntimeError(r.stderr.decode(errors="replace")[-400:])
    return tmp.name


def tile_to_15s(tile_path: str, out: Path):
    loops = int(TOTAL / TILE) + 2
    # aloop: size = TILE * 44100 samples
    size = int(TILE * 44100)
    cmd = [
        "ffmpeg", "-y", "-i", tile_path,
        "-filter_complex", f"aloop=loop={loops}:size={size}",
        "-t", str(TOTAL), "-ar", "44100", "-ac", "2", str(out),
    ]
    r = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8", errors="replace")
    print("[OK]" if r.returncode == 0 else f"[ERR] {r.stderr[-200:]}", out.name)


def build(layers, interval, out):
    tile = make_tile(layers, interval)
    tile_to_15s(tile, out)
    os.unlink(tile)


if __name__ == "__main__":

    # 01 — Клава + клик, средне (0.14s) — референс
    build(
        layers=[(KBD, 0.16, 1.0, 0.0), (CLICK, 0.12, 0.55, 0.0)],
        interval=0.14,
        out=OUT_DIR / "counter_01_kbd_medium.wav",
    )

    # 02 — Клава + клик, быстро (0.09s)
    build(
        layers=[(KBD, 0.14, 1.0, 0.0), (CLICK, 0.10, 0.55, 0.0)],
        interval=0.09,
        out=OUT_DIR / "counter_02_kbd_fast.wav",
    )

    # 03 — Только клик, очень плотно (0.07s) — максимальная скорость
    build(
        layers=[(CLICK, 0.12, 1.1, 0.0), (CLICK, 0.12, 0.6, 0.035)],
        interval=0.07,
        out=OUT_DIR / "counter_03_click_ultra.wav",
    )

    # 04 — Клава одна, медленнее (0.18s) — более спокойный
    build(
        layers=[(KBD, 0.16, 1.1, 0.0)],
        interval=0.18,
        out=OUT_DIR / "counter_04_kbd_slow.wav",
    )

    # 05 — Клава + dig_click (смесь тембров), быстро (0.10s)
    build(
        layers=[(KBD, 0.14, 0.9, 0.0), (DIG[0], 0.12, 0.7, 0.01)],
        interval=0.10,
        out=OUT_DIR / "counter_05_kbd_dig.wav",
    )

    print("Done ->", OUT_DIR)
