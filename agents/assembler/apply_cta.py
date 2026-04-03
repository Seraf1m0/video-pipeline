"""
apply_cta.py — standalone CTA overlay tool

Накладывает плашку "Подписаться" (RGBA MOV) на готовое видео.
Не требует перезапуска ассемблера.

Использование:
    python apply_cta.py <video.mp4> <cta.mov> [options]

    python apply_cta.py final.mp4 cta_de.mov
    python apply_cta.py final.mp4 cta_de.mov --timestamps 120 240 360
    python apply_cta.py final.mp4 cta_de.mov --channel channel_001_cosmos_de
    python apply_cta.py final.mp4 cta_de.mov --min-gap 90 --max-gap 120
    python apply_cta.py final.mp4 cta_de.mov --output final_cta.mp4

По умолчанию: тайминги генерируются автоматически (random в [min_gap, max_gap]).
Выходной файл: <stem>_cta.<ext> если --output не указан.
"""
import argparse
import json
import math
import random
import subprocess
import sys
import time
from pathlib import Path

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
if hasattr(sys.stderr, "reconfigure"):
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")

NVENC_PRESET = "p4"


def get_duration(path: str | Path) -> float:
    r = subprocess.run(
        ["ffprobe", "-v", "quiet", "-print_format", "json",
         "-show_streams", str(path)],
        capture_output=True, text=True,
    )
    try:
        for s in json.loads(r.stdout).get("streams", []):
            if s.get("codec_type") == "video":
                return float(s["duration"])
        # fallback: first stream
        return float(json.loads(r.stdout)["streams"][0]["duration"])
    except Exception:
        return 0.0


def compute_timestamps(
    total_dur:  float,
    intro_dur:  float,
    cta_dur:    float,
    min_gap_s:  float = 90.0,
    max_gap_s:  float = 120.0,
    end_buffer: float = 75.0,
) -> list[float]:
    timestamps = []
    earliest = intro_dur + 30.0
    latest   = total_dur - end_buffer - cta_dur
    if earliest > latest:
        return []
    t = earliest
    while t <= latest:
        timestamps.append(round(t, 1))
        t += random.uniform(min_gap_s, max_gap_s)
    return timestamps


def apply_cta(
    video_path:  Path,
    cta_path:    Path,
    timestamps:  list[float],
    output_path: Path,
) -> bool:
    if not timestamps:
        print("[CTA] Нет временных меток — нечего накладывать")
        return False

    print(f"[CTA] Видео:    {video_path.name}")
    print(f"[CTA] Плашка:   {cta_path.name}")
    print(f"[CTA] Тайминги: {[f'{t:.0f}s' for t in timestamps]}")
    print(f"[CTA] Выход:    {output_path.name}")

    cmd = ["ffmpeg", "-y", "-i", str(video_path)]
    for _ in timestamps:
        cmd += ["-i", str(cta_path)]

    filter_parts = []
    prev = "0:v"
    for i, ts in enumerate(timestamps):
        idx     = i + 1
        out_lbl = f"v{idx}"
        filter_parts.append(
            f"[{idx}:v]setpts=PTS+{ts}/TB[cta{idx}];"
            f"[{prev}][cta{idx}]overlay=0:0:eof_action=pass[{out_lbl}]"
        )
        prev = out_lbl

    cmd += [
        "-filter_complex", ";".join(filter_parts),
        "-map", f"[{prev}]",
        "-map", "0:a",
        "-c:v", "h264_nvenc", "-preset", NVENC_PRESET, "-cq", "26",
        "-c:a", "copy",
        str(output_path),
    ]

    t0 = time.time()
    rc = subprocess.run(cmd, capture_output=True)
    elapsed = time.time() - t0

    if rc.returncode != 0:
        print(f"[CTA] ✗ FFmpeg ошибка (rc={rc.returncode}):")
        print(rc.stderr.decode(errors="replace")[-800:])
        return False

    size_mb = output_path.stat().st_size / 1024 / 1024 if output_path.exists() else 0
    print(f"[CTA] ✅ Готово за {elapsed:.1f}s  {size_mb:.0f} MB → {output_path}")
    return True


def main():
    ap = argparse.ArgumentParser(
        description="Наложить CTA-плашку на готовое видео (post-process)"
    )
    ap.add_argument("video",   help="Путь к финальному видео .mp4")
    ap.add_argument("cta_mov", help="Путь к CTA-плашке .mov (RGBA)")
    ap.add_argument("--output",     "-o", help="Выходной файл (по умолчанию <stem>_cta.mp4)")
    ap.add_argument("--timestamps", "-t", type=float, nargs="+",
                    help="Явные тайминги в секундах (пример: 120 240 360)")
    ap.add_argument("--channel",    "-c", help="ID канала из channel_styles.json (берёт min/max gap)")
    ap.add_argument("--intro-dur",  type=float, default=0.0,
                    help="Длительность интро в секундах (по умолчанию 0)")
    ap.add_argument("--min-gap",    type=float, default=90.0,
                    help="Минимальный интервал между плашками (сек, default 90)")
    ap.add_argument("--max-gap",    type=float, default=120.0,
                    help="Максимальный интервал между плашками (сек, default 120)")
    args = ap.parse_args()

    video_path = Path(args.video)
    cta_path   = Path(args.cta_mov)

    if not video_path.exists():
        print(f"✗ Видео не найдено: {video_path}"); sys.exit(1)
    if not cta_path.exists():
        print(f"✗ CTA MOV не найден: {cta_path}"); sys.exit(1)

    output_path = Path(args.output) if args.output \
        else video_path.with_stem(video_path.stem + "_cta")

    # Читаем channel_styles.json если указан канал
    min_gap = args.min_gap
    max_gap = args.max_gap
    if args.channel:
        styles_path = Path(__file__).parent.parent.parent / "config" / "channel_styles.json"
        if styles_path.exists():
            with open(styles_path, encoding="utf-8") as f:
                styles = json.load(f)
            ch = styles.get(args.channel, {})
            min_gap = float(ch.get("cta_min_gap_s", min_gap))
            max_gap = float(ch.get("cta_max_gap_s", max_gap))
            print(f"[CTA] Канал {args.channel}: gap [{min_gap:.0f}s, {max_gap:.0f}s]")

    # Тайминги
    if args.timestamps:
        timestamps = args.timestamps
    else:
        total_dur  = get_duration(video_path)
        cta_dur    = get_duration(cta_path)
        intro_dur  = args.intro_dur
        if total_dur <= 0:
            print(f"✗ Не удалось определить длительность видео"); sys.exit(1)
        print(f"[CTA] Длительность видео: {total_dur:.1f}s, CTA: {cta_dur:.1f}s, интро: {intro_dur:.1f}s")
        timestamps = compute_timestamps(total_dur, intro_dur, cta_dur, min_gap, max_gap)
        if not timestamps:
            print("[CTA] Видео слишком короткое для CTA"); sys.exit(0)

    ok = apply_cta(video_path, cta_path, timestamps, output_path)
    sys.exit(0 if ok else 1)


if __name__ == "__main__":
    main()
