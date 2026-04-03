"""
CTA overlay v1 — ES channel  (spiritual / divine theme)
MOV / PNG codec (lossless RGBA), ~13s, 1920x1080

Timeline:
  0.00-1.20  divine light (fade in 0.2s, лучи дышат)
  1.20-2.50  морф: золотое сияние → золотая кнопка "Suscríbete"
  2.50-3.10  hold
  3.10-4.40  курсор летит снизу к кнопке
  4.40-4.80  клик → тёмно-сакральный + "Suscrito"
  4.80-5.50  hold, курсор уходит в PARK
  5.50-6.10  кнопка уходит вправо
  6.20-6.70  золотой бейдж колокольчика
  6.70-7.40  курсор → бейдж
  7.40-7.60  клик по колокольчику
  7.60-8.40  звон, курсор → PARK
  8.40-8.90  колокольчик уходит / сердце заходит (тандем)
  8.90-9.60  курсор → сердце
  9.60-9.80  клик по сердцу
  9.80-10.6  сердце бьётся (двойной удар)
  10.6-11.1  бейдж масштабируется в 0
  11.1-11.6  курсор уходит вниз
"""
import sys, math, random, subprocess
from pathlib import Path

try:
    from PIL import Image, ImageDraw, ImageFont
except ImportError:
    print("pip install pillow"); sys.exit(1)

OUT  = Path("D:/Video-pipeline-temp/test_cta_es.mov")
W, H = 1920, 1080
FPS  = 25

# ── Timeline ──────────────────────────────────────────────────────────────────
T_DIVINE_END  = 1.20
T_MORPH_END   = 2.50
T_HOLD_END    = 3.10
T_CURSOR_ARR  = 4.40
T_CLICK_END   = 4.80
T_BTN_EXIT    = 5.50
T_BTN_GONE    = 6.10

T_BELL_APPEAR = 6.20
T_BELL_READY  = 6.70
T_BELL_ARR    = 7.40
T_BELL_CLICK  = 7.60
T_BELL_RING   = 8.40
T_BELL_EXIT   = 8.40
T_SWAP_END    = 8.90

T_HEART_ARR   = 9.60
T_HEART_CLICK = 9.80
T_HEART_ANIM  = 10.60
T_HEART_GONE  = 11.10

T_CUR_EXIT    = 11.10
T_CUR_GONE    = 11.60
T_TOTAL       = 13.00
NFRM          = int(T_TOTAL * FPS)

# ── Геометрия ─────────────────────────────────────────────────────────────────
CW, CH = 500, 90
CX     = W - CW - 60
CY     = H - CH - 110
RADIUS = 24
BTN_CX = CX + CW // 2
BTN_CY = CY + CH // 2

BADGE_SIZE = 82
BADGE_CX   = CX + CW - BADGE_SIZE // 2 - 10
BADGE_CY   = CY + CH // 2

# ── Цвета ─────────────────────────────────────────────────────────────────────
GOLD         = (200, 158,  28)   # золотая кнопка подписки
DARK_SACRED  = ( 30,  18,   8)   # после клика — тёмно-сакральный
WHITE        = (255, 255, 255)
WARM_BLACK   = ( 22,  12,   4)   # текст на золоте
ICON_COL     = (255, 255, 255)

# Divine light
DIVINE_WHITE = (255, 252, 210)   # яркое тёплое белое ядро
DIVINE_GLOW  = (218, 162,  20)   # золотое сияние
DIVINE_RAYS  = (255, 208,  70)   # лучи
BADGE_COL    = (155, 120,  15)   # тёмное золото для бейджа

# ── Шрифт ────────────────────────────────────────────────────────────────────
FONT_DIR  = Path("C:/Windows/Fonts")
font_main = ImageFont.truetype(str(FONT_DIR / "Montserrat-VariableFont_wght.ttf"), 46)
try:
    font_main.set_variation_by_axes([800])
except Exception:
    pass

# ── Искры вокруг сияния ───────────────────────────────────────────────────────
_rng    = random.Random(33)
SPARKLES = [(BTN_CX + _rng.randint(-200, 200),
             BTN_CY + _rng.randint(-95,   95),
             _rng.randint(1, 3)) for _ in range(16)]

