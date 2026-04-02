"""
test_streamcopy.py — сравнение скорости:
  [A] _gray_wipe_chunk    — один большой filter_complex (текущий)
  [B] parallel + stream copy — параллельные переходы + stream copy тела

Запуск: python test_streamcopy.py [--clips N] [--channel es|de|fr]
"""
import sys, time, shutil, os, json, subprocess, argparse
from pathlib import Path
from concurrent.futures import ThreadPoolExecutor

sys.path.insert(0, "agents/assembler")
sys.path.insert(0, "agents/library")
sys.path.insert(0, "agents/utils")

from transitions import (
    _gray_wipe_chunk, get_video_duration,
    GPU_ENCODER_FAST, GPU_PARAMS_FAST,
    set_temp_dir,
)
from paths import LIBRARY_MAP, TEMP_ROOT

# ── Args ──────────────────────────────────────────────────────────────
parser = argparse.ArgumentParser()
parser.add_argument("--clips",   type=int, default=20, help="кол-во клипов для теста")
parser.add_argument("--channel", default="religion",   help="cosmos | religion")
parser.add_argument("--dur",     type=float, default=0.8, help="длительность перехода")
args = parser.parse_args()

N   = args.clips
D   = args.dur
FPS = 25
fpf = 1.0 / FPS

print(f"{'='*58}")
print(f"  Stream Copy тест — {N} клипов, переход={D}s")
print(f"{'='*58}")

# ── Найти клипы в библиотеке ─────────────────────────────────────────
lib = LIBRARY_MAP.get(args.channel, LIBRARY_MAP["religion"])
clips_dir = lib / "clips"
all_clips = sorted(clips_dir.glob("*.mp4"))
if len(all_clips) < N:
    print(f"❌ Мало клипов в библиотеке: {len(all_clips)} < {N}"); sys.exit(1)

