"""
html_thumbnail.py — генерация YouTube превью через HTML/CSS + Playwright

Флоу:
  base64_image (PixelAgent) + overlay_text
    → генерируем HTML с CSS стилями
    → Playwright делает скриншот 1920×1080
    → сохраняем thumbnail.png

Результат: профессиональное превью с точным позиционированием,
красивой типографикой, градиентами — всё через CSS.
"""

from __future__ import annotations

import base64
import re
import textwrap
from pathlib import Path


# ─── HTML шаблон ──────────────────────────────────────────────────────────────

HTML_TEMPLATE = """\
<!DOCTYPE html>
<html>
<head>
<meta charset="UTF-8">
<style>
  @import url('https://fonts.googleapis.com/css2?family=Oswald:wght@700&family=Bebas+Neue&display=swap');

  * {{ margin: 0; padding: 0; box-sizing: border-box; }}

  body {{
    width: 1920px;
    height: 1080px;
    overflow: hidden;
    background: #000;
    font-family: 'Oswald', sans-serif;
  }}

  .bg {{
    position: absolute;
    inset: 0;
    background-image: url('{bg_data_uri}');
    background-size: cover;
    background-position: center top;
  }}

  /* Градиент снизу — делает текст читаемым */
  .gradient {{
    position: absolute;
    inset: 0;
    background: linear-gradient(
      to bottom,
      transparent 30%,
      rgba(0,0,0,0.18) 55%,
      rgba(0,0,0,0.62) 80%,
      rgba(0,0,0,0.82) 100%
    );
  }}

  /* Боковой градиент — усиливает правую зону под текст если right_col */
  .side-gradient {{
    position: absolute;
    inset: 0;
    background: {side_gradient};
  }}

  .text-block {{
    position: absolute;
    {text_position}
    display: flex;
    flex-direction: column;
    justify-content: center;
    gap: 8px;
  }}

  .line {{
    display: block;
    font-family: 'Oswald', 'Arial Black', sans-serif;
    font-weight: 700;
    font-style: normal;
    text-transform: uppercase;
    color: #FFFFFF;
    line-height: 1.0;
    letter-spacing: 0.03em;
    text-shadow:
      0px 4px 24px rgba(0,0,0,0.95),
      0px 2px 8px rgba(0,0,0,0.9),
      3px 3px 0px rgba(0,0,0,0.5);
    white-space: nowrap;
  }}

  .line.accent {{
    color: #FFD84D;
  }}

  /* Vignette по краям */
  .vignette {{
    position: absolute;
    inset: 0;
    box-shadow: inset 0 0 180px rgba(0,0,0,0.35);
    pointer-events: none;
  }}
</style>
</head>
<body>
  <div class="bg"></div>
  <div class="gradient"></div>
  <div class="side-gradient"></div>
  <div class="text-block">
    {lines_html}
  </div>
  <div class="vignette"></div>
</body>
</html>
"""


def _split_lines(text: str) -> list[str]:
    """Split text into up to 3 balanced lines."""
    text = text.strip().upper()
    words = text.split()
    n = len(words)

    if n <= 2:
        return [text]

    # Natural break on dash/punct
    for i, w in enumerate(words[1:], 1):
        if w in ("—", "-", "–") or (w.endswith("?") or w.endswith("!")):
            a = " ".join(words[:i + 1])
            b = " ".join(words[i + 1:])
            if b:
                return [a, b]

    # Balanced 2-way split
    mid = n // 2
    return [" ".join(words[:mid]), " ".join(words[mid:])]


def _font_size_for_layout(layout: str, n_lines: int, max_chars: int) -> int:
    """Compute font size based on layout and content."""
    if layout in ("right_col", "left_col"):
        base = {1: 165, 2: 140, 3: 110}[min(n_lines, 3)]
        # Reduce if long words
        if max_chars > 12:
            base = int(base * 0.82)
        elif max_chars > 9:
            base = int(base * 0.91)
    else:
        # Standard: full-width text
        base = {1: 180, 2: 155, 3: 120}[min(n_lines, 3)]
        if max_chars > 14:
            base = int(base * 0.80)
        elif max_chars > 10:
            base = int(base * 0.90)
    return base


