"""
Test: shape wipe transition — 3 rectangular shapes sweep left→right with stagger,
revealing clip B underneath. Pastel neutral colors.
"""
import subprocess
from pathlib import Path

CLIP_A = "library_religion/clips/0105.mp4"
CLIP_B = "library_religion/clips/0646.mp4"
OUT    = "test_shapewipe_out.mp4"

D   = 0.5   # total transition duration
S1  = 0.10  # stagger: shape 1 starts at S1
S2  = 0.18  # stagger: shape 2 starts at S2
W0, W1, W2 = 220, 160, 100  # shape widths in pixels

# Pastel neutral colors (RGB hex)
C0 = "0xF5EBD2"  # warm cream
C1 = "0xC3C3C8"  # soft gray
C2 = "0xE1D7CD"  # light beige

# x_lead_i(t) — leading edge of each shape (for drawbox x=)
def xl(stagger, w):
    rem = D - stagger
    s = f"{stagger:.4f}"
    r = f"{rem:.4f}"
    w = int(w)
    return f"(iw+{w})*(1-cos(3.14159265*clip((t-{s})/{r},0,1)))/2-{w}"

xl0 = xl(0.0,  W0)
xl1 = xl(S1,   W1)
xl2 = xl(S2,   W2)

# Blend base: B left of shape2's trailing edge, A right
# x_trail2 = xl2 + W2 = (W+W2)*ease - W2 + W2 = (W+W2)*ease
def xl_T(stagger, w):
    rem = D - stagger
    s = f"{stagger:.4f}"
    r = f"{rem:.4f}"
    return f"(W+{w})*(1-cos(3.14159265*clip((T-{s})/{r},0,1)))/2"

x_trail2_T = xl_T(S2, W2)
blend_expr = f"if(lt(X,{x_trail2_T}),B,A)"

# Duration of A clip to use
dur_main = 2.0  # seconds before transition

fc = (
    f"[0:v]format=yuv420p,split=2[a1][a2];"
    f"[1:v]format=yuv420p,split=2[b1][b2];"
    f"[a1]trim=end={dur_main:.3f},setpts=PTS-STARTPTS[am];"
    f"[a2]trim=start={dur_main:.3f}:end={dur_main+D:.3f},setpts=PTS-STARTPTS[at];"
    f"[b1]trim=end={D:.3f},setpts=PTS-STARTPTS[bh];"
    f"[b2]trim=start={D:.3f}:end={dur_main+D:.3f},setpts=PTS-STARTPTS[bm];"
    f"[at][bh]blend=all_expr='{blend_expr}'[base];"
    f"[base]"
    f"drawbox=x='{xl2}':y=0:w={W2}:h=ih:color={C2}:t=fill,"
    f"drawbox=x='{xl1}':y=0:w={W1}:h=ih:color={C1}:t=fill,"
    f"drawbox=x='{xl0}':y=0:w={W0}:h=ih:color={C0}:t=fill"
    f"[trans];"
    f"[am][trans][bm]concat=n=3:v=1:a=0[out]"
)

cmd = [
    "ffmpeg", "-y",
    "-i", CLIP_A, "-i", CLIP_B,
    "-filter_complex", fc,
    "-map", "[out]",
    "-c:v", "libx264", "-preset", "fast", "-crf", "18", "-an",
    OUT
]

r = subprocess.run(cmd, capture_output=True, text=True)
if r.returncode != 0:
    print("STDERR:", r.stderr[-3000:])
else:
    print(f"Done: {OUT}")