# Берём каждый K-й для разнообразия
step = max(1, len(all_clips) // N)
clips = [all_clips[i * step] for i in range(N)]
print(f"Библиотека: {lib.name}  ({len(all_clips)} клипов)")
print(f"Тестируем:  {N} клипов (каждый {step}-й)\n")

# ── Temp dir ──────────────────────────────────────────────────────────
tmp = TEMP_ROOT / "test_streamcopy"
shutil.rmtree(tmp, ignore_errors=True)
tmp.mkdir(parents=True)
set_temp_dir(tmp)

# ── Обрезаем клипы до случайной длины 2-4s (имитация Zone A) ─────────
print("Подготовка: trim клипов до 2-4s...")
t_trim = time.time()
trimmed = []
durations = []

DURS = [2.0, 3.5, 2.5, 4.0, 3.0, 2.8, 3.2, 2.2, 4.0, 3.8,
        2.5, 3.0, 2.0, 3.5, 2.8, 4.0, 2.2, 3.0, 3.5, 2.0]

def _trim_one(args):
    src, dst, dur = args
    subprocess.run([
        "ffmpeg", "-y", "-i", str(src),
        "-t", f"{dur:.2f}", "-c:v", "copy", "-an",
        "-loglevel", "error", str(dst),
    ], capture_output=True)
    return dst

trim_args = []
for i, clip in enumerate(clips):
    dur = DURS[i % len(DURS)]
    dst = tmp / f"trimmed_{i:03d}.mp4"
    trim_args.append((clip, dst, dur))
    durations.append(dur)

with ThreadPoolExecutor(max_workers=12) as ex:
    trimmed = list(ex.map(_trim_one, trim_args))

print(f"  ✅ {len(trimmed)} клипов за {time.time()-t_trim:.1f}s  "
      f"(total {sum(durations):.1f}s)")

# ── Wipe expression ───────────────────────────────────────────────────
wipe_expr = (
    f"clip(((W+100)*(1-cos(3.14159265*T/{D:.4f}))/2-X)/100,0,1)"
    f"*B+(1-clip(((W+100)*(1-cos(3.14159265*T/{D:.4f}))/2-X)/100,0,1))*A"
)

# ════════════════════════════════════════════════════════════════════
# [A] _gray_wipe_chunk — текущий подход
# ════════════════════════════════════════════════════════════════════
print(f"\n{'─'*58}")
print(f"[A] _gray_wipe_chunk  (один filter_complex, текущий)")
print(f"{'─'*58}")

out_a = tmp / "out_A_chunk.mp4"
t0 = time.time()
_gray_wipe_chunk(trimmed, durations, out_a, duration=D, fps=FPS)
t_a = time.time() - t0

dur_a  = get_video_duration(str(out_a)) if out_a.exists() else 0
size_a = out_a.stat().st_size // 1024 if out_a.exists() else 0
print(f"  Время:    {t_a:.1f}s")
print(f"  Видео:    {dur_a:.1f}s,  {size_a//1024}MB")

# ════════════════════════════════════════════════════════════════════
# [B] Parallel transitions + stream copy bodies
# ════════════════════════════════════════════════════════════════════
print(f"\n{'─'*58}")
print(f"[B] Parallel NVENC transitions + stream copy bodies")
print(f"{'─'*58}")

out_b = tmp / "out_B_streamcopy.mp4"

def encode_transition(i):
    """Только переход: freeze хвост клипа i → blend с головой клипа i+1 (d сек).
    -ss перед -i: быстрый seek к последнему кадру, не декодируем весь клип."""
    out = tmp / f"b_trans_{i:04d}.mp4"
    da      = durations[i]
    # Seek чуть раньше конца (0.5s), чтобы попасть на ближайший keyframe
    seek_t  = max(0.0, da - 0.5)
    head_d  = min(D, max(0.04, durations[i + 1] * 0.45))

    fc = (
        # A: с нужного места → берём последние кадры → freeze на D секунд
        f"[0:v]fps={FPS},format=yuv420p,setpts=PTS-STARTPTS,"
        f"tpad=stop=-1:stop_mode=clone:stop_duration={D:.4f},"
        f"trim=0:{D:.4f},setpts=PTS-STARTPTS[ta];"
        # B: первые head_d секунд клипа i+1 (реальный контент)
        f"[1:v]fps={FPS},format=yuv420p,setpts=PTS-STARTPTS[tb];"
        f"[ta][tb]blend=all_expr='{wipe_expr}'[out]"
    )
    hwaccel = ["-hwaccel", "auto"] if GPU_ENCODER_FAST == "h264_nvenc" else []
    cmd = [
        "ffmpeg", "-y",
        *hwaccel, "-ss", f"{seek_t:.4f}", "-i", str(trimmed[i]),
        "-t", f"{head_d:.4f}", "-i", str(trimmed[i + 1]),
        "-filter_complex", fc,
        "-map", "[out]",
        "-c:v", GPU_ENCODER_FAST, *GPU_PARAMS_FAST,
        "-pix_fmt", "yuv420p", "-an", "-r", str(FPS),
        "-loglevel", "error", str(out),
    ]
    subprocess.run(cmd, capture_output=True)
    return out


def copy_body(i):
    """Stream copy тела клипа (без перекодирования)"""
    out      = tmp / f"b_body_{i:04d}.mp4"
    da       = durations[i]
    has_left = (i > 0)
    head_d   = min(D, max(0.04, da * 0.45)) if has_left else 0.0
    body_dur = max(0.04, da - head_d)

    cmd = [
        "ffmpeg", "-y",
        "-ss", f"{head_d:.4f}",
        "-i", str(trimmed[i]),
        "-t", f"{body_dur:.4f}",
        "-c", "copy", "-an",
        "-loglevel", "error", str(out),
    ]
    subprocess.run(cmd, capture_output=True)
    return out


t0 = time.time()
n = len(trimmed)

# Transitions и bodies параллельно
with ThreadPoolExecutor(max_workers=8)  as tx, \
     ThreadPoolExecutor(max_workers=12) as bx:
    t_futs = [tx.submit(encode_transition, i) for i in range(n - 1)]
    b_futs = [bx.submit(copy_body,         i) for i in range(n)]
    trans_outs = [f.result() for f in t_futs]
    body_outs  = [f.result() for f in b_futs]

t_parallel = time.time() - t0
print(f"  Параллельный этап: {t_parallel:.1f}s  "
      f"({n-1} переходов + {n} тел одновременно)")

# Финальный concat — c copy (без перекодирования)
concat_parts = []
for i in range(n):
    concat_parts.append(body_outs[i])
    if i < n - 1:
        concat_parts.append(trans_outs[i])

concat_file = str(tmp / "streamcopy_list.txt")
with open(concat_file, "w", encoding="utf-8") as f:
    for p in concat_parts:
        f.write(f"file '{os.path.abspath(str(p))}'\n")

t_concat_start = time.time()
r = subprocess.run([
    "ffmpeg", "-y", "-f", "concat", "-safe", "0",
    "-i", concat_file, "-c", "copy",
    "-loglevel", "warning", str(out_b),
], capture_output=True, text=True)
t_concat = time.time() - t_concat_start

t_b = time.time() - t0
dur_b  = get_video_duration(str(out_b)) if out_b.exists() else 0
size_b = out_b.stat().st_size // 1024 if out_b.exists() else 0

if r.returncode != 0:
    print(f"  ⚠ concat error: {r.stderr[-300:]}")
else:
    print(f"  Concat (-c copy): {t_concat:.1f}s")
    print(f"  Время итого:  {t_b:.1f}s")
    print(f"  Видео:    {dur_b:.1f}s,  {size_b//1024}MB")

# ════════════════════════════════════════════════════════════════════
# Итог
# ════════════════════════════════════════════════════════════════════
print(f"\n{'='*58}")
print(f"  РЕЗУЛЬТАТ ({N} клипов, переход={D}s)")
print(f"{'='*58}")
print(f"  [A] filter_complex (текущий):  {t_a:6.1f}s")
print(f"  [B] stream copy parallel:      {t_b:6.1f}s")
if t_b > 0:
    print(f"  Ускорение:                     {t_a/t_b:.1f}x быстрее")
print(f"  Длина видео A: {dur_a:.1f}s  |  B: {dur_b:.1f}s")
print(f"{'='*58}")

# Cleanup
shutil.rmtree(tmp, ignore_errors=True)
print("Temp очищен.")
