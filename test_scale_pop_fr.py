"""
test_scale_pop_fr.py — превью scale-pop субтитров для FR канала.
Arial Bold, тень 60%, 6 шагов ease-out без двоения (gte*lt границы).
"""
import subprocess, sys
from pathlib import Path

FONT  = "C\\:/Windows/Fonts/arialbd.ttf"
FS    = 68      # базовый fontsize
FO    = 0.20    # fade-out
SHX   = 3       # shadow offset X
SHY   = 4       # shadow offset Y
SHOP  = 0.60    # shadow opacity
DUR   = 20
OUT   = "test_scale_pop_fr.mp4"

# ease-out: 6 шагов, каждый 0.04s → всего 0.24s схлопывание
STEPS = [
    (0.04, 1.30),
    (0.04, 1.20),
    (0.04, 1.11),
    (0.04, 1.06),
    (0.04, 1.02),
    (0.04, 1.00),
]

groups = [
    (1.5,  3.2,  "LES ÉTOILES"),
    (3.7,  5.3,  "BRILLENT FORT"),
    (5.8,  7.4,  "DANS L'UNIVERS"),
    (7.9,  9.5,  "SANS FIN"),
    (10.0, 11.6, "CHAQUE NUIT"),
    (12.1, 13.8, "NOUS ÉMERVEILLE"),
]

def esc(t):
    return t.replace("'", "\u2019").replace(":", "\\:").replace(",", "\\,")

def enable_range(t0, t1, last=False):
    # half-open [t0, t1) — нет двоения на границах
    # последний шаг — включительно [t0, t1]
    if last:
        return f"gte(t\\,{t0:.4f})*lte(t\\,{t1:.4f})"
    return f"gte(t\\,{t0:.4f})*lt(t\\,{t1:.4f})"

def dt(txt, fs, alpha_expr, t0, t1, last=False):
    return (
        f"drawtext=fontfile='{FONT}':text='{txt}':fontsize={fs}"
        f":fontcolor=white:alpha='{alpha_expr}'"
        f":shadowcolor=black@{SHOP:.2f}:shadowx={SHX}:shadowy={SHY}"
        f":x=(w-text_w)/2:y=h-90"
        f":enable='{enable_range(t0, t1, last)}'"
    )

FADE_IN = 0.08  # fade-in растянут на первые 2 шага → 130% никогда не видно полностью

parts = []
for ts, te, text in groups:
    txt    = esc(text)
    cursor = ts

    for i, (step_dur, scale) in enumerate(STEPS):
        t0 = cursor
        t1 = round(cursor + step_dur, 4)
        fs = max(1, int(FS * scale))
        # alpha fade-in идёт ровно 0.08s (=2 шага), перекрывая scale-анимацию
        # → на шаге 0 (130%) alpha ≈ 0..0.5, на шаге 1 (120%) alpha ≈ 0.5..1.0
        # → большой размер никогда не виден полностью, нет ощущения "мигнул"
        alpha = f"min(1\\,(t-{ts:.4f})/{FADE_IN:.4f})"
        parts.append(dt(txt, fs, alpha, t0, t1, last=False))
        cursor = t1

    # Финальный удерживающий шаг [cursor, te] с fade-out
    alpha_fin = f"if(gt(t\\,{te - FO:.4f})\\,({te:.4f}-t)/{FO:.4f}\\,1)"
    parts.append(dt(txt, FS, alpha_fin, cursor, te, last=True))

vf = ",".join(parts)

Path("temp").mkdir(exist_ok=True)
vf_file = Path("temp/_test_scalepop_vf.txt")
vf_file.write_text(f"[0:v]{vf}[out]", encoding="utf-8")

# Фон средне-серый чтобы видно тень
cmd = [
    "ffmpeg", "-y",
    "-f", "lavfi",
    "-i", f"color=c=0x4a4a4a:size=1920x1080:rate=25:duration={DUR}",
    "-filter_complex_script", str(vf_file),
    "-map", "[out]",
    "-c:v", "libx264", "-preset", "fast", "-crf", "20",
    "-t", str(DUR), OUT,
]

print(f"Рендерим {OUT} ...")
r = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8", errors="replace")
if r.returncode != 0:
    print("ОШИБКА:")
    print(r.stderr[-1000:])
    sys.exit(1)
print(f"Готово: {Path(OUT).resolve()}")
