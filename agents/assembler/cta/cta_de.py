"""
CTA overlay v4 — DE channel
MOV / PNG codec (lossless RGBA alpha), 6.5s, 1920x1080

Фазы:
  0.00-0.55  slide in (красная, только "Abonnieren" по центру, без колокольчика)
  0.55-1.20  hold
  1.20-2.40  курсор летит по дуге к кнопке
  2.40-2.80  клик: плашка -> тёмно-серая, текст -> "Abonniert" + белый
  2.80-3.30  колокольчик появляется (fade in слева)
  3.30-4.00  курсор летит к колокольчику
  4.00-4.20  клик по колокольчику
  4.20-5.00  колокольчик звенит
  5.00-5.40  курсор уходит вниз за экран
  5.40-6.50  плашка уходит вправо за экран
"""
import sys, math, subprocess
from pathlib import Path

try:
    from PIL import Image, ImageDraw, ImageFont
except ImportError:
    print("pip install pillow"); sys.exit(1)

OUT  = Path("D:/Video-pipeline-temp/test_cta_de.mov")
W, H = 1920, 1080
FPS  = 25

# ── Timeline ──────────────────────────────────────────────────────────────────
T_ENTRY_END   = 0.55
T_HOLD_END    = 1.20
T_CURSOR_ARR  = 2.40
T_CLICK_END   = 2.80
T_BELL_READY  = 3.30   # колокольчик полностью появился
T_BELL_ARR    = 4.00   # курсор у колокольчика
T_BELL_CLICK  = 4.20   # колокольчик начинает звенеть
T_CUR_EXIT    = 5.00   # курсор начинает уходить вниз
T_CUR_GONE    = 5.40   # курсор ушёл
T_PLK_EXIT    = 5.40   # плашка начинает уходить вправо
T_TOTAL       = 6.50
NFRM          = int(T_TOTAL * FPS)

# ── Плашка ────────────────────────────────────────────────────────────────────
CW, CH = 500, 90
CX     = W - CW - 60
CY     = H - CH - 110
RADIUS = 24

# ── Цвета ─────────────────────────────────────────────────────────────────────
RED       = (255,  0,  0)
GRAY_BTN  = ( 66, 66, 74)
WHITE     = (255, 255, 255)
BLACK_TXT = ( 18,  18,  18)
BELL_COL  = (255, 255, 255)

# ── Шрифт (Montserrat ExtraBold) ──────────────────────────────────────────────
FONT_DIR  = Path("C:/Windows/Fonts")
_fpath    = str(FONT_DIR / "Montserrat-VariableFont_wght.ttf")
font_main = ImageFont.truetype(_fpath, 46)
try:
    font_main.set_variation_by_axes([800])
except Exception:
    pass

# ── Ключевые точки курсора ────────────────────────────────────────────────────
BTN_CX  = CX + CW // 2
BTN_CY  = CY + CH // 2
BELL_X  = CX + 52
BELL_Y  = CY + CH // 2

