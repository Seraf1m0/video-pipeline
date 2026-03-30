"""
Test: fadegrays transition between 2 clips.
Fade through gray — both clips desaturate + opacity crossfade.

Output: test_fadegrays_out.mp4
"""
import subprocess, sys, math
from pathlib import Path

CLIP_A = Path("library_religion/clips/0001.mp4")
CLIP_B = Path("library_religion/clips/0002.mp4")
OUT    = Path("test_fadegrays_out.mp4")
DUR    = 0.8   # transition duration seconds
SHOW   = 2.5   # seconds of each clip to show before/after transition

# ── Get durations ─────────────────────────────────────────────────────────────
def get_dur(p):
    r = subprocess.run(
        ["ffprobe", "-v", "quiet", "-show_entries", "format=duration",
         "-of", "default=noprint_wrappers=1:nokey=1", str(p)],
        capture_output=True, text=True)
    return float(r.stdout.strip())

dur_a = get_dur(CLIP_A)
dur_b = get_dur(CLIP_B)
print(f"Clip A: {dur_a:.2f}s   Clip B: {dur_b:.2f}s")

# We take: last SHOW+DUR seconds of clip A, first SHOW+DUR seconds of clip B
# Then blend the last DUR of A with first DUR of B (with desaturation on both)

ta_start = max(0.0, dur_a - SHOW - DUR)
tb_end   = min(dur_b, SHOW + DUR)

# ── Filter complex ─────────────────────────────────────────────────────────────
# Approach:
#   [a] → trim to last (SHOW+DUR)s → split into [a_main] and [a_blend_zone]
#   [b] → trim to first (SHOW+DUR)s → split into [b_blend_zone] and [b_main]
#   blend zone: desaturate both, cross-fade opacity (a fades out, b fades in)
#   final: concat [a_main] + [blended] + [b_main]
#
# Simpler: use xfade=dissolve but pre-apply hue=s=0 to both in transition zone
# via enable=. This avoids pixelation because hue filter is CPU but clean.
#
# Even simpler clean approach:
#   - Trim A last SHOW+DUR s, trim B first SHOW+DUR s
#   - Apply hue=s=0 gated to transition zone on both
#   - Use blend=all_expr='A*(1-T/DUR)+B*(T/DUR)' on the DUR-overlap portion
#   - Then concat A_main + blend_result + B_main

# Let's use the clean overlay approach:
# Steps:
# 1. [a]: trim start=ta_start, duration=SHOW+DUR → apply hue=s=0 on last DUR → [av]
# 2. [b]: trim start=0, duration=SHOW+DUR → apply hue=s=0 on first DUR → [bv]
# 3. From [av] take last DUR as [a_ov], from [bv] take first DUR as [b_ov]
# 4. blend [a_ov] over [b_ov] with opacity
# 5. concat: [av without last DUR] + [blended DUR] + [bv without first DUR]
#
# Actually easiest: just 2-input xfade=dissolve with hue pre-applied via enable=

a_dur_trim = min(SHOW + DUR, dur_a - ta_start)
b_dur_trim = tb_end

xfade_offset = a_dur_trim - DUR  # offset within the trimmed-a timeline

fcomplex = (
    f"[0:v]trim=start={ta_start:.3f},setpts=PTS-STARTPTS,"
    f"hue=s=0:enable='gte(t,{xfade_offset:.3f})'[av];"
    f"[1:v]trim=start=0:end={b_dur_trim:.3f},setpts=PTS-STARTPTS,"
    f"hue=s=0:enable='lte(t,{DUR:.3f})'[bv];"
    f"[av][bv]xfade=transition=dissolve:offset={xfade_offset:.3f}:duration={DUR:.3f}[out]"
)

cmd = [
    "ffmpeg", "-y",
    "-i", str(CLIP_A),
    "-i", str(CLIP_B),
    "-filter_complex", fcomplex,
    "-map", "[out]",
    "-c:v", "libx264", "-preset", "fast", "-crf", "18",
    "-an",
    str(OUT)
]

print("Running ffmpeg...")
print(" ".join(cmd))
r = subprocess.run(cmd, capture_output=True, text=True)
if r.returncode != 0:
    print("STDERR:", r.stderr[-2000:])
    sys.exit(1)

print(f"\n✓ Output: {OUT}")
print(f"  Duration: {get_dur(OUT):.2f}s")
