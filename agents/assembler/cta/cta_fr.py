"""
CTA overlay v3 — FR channel
MOV / PNG codec (lossless RGBA), ~13s, 1920x1080

Timeline:
  0.00-1.20  планета чёрная (fade in 0.2s, дышит)
  1.20-2.50  морф: чёрный круг → красная кнопка (ease_in расширение + bloom)
  2.50-3.10  hold кнопка
  3.10-4.40  курсор летит снизу к кнопке
  4.40-4.80  клик → почти чёрный + "Abonné"
  4.80-5.50  hold, курсор уходит в PARK
  5.50-6.10  кнопка уходит вправо
  6.20-6.70  бейдж колокольчика появляется
  6.70-7.40  курсор → бейдж
  7.40-7.60  клик по колокольчику
  7.60-8.40  звон, курсор уходит в PARK
  8.40-8.90  колокольчик уходит влево / комментарий заходит справа
  8.90-9.60  курсор → комментарий
  9.60-9.80  клик
  9.80-10.6  bounce
  10.6-11.1  бейдж → scale 0
  11.1-11.6  курсор уходит вниз
  11.6-13.0  пусто
"""
import sys, math, random, subprocess
from pathlib import Path

try:
    from PIL import Image, ImageDraw, ImageFont
except ImportError:
    print("pip install pillow"); sys.exit(1)

OUT  = Path("D:/Video-pipeline-temp/test_cta_fr.mov")
W, H = 1920, 1080
FPS  = 25

# ── Timeline ──────────────────────────────────────────────────────────────────
T_PLANET_END  = 1.20
T_MORPH_END   = 2.50   # 1.3s морфа — достаточно для плавного перехода
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

T_LIKE_ARR    = 9.60
T_LIKE_CLICK  = 9.80
T_LIKE_ANIM   = 10.60
T_LIKE_GONE   = 11.10

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
RED         = (255,   0,   0)
NEAR_BLACK  = ( 14,  14,  18)   # подписан + бейдж
WHITE       = (255, 255, 255)
BLACK_TXT   = ( 18,  18,  18)
ICON_COL    = (255, 255, 255)

# Планета: чёрная с красным свечением
PLANET_COL  = (  0,   0,   0)   # чёрный
PLANET_RING = (130,  15,  15)   # тёмно-красное кольцо
PLANET_GLOW = ( 70,   0,   0)   # красное свечение

# ── Шрифт ────────────────────────────────────────────────────────────────────
FONT_DIR  = Path("C:/Windows/Fonts")
font_main = ImageFont.truetype(str(FONT_DIR / "Montserrat-VariableFont_wght.ttf"), 46)
try:
    font_main.set_variation_by_axes([800])
except Exception:
    pass

# ── Звёзды (детерминированные) ────────────────────────────────────────────────
_rng  = random.Random(77)
STARS = [(BTN_CX + _rng.randint(-190, 190),
          BTN_CY + _rng.randint(-90,   90),
          _rng.randint(1, 3)) for _ in range(14)]