CUR_P0   = (W // 2 - 80, H + 100)       # старт — за экраном снизу
CUR_CTRL = (CX + 50,     CY + 180)       # контрольная точка дуги
CUR_P1   = (BTN_CX - 14, BTN_CY - 10)   # прицел на кнопку
CUR_BELL = (BELL_X - 10, BELL_Y - 8)    # прицел на колокольчик

# ── Easing ────────────────────────────────────────────────────────────────────
def ease_out(t): return 1 - (1 - max(0.0, min(1.0, t))) ** 3
def ease_in(t):  return max(0.0, min(1.0, t)) ** 3
def ease_io(t):
    t = max(0.0, min(1.0, t))
    return t * t * (3 - 2 * t)

def bezier2(t, p0, p1, p2):
    t = max(0.0, min(1.0, t))
    u = 1 - t
    return (u*u*p0[0] + 2*u*t*p1[0] + t*t*p2[0],
            u*u*p0[1] + 2*u*t*p1[1] + t*t*p2[1])

def lerp2(t, a, b):
    t = max(0.0, min(1.0, t))
    return (a[0] + (b[0]-a[0])*t, a[1] + (b[1]-a[1])*t)

# ── Курсор: белый с чёрной обводкой (RGBA) ───────────────────────────────────
def draw_cursor(draw, x, y, alpha=255, scale=1.0):
    s = 34 * scale
    if s < 4:
        return
    def pts(ox=0, oy=0):
        return [
            (x+ox,              y+oy),
            (x+ox,              y+oy + s),
            (x+ox + s*0.26,     y+oy + s*0.65),
            (x+ox + s*0.44,     y+oy + s*0.93),
            (x+ox + s*0.58,     y+oy + s*0.85),
            (x+ox + s*0.38,     y+oy + s*0.58),
            (x+ox + s*0.67,     y+oy + s*0.58),
        ]
    a = int(max(0, min(255, alpha)))
    for dx, dy in [(-1,0),(1,0),(0,-1),(0,1),(-1,-1),(1,1),(-1,1),(1,-1)]:
        draw.polygon(pts(dx, dy), fill=(0, 0, 0, min(a, 200)))
    draw.polygon(pts(), fill=(255, 255, 255, a))

# ── Колокольчик ───────────────────────────────────────────────────────────────
def draw_bell(draw, cx, cy, size, angle_deg, color):
    r    = math.radians(angle_deg)
    c_, s_ = math.cos(r), math.sin(r)
    def rot(px, py):
        dx, dy = px-cx, py-cy
        return (cx + dx*c_ - dy*s_, cy + dx*s_ + dy*c_)
    z = size
    body = [(-z*.30,-z*.05),(z*.30,-z*.05),(z*.42,z*.38),(-z*.42,z*.38)]
    draw.polygon([rot(cx+dx, cy+dy) for dx,dy in body], fill=color)
    dome = [rot(cx + math.cos(math.pi + i*math.pi/12)*z*.32,
                cy + math.sin(math.pi + i*math.pi/12)*z*.28 - z*.05)
            for i in range(13)]
    draw.polygon(dome, fill=color)
    draw.line([rot(cx, cy-z*.30), rot(cx, cy-z*.48)],
              fill=color, width=max(2, int(z//11)))
    cl = rot(cx + s_*z*.18, cy + z*.44)
    cr = max(2, int(z//7))
    draw.ellipse([cl[0]-cr, cl[1]-cr, cl[0]+cr, cl[1]+cr], fill=color)

# ── Центровка текста ──────────────────────────────────────────────────────────
def draw_centered_text(draw, text, font, x_left, x_right, y_center, color):
    bb = font.getbbox(text)
    tw = bb[2] - bb[0]
    th = bb[3] - bb[1]
    tx = int(x_left + (x_right - x_left - tw) // 2)
    ty = int(y_center - th // 2 - bb[1])
    draw.text((tx, ty), text, fill=color, font=font, stroke_width=0)

# ── Кадр ──────────────────────────────────────────────────────────────────────
def make_frame(idx: int) -> Image.Image:
    t = idx / FPS

    # --- Слайд плашки ---
    if t < T_ENTRY_END:
        slide_x = int((1 - ease_out(t / T_ENTRY_END)) * (CW + 90))
        plk_a   = ease_out(t / T_ENTRY_END)
    elif t > T_PLK_EXIT:
        p       = ease_in((t - T_PLK_EXIT) / (T_TOTAL - T_PLK_EXIT))
        slide_x = int(p * (W - CX + 20))
        plk_a   = 1.0
    else:
        slide_x = 0
        plk_a   = 1.0
    plk_a = max(0.0, min(1.0, plk_a))

    # --- Цвет кнопки ---
    if T_CURSOR_ARR <= t <= T_CLICK_END:
        p_c     = ease_io((t - T_CURSOR_ARR) / (T_CLICK_END - T_CURSOR_ARR))
        btn_col = tuple(int(RED[i] + (GRAY_BTN[i]-RED[i]) * p_c) for i in range(3))
        v       = int(18 + (255-18) * p_c)
        txt_col = (v, v, v)
    elif t > T_CLICK_END:
        btn_col = GRAY_BTN
        txt_col = WHITE
    else:
        btn_col = RED
        txt_col = BLACK_TXT

    clicked = t > T_CLICK_END
    label   = "Abonniert" if clicked else "Abonnieren"

    # --- Alpha колокольчика ---
    if t < T_CLICK_END:
        bell_a = 0.0
    elif t < T_BELL_READY:
        bell_a = ease_io((t - T_CLICK_END) / (T_BELL_READY - T_CLICK_END))
    else:
        bell_a = 1.0

    # --- Качание колокольчика ---
    if t >= T_BELL_CLICK:
        st    = t - T_BELL_CLICK
        swing = math.sin(2*math.pi*5.5*st) * 22 * math.exp(-st*1.9)
    else:
        swing = 0.0

    # --- Рисуем RGBA (прозрачный фон) ---
    img  = Image.new("RGBA", (W, H), (0, 0, 0, 0))
    draw = ImageDraw.Draw(img)

    bx = CX + slide_x
    by = CY

    a_bg  = int(245 * plk_a)
    a_txt = int(255 * plk_a)

    brd = tuple(max(0, c - 55) for c in btn_col)
    draw.rounded_rectangle(
        [bx, by, bx+CW, by+CH], radius=RADIUS,
        fill=(*btn_col, a_bg),
        outline=(*brd, int(200 * plk_a)), width=2,
    )

    # Колокольчик
    if bell_a > 0:
        b_alpha = int(255 * bell_a * plk_a)
        draw_bell(draw, bx+52, by+CH//2+2, 29, swing,
                  (*BELL_COL, b_alpha))
        lx = bx + 88
        draw.line([(lx, by+15), (lx, by+CH-15)],
                  fill=(*txt_col, int(80 * bell_a * plk_a)), width=1)
        draw_centered_text(draw, label, font_main,
                           lx + 14, bx+CW-16,
                           by + CH//2,
                           (*txt_col, a_txt))
    else:
        draw_centered_text(draw, label, font_main,
                           bx + 16, bx+CW-16,
                           by + CH//2,
                           (*txt_col, a_txt))

    # --- Курсор ---
    if T_HOLD_END <= t:
        if t < T_HOLD_END + 0.45:
            cur_a = 255 * ease_out((t - T_HOLD_END) / 0.45)
        else:
            cur_a = 255.0

        if t <= T_CURSOR_ARR:
            p_m = ease_io((t - T_HOLD_END) / (T_CURSOR_ARR - T_HOLD_END))
            cx_pos, cy_pos = bezier2(p_m, CUR_P0, CUR_CTRL, CUR_P1)
            c_scale = 1.0
        elif t <= T_CLICK_END:
            p_c = (t - T_CURSOR_ARR) / (T_CLICK_END - T_CURSOR_ARR)
            cx_pos, cy_pos = CUR_P1
            c_scale = 1.0 - 0.35 * math.sin(math.pi * p_c)
        elif t <= T_BELL_ARR:
            p_m = ease_io((t - T_CLICK_END) / (T_BELL_ARR - T_CLICK_END))
            cx_pos, cy_pos = lerp2(p_m, CUR_P1, CUR_BELL)
            c_scale = 1.0
        elif t <= T_BELL_CLICK:
            p_c = (t - T_BELL_ARR) / (T_BELL_CLICK - T_BELL_ARR)
            cx_pos, cy_pos = CUR_BELL
            c_scale = 1.0 - 0.35 * math.sin(math.pi * p_c)
        elif t <= T_CUR_EXIT:
            cx_pos, cy_pos = CUR_BELL
            c_scale = 1.0
        else:
            p_exit = ease_in((t - T_CUR_EXIT) / (T_CUR_GONE - T_CUR_EXIT))
            cx_pos = CUR_BELL[0]
            cy_pos = CUR_BELL[1] + p_exit * (H - CUR_BELL[1] + 60)
            c_scale = 1.0

        if t > T_CUR_EXIT:
            p_fo  = (t - T_CUR_EXIT) / (T_CUR_GONE - T_CUR_EXIT)
            cur_a = 255 * (1 - p_fo)

        cur_a = max(0, min(255, cur_a))
        draw_cursor(draw, cx_pos, cy_pos, alpha=cur_a, scale=c_scale)

    return img


# ── FFmpeg — PNG codec в MOV (lossless RGBA) ─────────────────────────────────
cmd = [
    "ffmpeg", "-y",
    "-f", "rawvideo", "-vcodec", "rawvideo",
    "-s", f"{W}x{H}", "-pix_fmt", "rgba",
    "-r", str(FPS), "-i", "-",
    "-c:v", "png",
    "-pix_fmt", "rgba",
    str(OUT),
]

print(f"Render {NFRM} frames -> {OUT}")
proc = subprocess.Popen(cmd, stdin=subprocess.PIPE)
for i in range(NFRM):
    if i % 30 == 0:
        print(f"  {i}/{NFRM}  t={i/FPS:.1f}s")
    proc.stdin.write(make_frame(i).tobytes())
proc.stdin.close()
proc.wait()
print(f"Done: {OUT}  rc={proc.returncode}")