# ── Позиции курсора ───────────────────────────────────────────────────────────
CUR_P0    = (W // 2 - 80, H + 100)
CUR_CTRL  = (CX + 50,     CY + 180)
CUR_P1    = (BTN_CX - 14, BTN_CY - 10)
CUR_PARK  = (BADGE_CX - 110, BADGE_CY + 70)
CUR_BADGE = (BADGE_CX - 14,  BADGE_CY - 12)

# ── Easing ────────────────────────────────────────────────────────────────────
def ease_out(t): return 1 - (1 - max(0.0, min(1.0, t))) ** 3
def ease_in(t):  return max(0.0, min(1.0, t)) ** 3
def ease_io(t):
    t = max(0.0, min(1.0, t))
    return t * t * (3 - 2 * t)
def ease_sine(t):
    t = max(0.0, min(1.0, t))
    return -(math.cos(math.pi * t) - 1) / 2

def bezier2(t, p0, p1, p2):
    t = max(0.0, min(1.0, t)); u = 1 - t
    return (u*u*p0[0]+2*u*t*p1[0]+t*t*p2[0],
            u*u*p0[1]+2*u*t*p1[1]+t*t*p2[1])
def lerp2(t, a, b):
    t = max(0.0, min(1.0, t))
    return (a[0]+(b[0]-a[0])*t, a[1]+(b[1]-a[1])*t)
def lerp_col(t, a, b):
    t = max(0.0, min(1.0, t))
    return tuple(int(a[i]+(b[i]-a[i])*t) for i in range(3))

# ── Курсор ────────────────────────────────────────────────────────────────────
def draw_cursor(draw, x, y, alpha=255, scale=1.0):
    s = 34 * scale
    if s < 4: return
    def pts(ox=0, oy=0):
        return [(x+ox,y+oy),(x+ox,y+oy+s),
                (x+ox+s*0.26,y+oy+s*0.65),(x+ox+s*0.44,y+oy+s*0.93),
                (x+ox+s*0.58,y+oy+s*0.85),(x+ox+s*0.38,y+oy+s*0.58),
                (x+ox+s*0.67,y+oy+s*0.58)]
    a = int(max(0, min(255, alpha)))
    for dx, dy in [(-1,0),(1,0),(0,-1),(0,1),(-1,-1),(1,1),(-1,1),(1,-1)]:
        draw.polygon(pts(dx, dy), fill=(0, 0, 0, min(a, 200)))
    draw.polygon(pts(), fill=(255, 255, 255, a))

# ── Колокольчик ───────────────────────────────────────────────────────────────
def draw_bell(draw, cx, cy, size, angle_deg, color):
    r = math.radians(angle_deg); c_, s_ = math.cos(r), math.sin(r)
    def rot(px, py):
        dx, dy = px-cx, py-cy
        return (cx+dx*c_-dy*s_, cy+dx*s_+dy*c_)
    z = size
    draw.polygon([rot(cx+dx,cy+dy) for dx,dy in
                  [(-z*.30,-z*.05),(z*.30,-z*.05),(z*.42,z*.38),(-z*.42,z*.38)]], fill=color)
    draw.polygon([rot(cx+math.cos(math.pi+i*math.pi/12)*z*.32,
                      cy+math.sin(math.pi+i*math.pi/12)*z*.28-z*.05)
                  for i in range(13)], fill=color)
    draw.line([rot(cx,cy-z*.30), rot(cx,cy-z*.48)], fill=color, width=max(2,int(z//11)))
    cl = rot(cx+s_*z*.18, cy+z*.44); cr = max(2, int(z//7))
    draw.ellipse([cl[0]-cr,cl[1]-cr,cl[0]+cr,cl[1]+cr], fill=color)

# ── Сердце (параметрическое) ──────────────────────────────────────────────────
def draw_heart(draw, cx, cy, size, color):
    z = size * 0.036   # масштаб параметрического уравнения
    pts = []
    n = 80
    for i in range(n):
        theta = 2 * math.pi * i / n
        hx = 16 * math.sin(theta) ** 3
        hy = -(13*math.cos(theta) - 5*math.cos(2*theta)
               - 2*math.cos(3*theta) - math.cos(4*theta))
        pts.append((cx + hx * z, cy + hy * z + size * 0.04))
    draw.polygon(pts, fill=color)

# ── Divine light эффекты ──────────────────────────────────────────────────────
def draw_divine_fx(draw, morph_t, vis, breathe, rx0, ry0, rx1, ry1, rr):
    if vis <= 0.01: return
    m = max(0.0, min(1.0, morph_t))

    glow_a   = max(0.0, 1.0 - m / 0.45) * vis * (0.70 + 0.30 * breathe)
    spark_a  = max(0.0, 1.0 - m / 0.58) * vis
    ray_a    = max(0.0, 1.0 - m / 0.26) * vis

    mcx = int((rx0 + rx1) / 2)
    mcy = int((ry0 + ry1) / 2)
    cur_r = int((ry1 - ry0) / 2)   # текущий «радиус» (полувысота)

    # Золотое сияние вокруг формы
    if glow_a > 0.01:
        ext = max(1, int(26 * (1 - m)))
        for g in range(ext, 0, -1):
            ga = int(26 * glow_a * g / ext)
            draw.rounded_rectangle(
                [rx0-g, ry0-g, rx1+g, ry1+g],
                radius=min(rr+g, cur_r+g),
                fill=(*DIVINE_GLOW, ga),
            )

    # Искры (звёздочки)
    if spark_a > 0.01:
        for sx, sy, sr in SPARKLES:
            draw.ellipse([sx-sr, sy-sr, sx+sr, sy+sr],
                         fill=(*DIVINE_RAYS, int(255 * spark_a)))

    # 12 световых лучей
    if ray_a > 0.01:
        r_inner = cur_r + 4
        r_outer = cur_r + int(20 * (1 - m * 0.6)) + int(9 * breathe)
        for i in range(12):
            angle   = 2 * math.pi * i / 12
            x1 = mcx + r_inner * math.cos(angle)
            y1 = mcy + r_inner * math.sin(angle)
            x2 = mcx + r_outer * math.cos(angle)
            y2 = mcy + r_outer * math.sin(angle)
            w  = max(1, int(3 * (1 - m)))
            draw.line([(x1,y1),(x2,y2)],
                      fill=(*DIVINE_RAYS, int(220 * ray_a)), width=w)

# ── Центровка текста ──────────────────────────────────────────────────────────
def draw_centered_text(draw, text, font, x_left, x_right, y_center, color):
    bb = font.getbbox(text); tw, th = bb[2]-bb[0], bb[3]-bb[1]
    tx = int(x_left + (x_right - x_left - tw) // 2)
    ty = int(y_center - th // 2 - bb[1])
    draw.text((tx, ty), text, fill=color, font=font, stroke_width=0)

# ── Кадр ──────────────────────────────────────────────────────────────────────
def make_frame(idx: int) -> Image.Image:
    t = idx / FPS

    # ── Divine / морф ────────────────────────────────────────────────────────
    if t < T_DIVINE_END:
        morph_t    = 0.0
        divine_vis = min(1.0, t / 0.20)
        breathe    = 0.5 + 0.5 * math.sin(2 * math.pi * 0.65 * t)
    elif t < T_MORPH_END:
        raw_t      = (t - T_DIVINE_END) / (T_MORPH_END - T_DIVINE_END)
        morph_t    = ease_sine(raw_t)
        divine_vis = 1.0
        breathe    = 0.5
    else:
        morph_t    = 1.0
        divine_vis = 0.0
        breathe    = 0.0

    # Геометрия: круг → плашка
    shape_w_t = ease_in(morph_t)           # расширение с ease_in — резкий взрыв в конце
    m_w  = CH + (CW - CH) * shape_w_t
    m_r  = (CH / 2) * (1 - morph_t) + RADIUS * morph_t
    rx0  = BTN_CX - m_w / 2;  ry0 = BTN_CY - CH / 2
    rx1  = BTN_CX + m_w / 2;  ry1 = BTN_CY + CH / 2
    rr   = int(m_r)

    # ── Слайд/выход ──────────────────────────────────────────────────────────
    if t < T_MORPH_END:
        slide_x = 0
        plk_a   = divine_vis
    elif t > T_BTN_EXIT:
        p       = min(1.0, (t - T_BTN_EXIT) / (T_BTN_GONE - T_BTN_EXIT))
        slide_x = int(ease_in(p) * (W - CX + 20))
        plk_a   = 1.0
    else:
        slide_x = 0
        plk_a   = 1.0

    # ── Цвет кнопки ──────────────────────────────────────────────────────────
    if T_CURSOR_ARR <= t <= T_CLICK_END:
        p_c     = ease_io((t - T_CURSOR_ARR) / (T_CLICK_END - T_CURSOR_ARR))
        btn_col = lerp_col(p_c, GOLD, DARK_SACRED)
        v       = int(200 - 180 * p_c)   # золотой → тёмный
        txt_col = (v, int(v * 0.75), int(v * 0.3))
    elif t > T_CLICK_END:
        btn_col = DARK_SACRED
        txt_col = WHITE
    else:
        # Белое-золотое сияние → золото (bloom через morph_t**2.5)
        color_frac = morph_t ** 2.5
        btn_col    = lerp_col(color_frac, DIVINE_WHITE, GOLD)
        txt_col    = WARM_BLACK

    clicked = t > T_CLICK_END
    label   = "Suscrito" if clicked else "Suscríbete"

    # ── Bell badge ────────────────────────────────────────────────────────────
    if t < T_BELL_APPEAR:
        bell_vis = 0.0
    elif t < T_BELL_READY:
        bell_vis = ease_io((t - T_BELL_APPEAR) / (T_BELL_READY - T_BELL_APPEAR))
    elif t < T_BELL_EXIT:
        bell_vis = 1.0
    elif t < T_SWAP_END:
        bell_vis = 1.0 - ease_io((t - T_BELL_EXIT) / (T_SWAP_END - T_BELL_EXIT))
    else:
        bell_vis = 0.0

    bell_xoff = 0.0
    if T_BELL_APPEAR <= t < T_BELL_READY:
        bell_xoff = 52 * (1 - ease_io((t - T_BELL_APPEAR) / (T_BELL_READY - T_BELL_APPEAR)))
    elif T_BELL_EXIT <= t < T_SWAP_END:
        bell_xoff = -52 * ease_in((t - T_BELL_EXIT) / (T_SWAP_END - T_BELL_EXIT))

    swing = 0.0
    if t >= T_BELL_CLICK:
        st    = t - T_BELL_CLICK
        swing = math.sin(2*math.pi*5.5*st) * 22 * math.exp(-st*1.9)

    # ── Heart badge ───────────────────────────────────────────────────────────
    if t < T_BELL_EXIT:
        heart_vis = 0.0
    elif t < T_SWAP_END:
        heart_vis = ease_io((t - T_BELL_EXIT) / (T_SWAP_END - T_BELL_EXIT))
    elif t < T_HEART_GONE:
        heart_vis = 1.0
    else:
        heart_vis = 0.0

    heart_badge_scale = 1.0
    if T_HEART_ANIM <= t < T_HEART_GONE:
        heart_badge_scale = 1.0 - ease_in((t - T_HEART_ANIM) / (T_HEART_GONE - T_HEART_ANIM))
    elif t >= T_HEART_GONE:
        heart_badge_scale = 0.0

    heart_xoff = 0.0
    if T_BELL_EXIT <= t < T_SWAP_END:
        heart_xoff = 52 * (1 - ease_io((t - T_BELL_EXIT) / (T_SWAP_END - T_BELL_EXIT)))

    # Двойной удар сердца (lub-dub)
    heart_beat_scale = 1.0
    if t >= T_HEART_CLICK:
        st = t - T_HEART_CLICK
        lub  = 0.38 * math.exp(-((st * 5.5 - 0.80) ** 2) * 6)
        dub  = 0.22 * math.exp(-((st * 5.5 - 2.00) ** 2) * 6)
        decay = math.exp(-st * 0.9)
        heart_beat_scale = 1.0 + (lub + dub) * decay

    heart_icon_scale = 1.0
    if T_HEART_ARR <= t <= T_HEART_CLICK:
        p_c = (t - T_HEART_ARR) / (T_HEART_CLICK - T_HEART_ARR)
        heart_icon_scale = 1.0 - 0.35 * math.sin(math.pi * p_c)

    # ── Рисуем ────────────────────────────────────────────────────────────────
    img  = Image.new("RGBA", (W, H), (0, 0, 0, 0))
    draw = ImageDraw.Draw(img)

    bx   = CX + slide_x
    by   = CY
    rx0s = rx0 + slide_x
    rx1s = rx1 + slide_x

    # Divine light эффекты
    if divine_vis > 0.01:
        draw_divine_fx(draw, morph_t, divine_vis, breathe, rx0s, ry0, rx1s, ry1, rr)

    # Основная форма
    a_bg  = int(255 * plk_a)
    a_txt = int(255 * plk_a)
    brd   = tuple(max(0, c - 35) for c in btn_col)
    draw.rounded_rectangle(
        [rx0s, ry0, rx1s, ry1], radius=rr,
        fill=(*btn_col, a_bg),
        outline=(*brd, int(160 * plk_a)), width=2,
    )

    # Текст: появляется в последние 22% морфа
    if morph_t >= 1.0:
        draw_centered_text(draw, label, font_main,
                           bx+16, bx+CW-16, by+CH//2, (*txt_col, a_txt))
    elif morph_t > 0.78:
        tf = (morph_t - 0.78) / 0.22
        draw_centered_text(draw, label, font_main,
                           int(rx0s)+16, int(rx1s)-16, (ry0+ry1)/2,
                           (*txt_col, int(255*tf*plk_a)))

    # ── Bell badge (золотой фон) ──────────────────────────────────────────────
    if bell_vis > 0.01:
        bcx  = BADGE_CX + bell_xoff
        bcy  = float(BADGE_CY)
        bs   = BADGE_SIZE
        brd2 = tuple(max(0, c - 25) for c in BADGE_COL)
        draw.rounded_rectangle(
            [bcx-bs//2, bcy-bs//2, bcx+bs//2, bcy+bs//2], radius=18,
            fill=(*BADGE_COL, int(255*bell_vis)),
            outline=(*brd2, int(140*bell_vis)), width=2,
        )
        draw_bell(draw, bcx, bcy+2, bs*0.38, swing,
                  (*ICON_COL, int(255*bell_vis)))

    # ── Heart badge (золотой фон) ─────────────────────────────────────────────
    if heart_vis > 0.01 and heart_badge_scale > 0.005:
        lcx  = BADGE_CX + heart_xoff
        lcy  = float(BADGE_CY)
        bs   = int(BADGE_SIZE * heart_badge_scale)
        br   = max(2, int(18 * heart_badge_scale))
        if bs >= 4:
            brd2 = tuple(max(0, c - 25) for c in BADGE_COL)
            draw.rounded_rectangle(
                [lcx-bs//2, lcy-bs//2, lcx+bs//2, lcy+bs//2], radius=br,
                fill=(*BADGE_COL, int(255*heart_vis)),
                outline=(*brd2, int(140*heart_vis)), width=2,
            )
            eff_size = bs * 0.42 * heart_icon_scale * heart_beat_scale
            draw_heart(draw, lcx, lcy,
                       eff_size,
                       (*ICON_COL, int(255*heart_vis)))

    # ── Курсор ────────────────────────────────────────────────────────────────
    if T_HOLD_END <= t:
        cur_a = 255.0
        if t < T_HOLD_END + 0.45:
            cur_a = 255 * ease_out((t - T_HOLD_END) / 0.45)

        if t <= T_CURSOR_ARR:
            p_m = ease_io((t - T_HOLD_END) / (T_CURSOR_ARR - T_HOLD_END))
            cx_pos, cy_pos = bezier2(p_m, CUR_P0, CUR_CTRL, CUR_P1)
            c_scale = 1.0
        elif t <= T_CLICK_END:
            p_c = (t - T_CURSOR_ARR) / (T_CLICK_END - T_CURSOR_ARR)
            cx_pos, cy_pos = CUR_P1
            c_scale = 1.0 - 0.35 * math.sin(math.pi * p_c)
        elif t <= T_BTN_EXIT:
            p_m = ease_io((t - T_CLICK_END) / (T_BTN_EXIT - T_CLICK_END))
            cx_pos, cy_pos = lerp2(p_m, CUR_P1, CUR_PARK)
            c_scale = 1.0
        elif t <= T_BELL_READY:
            cx_pos, cy_pos = CUR_PARK
            c_scale = 1.0
        elif t <= T_BELL_ARR:
            p_m = ease_io((t - T_BELL_READY) / (T_BELL_ARR - T_BELL_READY))
            cx_pos, cy_pos = lerp2(p_m, CUR_PARK, CUR_BADGE)
            c_scale = 1.0
        elif t <= T_BELL_CLICK:
            p_c = (t - T_BELL_ARR) / (T_BELL_CLICK - T_BELL_ARR)
            cx_pos, cy_pos = CUR_BADGE
            c_scale = 1.0 - 0.35 * math.sin(math.pi * p_c)
        elif t <= T_BELL_RING:
            p_m = ease_io((t - T_BELL_CLICK) / (T_BELL_RING - T_BELL_CLICK))
            cx_pos, cy_pos = lerp2(p_m, CUR_BADGE, CUR_PARK)
            c_scale = 1.0
        elif t <= T_SWAP_END:
            cx_pos, cy_pos = CUR_PARK
            c_scale = 1.0
        elif t <= T_HEART_ARR:
            p_m = ease_io((t - T_SWAP_END) / (T_HEART_ARR - T_SWAP_END))
            cx_pos, cy_pos = lerp2(p_m, CUR_PARK, CUR_BADGE)
            c_scale = 1.0
        elif t <= T_HEART_CLICK:
            p_c = (t - T_HEART_ARR) / (T_HEART_CLICK - T_HEART_ARR)
            cx_pos, cy_pos = CUR_BADGE
            c_scale = 1.0 - 0.35 * math.sin(math.pi * p_c)
        elif t <= T_CUR_EXIT:
            p_m = ease_io((t - T_HEART_CLICK) / (T_CUR_EXIT - T_HEART_CLICK))
            cx_pos, cy_pos = lerp2(p_m, CUR_BADGE, CUR_PARK)
            c_scale = 1.0
        else:
            p_exit = ease_in((t - T_CUR_EXIT) / (T_CUR_GONE - T_CUR_EXIT))
            cx_pos = CUR_PARK[0]
            cy_pos = CUR_PARK[1] + p_exit * (H - CUR_PARK[1] + 60)
            c_scale = 1.0

        if t > T_CUR_EXIT:
            p_fo  = (t - T_CUR_EXIT) / (T_CUR_GONE - T_CUR_EXIT)
            cur_a = 255 * (1 - p_fo)

        cur_a = max(0, min(255, cur_a))
        draw_cursor(draw, cx_pos, cy_pos, alpha=cur_a, scale=c_scale)

    return img


# ── FFmpeg — PNG/MOV (lossless RGBA) ─────────────────────────────────────────
cmd = [
    "ffmpeg", "-y",
    "-f", "rawvideo", "-vcodec", "rawvideo",
    "-s", f"{W}x{H}", "-pix_fmt", "rgba",
    "-r", str(FPS), "-i", "-",
    "-c:v", "png", "-pix_fmt", "rgba",
    str(OUT),
]

print(f"Render {NFRM} frames -> {OUT}")
proc = subprocess.Popen(cmd, stdin=subprocess.PIPE)
for i in range(NFRM):
    if i % 25 == 0:
        print(f"  {i}/{NFRM}  t={i/FPS:.1f}s")
    proc.stdin.write(make_frame(i).tobytes())
proc.stdin.close()
proc.wait()
print(f"Done: {OUT}  rc={proc.returncode}")