def build_html(
    img_bytes: bytes,
    overlay_text: str,
    layout: str = "standard",
) -> str:
    """
    Build HTML string for a 1920×1080 YouTube thumbnail.

    img_bytes:    raw PNG/JPEG/WebP bytes from PixelAgent
    overlay_text: bold uppercase text (e.g. "TE EXTRAÑO")
    layout:       "standard" | "right_col" | "left_col" | "split"
    """
    # Encode image as data URI (no external requests needed)
    b64 = base64.b64encode(img_bytes).decode()
    # Detect format
    if img_bytes[:4] == b'\x89PNG':
        mime = "image/png"
    elif img_bytes[:2] == b'\xff\xd8':
        mime = "image/jpeg"
    else:
        mime = "image/webp"
    bg_data_uri = f"data:{mime};base64,{b64}"

    lines = _split_lines(overlay_text)
    n_lines = len(lines)
    max_chars = max(len(l) for l in lines)
    font_size = _font_size_for_layout(layout, n_lines, max_chars)

    # ── Layout geometry ───────────────────────────────────────────────────────
    if layout == "right_col":
        # Text in right 44%, centered vertically
        text_position = (
            "right: 48px; top: 0; bottom: 0; "
            f"width: 840px; "
            "padding: 0 32px;"
        )
        side_gradient = (
            "linear-gradient(to left, "
            "rgba(0,0,0,0.45) 0%, "
            "rgba(0,0,0,0.20) 40%, "
            "transparent 65%)"
        )

    elif layout == "left_col":
        text_position = (
            "left: 48px; top: 0; bottom: 0; "
            f"width: 840px; "
            "padding: 0 32px;"
        )
        side_gradient = (
            "linear-gradient(to right, "
            "rgba(0,0,0,0.45) 0%, "
            "rgba(0,0,0,0.20) 40%, "
            "transparent 65%)"
        )

    elif layout == "split":
        # Full-width, centered, lower half
        text_position = (
            "left: 80px; right: 80px; "
            "bottom: 80px; "
        )
        side_gradient = "none"

    else:  # standard
        # Full-width, bottom area
        text_position = (
            "left: 80px; right: 80px; "
            "bottom: 72px; "
        )
        side_gradient = "none"

    # ── Build lines HTML ──────────────────────────────────────────────────────
    accent_words = {"LÁGRIMA", "LÁGRIMAS", "EXTRAÑO", "ORGULLOSO", "NUNCA",
                    "SIEMPRE", "AQUÍ", "URGENTE", "CASUALIDAD", "TODO", "TRAJE"}

    lines_html_parts = []
    for i, line in enumerate(lines):
        words = line.split()
        # First line gets accent color on last word if it's in accent set
        use_accent = (i == 0 and len(lines) > 1 and
                      words[-1].rstrip(".,!?—") in accent_words)
        cls = "line accent" if use_accent else "line"
        # Scale: first line big, rest slightly smaller
        scale = 1.0 if i == 0 else 0.72
        size = int(font_size * scale)
        lines_html_parts.append(
            f'<span class="{cls}" style="font-size:{size}px">{line}</span>'
        )

    lines_html = "\n    ".join(lines_html_parts)

    return HTML_TEMPLATE.format(
        bg_data_uri=bg_data_uri,
        side_gradient=side_gradient,
        text_position=text_position,
        lines_html=lines_html,
    )


def render_thumbnail(
    img_bytes: bytes,
    overlay_text: str,
    out_path: Path,
    layout: str = "standard",
) -> bool:
    """
    Render thumbnail via Playwright and save as PNG.

    Returns True on success.
    """
    from playwright.sync_api import sync_playwright

    html = build_html(img_bytes, overlay_text, layout)

    try:
        with sync_playwright() as p:
            # Try headless shell first, fall back to full chromium
            import os
            headless_exe = (
                Path(os.environ.get("LOCALAPPDATA", ""))
                / "ms-playwright/chromium_headless_shell-1208"
                / "chrome-headless-shell-win64/chrome-headless-shell.exe"
            )
            chromium_exe = (
                Path(os.environ.get("LOCALAPPDATA", ""))
                / "ms-playwright/chromium-1208/chrome-win/chrome.exe"
            )
            if headless_exe.exists():
                browser = p.chromium.launch(
                    headless=True,
                    executable_path=str(headless_exe),
                )
            elif chromium_exe.exists():
                browser = p.chromium.launch(
                    headless=True,
                    executable_path=str(chromium_exe),
                )
            else:
                browser = p.chromium.launch(headless=True)
            page = browser.new_page(viewport={"width": 1920, "height": 1080})

            # Use data URI to avoid any network loading issues with local files
            page.set_content(html, wait_until="networkidle")

            # Wait for Google Fonts to load (if online) or timeout gracefully
            try:
                page.wait_for_timeout(1200)
            except Exception:
                pass

            page.screenshot(path=str(out_path), type="png", clip={
                "x": 0, "y": 0, "width": 1920, "height": 1080
            })
            browser.close()

        size_kb = out_path.stat().st_size // 1024
        print(f"  [HTML] Saved {out_path.name} ({size_kb}KB)", flush=True)
        return True

    except Exception as e:
        print(f"  [HTML] Render failed: {e}", flush=True)
        return False
