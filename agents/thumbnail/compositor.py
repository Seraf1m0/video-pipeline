"""
compositor.py — Pillow-based thumbnail compositor.

Накладывает текст, стрелки, круги выделения и glow эффекты
на base image от PixelAgent.

Fonts: Druk Cyr Heavy (основной заголовок), Benzin ExtraBold (подзаголовок)
"""

import math
from pathlib import Path
from typing import Literal

from PIL import Image, ImageDraw, ImageFilter, ImageFont

# ── Font paths ────────────────────────────────────────────────────────────────

_USER_FONTS = Path("C:/Users/Serafim/AppData/Local/Microsoft/Windows/Fonts")
_SYS_FONTS  = Path("C:/Windows/Fonts")

FONT_TITLE    = _USER_FONTS / "DrukCyr-Heavy.ttf"          # главный заголовок
FONT_SUBTITLE = _USER_FONTS / "Benzin-ExtraBold.ttf"        # подзаголовок
FONT_FALLBACK = _SYS_FONTS  / "arialbd.ttf"                 # fallback

# ── Canvas size ───────────────────────────────────────────────────────────────

THUMB_W = 1280
THUMB_H = 720


# ── Helpers ───────────────────────────────────────────────────────────────────

def _load_font(path: Path, size: int) -> ImageFont.FreeTypeFont:
    if path.exists():
        return ImageFont.truetype(str(path), size)
    return ImageFont.truetype(str(FONT_FALLBACK), size)


def _text_size(draw: ImageDraw.ImageDraw, text: str, font) -> tuple[int, int]:
    bbox = draw.textbbox((0, 0), text, font=font)
    return bbox[2] - bbox[0], bbox[3] - bbox[1]


def _ensure_rgba(img: Image.Image) -> Image.Image:
    return img.convert("RGBA") if img.mode != "RGBA" else img


# ── Glow effect ───────────────────────────────────────────────────────────────

def _make_glow_layer(
    size: tuple[int, int],
    text: str,
    pos: tuple[int, int],
    font,
    color: tuple[int, int, int],
    radius: int = 18,
    strength: int = 4,
) -> Image.Image:
    """Создаёт размытый слой с текстом — имитация glow вокруг букв."""
    glow = Image.new("RGBA", size, (0, 0, 0, 0))
    gd   = ImageDraw.Draw(glow)
    for _ in range(strength):
        gd.text(pos, text, font=font, fill=(*color, 200))
    return glow.filter(ImageFilter.GaussianBlur(radius))


# ── Text with outline + glow ──────────────────────────────────────────────────

def draw_text_shadow(
    canvas: Image.Image,
    text: str,
    pos: tuple[int, int],
    font,
    fill: tuple[int, int, int] = (255, 255, 255),
    shadow_offset: tuple[int, int] = (4, 4),
    shadow_blur: int = 12,
    shadow_alpha: int = 200,
    glow_color: tuple[int, int, int] | None = None,
    glow_radius: int = 20,
) -> Image.Image:
    """
    Рисует текст с мягкой тенью (без outline — outline дешевит).
    pos = верхний левый угол текста.
    """
    canvas = _ensure_rgba(canvas)

    # Glow слой (опционально)
    if glow_color:
        glow = _make_glow_layer(canvas.size, text, pos, font, glow_color, glow_radius)
        canvas = Image.alpha_composite(canvas, glow)

    # Shadow слой — размытый чёрный текст со смещением
    shadow_layer = Image.new("RGBA", canvas.size, (0, 0, 0, 0))
    sd = ImageDraw.Draw(shadow_layer)
    sx, sy = pos[0] + shadow_offset[0], pos[1] + shadow_offset[1]
    sd.text((sx, sy), text, font=font, fill=(0, 0, 0, shadow_alpha))
    shadow_layer = shadow_layer.filter(ImageFilter.GaussianBlur(shadow_blur))
    canvas = Image.alpha_composite(canvas, shadow_layer)

    # Основной текст
    draw = ImageDraw.Draw(canvas)
    draw.text(pos, text, font=font, fill=(*fill, 255))
    return canvas