# ── Позиции курсора ───────────────────────────────────────────────────────────
CUR_P0    = (W // 2 - 80, H + 100)
CUR_CTRL  = (CX + 50,     CY + 180)
CUR_P1    = (BTN_CX - 14, BTN_CY - 10)
CUR_PARK  = (BADGE_CX - 110, BADGE_CY + 70)   # рядом с бейджем, чуть левее-ниже
CUR_BADGE = (BADGE_CX - 14,  BADGE_CY - 12)

# ── Easing ────────────────────────────────────────────────────────────────────
def ease_out(t): return 1 - (1 - max(0.0, min(1.0, t))) ** 3
def ease_in(t):  return max(0.0, min(1.0, t)) ** 3
def ease_io(t):
    t = max(0.0, min(1.0, t))
    return t * t * (3 - 2 * t)
def ease_sine(t):
    """Плавный синусоидальный ease-in-out."""
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

# ── Комментарий (speech bubble) ───────────────────────────────────────────────
def draw_comment(draw, cx, cy, size, color):
    z   = size
    hw  = z * 0.44
    hh  = z * 0.28
    br  = max(2, int(z * 0.18))
    top = cy - hh - z * 0.12
    bot = cy + hh - z * 0.12
    draw.rounded_rectangle([cx-hw, top, cx+hw, bot], radius=br, fill=color)
    draw.polygon([
        (cx - hw*0.20, bot),
        (cx - hw*0.55, bot),
        (cx - hw*0.75, bot + z*0.30),
    ], fill=color)

# ── Планета: космические эффекты ──────────────────────────────────────────────
def draw_planet_fx(draw, morph_t, vis, breathe, rx0, ry0, rx1, ry1, rr):
    """
    morph_t  0..1 — прогресс морфа (0 = ещё планета)
    vis      0..1 — общая прозрачность (fade-in при появлении)
    breathe  0..1 — пульс "дыхания" планеты до морфа
    """
    if vis <= 0.01: return
    m = max(0.0, min(1.0, morph_t))

    star_a = max(0.0, 1.0 - m / 0.55) * vis
    ring_a = max(0.0, 1.0 - m / 0.30) * vis
    glow_a = max(0.0, 1.0 - m / 0.42) * vis * (0.75 + 0.25 * breathe)  # пульс

    if star_a > 0.01:
        for sx, sy, sr in STARS:
            draw.ellipse([sx-sr,sy-sr,sx+sr,sy+sr],
                         fill=(255, 255, 255, int(255 * star_a)))

    if glow_a > 0.01:
        ext = max(1, int(22 * (1 - m)))
        for g in range(ext, 0, -1):
            ga = int(22 * glow_a * g / ext)
            draw.rounded_rectangle(
                [rx0-g, ry0-g, rx1+g, ry1+g],
                radius=min(rr+g, int((ry1-ry0)/2)+g),
                fill=(*PLANET_GLOW, ga),
            )

    if ring_a > 0.01:
        ra  = int(230 * ring_a)
        mcx = int((rx0+rx1)/2); mcy = int((ry0+ry1)/2)
        rrx = int((rx1-rx0)*0.72+12); rry = int((ry1-ry0)*0.22)
        draw.arc([mcx-rrx,mcy-rry,mcx+rrx,mcy+rry],
                 start=195, end=345, fill=(*PLANET_RING, ra), width=3)
        draw.arc([mcx-rrx,mcy-rry,mcx+rrx,mcy+rry],
                 start=15,  end=165, fill=(*PLANET_RING, int(ra*.28)), width=2)

# ── Центровка текста ──────────────────────────────────────────────────────────
def draw_centered_text(draw, text, font, x_left, x_right, y_center, color):
    bb = font.getbbox(text); tw, th = bb[2]-bb[0], bb[3]-bb[1]
    tx = int(x_left + (x_right-x_left-tw) // 2)
    ty = int(y_center - th//2 - bb[1])
    draw.text((tx, ty), text, fill=color, font=font, stroke_width=0)

# ── Кадр ──────────────────────────────────────────────────────────────────────
def make_frame(idx: int) -> Image.Image:
    t = idx / FPS

    # ── Морф ─────────────────────────────────────────────────────────────────
    if t < T_PLANET_END:
        morph_t    = 0.0
        planet_vis = min(1.0, t / 0.20)
        # Плавное "дыхание" планеты (0.7 Hz, синус)
        breathe    = 0.5 + 0.5 * math.sin(2 * math.pi * 0.7 * t)
    elif t < T_MORPH_END:
        raw_t      = (t - T_PLANET_END) / (T_MORPH_END - T_PLANET_END)
        morph_t    = ease_sine(raw_t)   # плавный синусоидальный ease
        planet_vis = 1.0
        breathe    = 0.5
    else:
        morph_t    = 1.0
        planet_vis = 0.0
        breathe    = 0.0

    # ── Геометрия формы ───────────────────────────────────────────────────────
    # Ширина: ease_in → долго остаётся кругом, потом резко расширяется
    shape_w_t = ease_in(morph_t)
    m_w  = CH + (CW - CH) * shape_w_t     # 90 → 500 (с ease_in)
    m_r  = (CH / 2) * (1 - morph_t) + RADIUS * morph_t  # 45 → 24 (линейно)
    rx0  = BTN_CX - m_w / 2;  ry0 = BTN_CY - CH / 2
    rx1  = BTN_CX + m_w / 2;  ry1 = BTN_CY + CH / 2
    rr   = int(m_r)

    # ── Слайд/выход плашки ───────────────────────────────────────────────────
    if t < T_MORPH_END:
        slide_x = 0
        plk_a   = planet_vis
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
        btn_col = lerp_col(p_c, RED, NEAR_BLACK)
        v       = int(18 + (255 - 18) * p_c)
        txt_col = (v, v, v)
    elif t > T_CLICK_END:
        btn_col = NEAR_BLACK
        txt_col = WHITE
    else:
        # Чёрная планета медленно загорается красным.
        # morph_t**2.5: долго остаётся почти чёрной, bloom ближе к концу морфа.
        color_frac = morph_t ** 2.5
        btn_col    = lerp_col(color_frac, PLANET_COL, RED)
        txt_col    = BLACK_TXT

    clicked = t > T_CLICK_END
    label   = "Abonné" if clicked else "S'abonner"

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

    # ── Comment badge ─────────────────────────────────────────────────────────
    if t < T_BELL_EXIT:
        like_vis = 0.0
    elif t < T_SWAP_END:
        like_vis = ease_io((t - T_BELL_EXIT) / (T_SWAP_END - T_BELL_EXIT))
    elif t < T_LIKE_GONE:
        like_vis = 1.0
    else:
        like_vis = 0.0

    like_badge_scale = 1.0
    if T_LIKE_ANIM <= t < T_LIKE_GONE:
        like_badge_scale = 1.0 - ease_in((t - T_LIKE_ANIM) / (T_LIKE_GONE - T_LIKE_ANIM))
    elif t >= T_LIKE_GONE:
        like_badge_scale = 0.0

    like_xoff = 0.0
    if T_BELL_EXIT <= t < T_SWAP_END:
        like_xoff = 52 * (1 - ease_io((t - T_BELL_EXIT) / (T_SWAP_END - T_BELL_EXIT)))

    like_bounce_y = 0.0
    if t >= T_LIKE_CLICK:
        st = t - T_LIKE_CLICK
        like_bounce_y = math.sin(2*math.pi*4.5*st) * 10 * math.exp(-st*2.6)

    like_icon_scale = 1.0
    if T_LIKE_ARR <= t <= T_LIKE_CLICK:
        p_c = (t - T_LIKE_ARR) / (T_LIKE_CLICK - T_LIKE_ARR)
        like_icon_scale = 1.0 - 0.35 * math.sin(math.pi * p_c)

    # ── Рисуем ────────────────────────────────────────────────────────────────
    img  = Image.new("RGBA", (W, H), (0, 0, 0, 0))
    draw = ImageDraw.Draw(img)

    bx   = CX + slide_x
    by   = CY
    rx0s = rx0 + slide_x
    rx1s = rx1 + slide_x

    # Космические эффекты (только пока есть планета)
    if planet_vis > 0.01:
        draw_planet_fx(draw, morph_t, planet_vis, breathe, rx0s, ry0, rx1s, ry1, rr)

    # Основная форма
    a_bg  = int(255 * plk_a)
    a_txt = int(255 * plk_a)
    brd   = tuple(max(0, c-40) for c in btn_col)
    draw.rounded_rectangle(
        [rx0s, ry0, rx1s, ry1], radius=rr,
        fill=(*btn_col, a_bg),
        outline=(*brd, int(180 * plk_a)), width=2,
    )

    # Текст появляется в последние 22% морфа (когда форма уже заметно красная)
    if morph_t >= 1.0:
        draw_centered_text(draw, label, font_main,
                           bx+16, bx+CW-16, by+CH//2, (*txt_col, a_txt))
    elif morph_t > 0.78:
        tf = (morph_t - 0.78) / 0.22
        draw_centered_text(draw, label, font_main,
                           int(rx0s)+16, int(rx1s)-16, (ry0+ry1)/2,
                           (*txt_col, int(255*tf*plk_a)))

    # ── Bell badge ────────────────────────────────────────────────────────────
    if bell_vis > 0.01:
        bcx  = BADGE_CX + bell_xoff
        bcy  = float(BADGE_CY)
        bs   = BADGE_SIZE
        brd2 = tuple(max(0, c-10) for c in NEAR_BLACK)
        draw.rounded_rectangle(
            [bcx-bs//2, bcy-bs//2, bcx+bs//2, bcy+bs//2], radius=18,
            fill=(*NEAR_BLACK, int(255*bell_vis)),
            outline=(*brd2, int(150*bell_vis)), width=2,
        )
        draw_bell(draw, bcx, bcy+2, bs*0.38, swing, (*ICON_COL, int(255*bell_vis)))

    # ── Comment badge ─────────────────────────────────────────────────────────
    if like_vis > 0.01 and like_badge_scale > 0.005:
        lcx  = BADGE_CX + like_xoff
        lcy  = BADGE_CY + like_bounce_y
        bs   = int(BADGE_SIZE * like_badge_scale)
        br   = max(2, int(18 * like_badge_scale))
        if bs >= 4:
            brd2 = tuple(max(0, c-10) for c in NEAR_BLACK)
            draw.rounded_rectangle(
                [lcx-bs//2, lcy-bs//2, lcx+bs//2, lcy+bs//2], radius=br,
                fill=(*NEAR_BLACK, int(255*like_vis)),
                outline=(*brd2, int(150*like_vis)), width=2,
            )
            draw_comment(draw, lcx, lcy,
                         bs * 0.38 * like_icon_scale,
                         (*ICON_COL, int(255*like_vis)))

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
        elif t <= T_LIKE_ARR:
            p_m = ease_io((t - T_SWAP_END) / (T_LIKE_ARR - T_SWAP_END))
            cx_pos, cy_pos = lerp2(p_m, CUR_PARK, CUR_BADGE)
            c_scale = 1.0
        elif t <= T_LIKE_CLICK:
            p_c = (t - T_LIKE_ARR) / (T_LIKE_CLICK - T_LIKE_ARR)
            cx_pos, cy_pos = CUR_BADGE
            c_scale = 1.0 - 0.35 * math.sin(math.pi * p_c)
        elif t <= T_CUR_EXIT:
            p_m = ease_io((t - T_LIKE_CLICK) / (T_CUR_EXIT - T_LIKE_CLICK))
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
    if i % 30 == 0:
        print(f"  {i}/{NFRM}  t={i/FPS:.1f}s")
    proc.stdin.write(make_frame(i).tobytes())
proc.stdin.close()
proc.wait()
print(f"Done: {OUT}  rc={proc.returncode}")