# ── Arrow ─────────────────────────────────────────────────────────────────────

def draw_arrow(
    canvas: Image.Image,
    start: tuple[int, int],
    end: tuple[int, int],
    color: tuple[int, int, int] = (255, 50, 50),
    width: int = 6,
    head_size: int = 24,
) -> Image.Image:
    """Рисует стрелку от start к end."""
    canvas = _ensure_rgba(canvas)
    draw   = ImageDraw.Draw(canvas)

    # Линия
    draw.line([start, end], fill=(*color, 255), width=width)

    # Наконечник (угол 25° от направления стрелки)
    angle = math.atan2(end[1] - start[1], end[0] - start[0])
    for da in (0.45, -0.45):
        ax = end[0] - head_size * math.cos(angle + da)
        ay = end[1] - head_size * math.sin(angle + da)
        draw.line([end, (int(ax), int(ay))], fill=(*color, 255), width=width)

    return canvas


# ── Circle highlight ──────────────────────────────────────────────────────────

def draw_circle_highlight(
    canvas: Image.Image,
    center: tuple[int, int],
    radius: int = 80,
    color: tuple[int, int, int] = (255, 220, 0),
    width: int = 5,
    glow: bool = True,
) -> Image.Image:
    """Рисует круг/эллипс выделения вокруг объекта."""
    canvas = _ensure_rgba(canvas)

    if glow:
        glow_layer = Image.new("RGBA", canvas.size, (0, 0, 0, 0))
        gd = ImageDraw.Draw(glow_layer)
        cx, cy = center
        for r_off in range(0, 12, 2):
            gd.ellipse(
                [cx - radius - r_off, cy - radius - r_off,
                 cx + radius + r_off, cy + radius + r_off],
                outline=(*color, 60), width=width + 2
            )
        glow_layer = glow_layer.filter(ImageFilter.GaussianBlur(8))
        canvas = Image.alpha_composite(canvas, glow_layer)

    draw = ImageDraw.Draw(canvas)
    cx, cy = center
    draw.ellipse(
        [cx - radius, cy - radius, cx + radius, cy + radius],
        outline=(*color, 230), width=width
    )
    return canvas


# ── Vignette ─────────────────────────────────────────────────────────────────

def apply_vignette(
    canvas: Image.Image,
    strength: float = 0.6,
) -> Image.Image:
    """Тёмная виньетка по краям через радиальный градиент (numpy)."""
    import numpy as np
    canvas = _ensure_rgba(canvas)
    w, h   = canvas.size

    # Нормализованная сетка координат [-1, 1]
    xs = (np.linspace(0, w - 1, w) - w / 2) / (w / 2)
    ys = (np.linspace(0, h - 1, h) - h / 2) / (h / 2)
    xx, yy = np.meshgrid(xs, ys)

    # Радиус с коррекцией на соотношение сторон
    radius = np.sqrt(xx ** 2 + (yy * w / h) ** 2)

    # Плавный dark falloff: 0 в центре → strength по краям
    vig = np.clip((radius - 0.3) / 0.7, 0, 1) ** 2 * strength
    alpha = (vig * 255).astype(np.uint8)

    # Создаём чёрный слой с этой alpha-маской
    vignette_arr = np.zeros((h, w, 4), dtype=np.uint8)
    vignette_arr[:, :, 3] = alpha  # только alpha, RGB = 0 (чёрный)

    vignette = Image.fromarray(vignette_arr, "RGBA")
    return Image.alpha_composite(canvas, vignette)


# ── Layout presets ────────────────────────────────────────────────────────────

def compose_thumbnail(
    base_image_path: str | Path,
    title: str,
    keyword: str | None = None,        # ключевое слово — будет крупнее/другого цвета
    subtitle: str | None = None,
    title_pos: Literal["top", "bottom", "top-left", "bottom-left", "center"] = "bottom",
    title_size: int = 100,
    keyword_size: int | None = None,    # если None — title_size * 1.4
    subtitle_size: int = 48,
    title_color: tuple[int, int, int] = (255, 255, 255),
    keyword_color: tuple[int, int, int] = (255, 255, 255),
    title_glow: tuple[int, int, int] | None = None,
    arrows: list[dict] | None = None,
    circles: list[dict] | None = None,
    vignette: bool = True,
    vignette_strength: float = 0.45,
    out_path: str | Path | None = None,
) -> Image.Image:
    """
    Главная функция композитинга.

    title:   основной текст (верхняя строка если есть keyword)
    keyword: ключевое слово — рисуется крупнее под title (как у конкурентов)
    subtitle: мелкий надпись (третья строка, редко)
    title_pos: позиция блока текста
    """
    img = Image.open(base_image_path).convert("RGBA")
    if img.size != (THUMB_W, THUMB_H):
        img = img.resize((THUMB_W, THUMB_H), Image.LANCZOS)

    if vignette:
        img = apply_vignette(img, vignette_strength)

    font_title    = _load_font(FONT_TITLE, title_size)
    kw_size       = keyword_size or int(title_size * 1.45)
    font_keyword  = _load_font(FONT_TITLE, kw_size)
    font_subtitle = _load_font(FONT_SUBTITLE, subtitle_size)

    draw = ImageDraw.Draw(img)
    pad  = 52

    # Вычислить высоту всего текстового блока
    tw, th     = _text_size(draw, title, font_title)
    kw_w, kw_h = _text_size(draw, keyword, font_keyword) if keyword else (0, 0)
    sw, sh     = _text_size(draw, subtitle, font_subtitle) if subtitle else (0, 0)
    gap        = 8
    block_h    = th + (kw_h + gap if keyword else 0) + (sh + gap if subtitle else 0)
    block_w    = max(tw, kw_w, sw)

    # Позиция блока
    if title_pos == "bottom":
        bx = THUMB_W // 2 - block_w // 2
        by = THUMB_H - pad - block_h
    elif title_pos == "bottom-left":
        bx = pad
        by = THUMB_H - pad - block_h
    elif title_pos == "top":
        bx = THUMB_W // 2 - block_w // 2
        by = pad
    elif title_pos == "top-left":
        bx = pad
        by = pad
    elif title_pos == "center":
        bx = THUMB_W // 2 - block_w // 2
        by = THUMB_H // 2 - block_h // 2
    else:
        bx, by = pad, pad

    # Рисуем title (центрируем каждую строку по block_w)
    tx = bx + (block_w - tw) // 2
    img = draw_text_shadow(img, title, (tx, by), font_title,
                           fill=title_color, shadow_offset=(3, 5),
                           shadow_blur=14, shadow_alpha=220,
                           glow_color=title_glow, glow_radius=18)
    cur_y = by + th + gap

    # Рисуем keyword (крупнее)
    if keyword:
        kx = bx + (block_w - kw_w) // 2
        img = draw_text_shadow(img, keyword, (kx, cur_y), font_keyword,
                               fill=keyword_color, shadow_offset=(4, 6),
                               shadow_blur=18, shadow_alpha=230,
                               glow_color=title_glow, glow_radius=22)
        cur_y += kw_h + gap

    # Рисуем subtitle
    if subtitle:
        sux = bx + (block_w - sw) // 2
        img = draw_text_shadow(img, subtitle, (sux, cur_y), font_subtitle,
                               fill=(210, 210, 210), shadow_offset=(2, 3),
                               shadow_blur=10, shadow_alpha=180)

    # Стрелки
    for arrow in (arrows or []):
        img = draw_arrow(img,
                         start=arrow["start"], end=arrow["end"],
                         color=arrow.get("color", (220, 40, 40)),
                         width=arrow.get("width", 6),
                         head_size=arrow.get("head_size", 26))

    # Круги
    for circle in (circles or []):
        img = draw_circle_highlight(img,
                                    center=circle["center"],
                                    radius=circle.get("radius", 80),
                                    color=circle.get("color", (255, 220, 0)),
                                    width=circle.get("width", 5),
                                    glow=circle.get("glow", True))

    if out_path:
        out = Path(out_path)
        out.parent.mkdir(parents=True, exist_ok=True)
        img.convert("RGB").save(str(out), "PNG", quality=95)

    return img
