"""
meta_generator.py — YouTube meta generation agent for Video Pipeline.

Generates titles, descriptions, tags, thumbnail images and overlays
for a given session using Claude Opus + PixelAgent API.

Usage:
    python agents/meta_agent/meta_generator.py \
        --channel channel_003_religion_es \
        --session Video_20260404_174933

Output (in {session_dir}/meta/):
    thumbnail_1.png, thumbnail_2.png, thumbnail_3.png
    titles.txt
    descriptions.txt
    tags.txt
    meta_raw.json
"""

import argparse
import base64
import io
import json
import os
import subprocess
import sys
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

# ── stdout UTF-8 ──────────────────────────────────────────────────────────────
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

# ── Ensure project root is on sys.path ───────────────────────────────────────
_PROJECT_ROOT = Path(__file__).parent.parent.parent
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))

# ── Import paths from agents/utils/paths.py ───────────────────────────────────
_UTILS_DIR = Path(__file__).parent.parent / "utils"
if str(_UTILS_DIR) not in sys.path:
    sys.path.insert(0, str(_UTILS_DIR))

from paths import get_session_dir, get_transcripts_dir  # noqa: E402

# ── PIL (Pillow) ──────────────────────────────────────────────────────────────
try:
    from PIL import Image, ImageDraw, ImageFont, ImageFilter
    _PIL_AVAILABLE = True
except ImportError:
    _PIL_AVAILABLE = False
    print("[META] WARNING: Pillow not installed — thumbnail composition will be skipped", flush=True)

# ── ReportLab PDF ────────────────────────────────────────────────────────────
try:
    from reportlab.lib import colors
    from reportlab.lib.pagesizes import A4
    from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
    from reportlab.lib.units import cm
    from reportlab.platypus import (
        SimpleDocTemplate, Paragraph, Spacer, Table, TableStyle, HRFlowable
    )
    from reportlab.pdfbase import pdfmetrics
    from reportlab.pdfbase.ttfonts import TTFont
    _REPORTLAB_AVAILABLE = True
except ImportError:
    _REPORTLAB_AVAILABLE = False

# ── requests for PixelAgent API ───────────────────────────────────────────────
try:
    import requests as _requests
    _REQUESTS_AVAILABLE = True
except ImportError:
    _REQUESTS_AVAILABLE = False
    print("[META] WARNING: requests not installed — image generation will be skipped", flush=True)

# ── Gemini Flash (image prompt generation with baked-in text) ─────────────────
_FLASH_MODEL    = "gemini-2.5-flash"
_NO_THINKING    = {"thinking_config": {"thinking_budget": 0}}
_gemini_client  = None

def _get_gemini_client():
    global _gemini_client
    if _gemini_client is None:
        from google import genai
        env = load_env()
        api_key = env.get("GEMINI_API_KEY") or os.environ.get("GEMINI_API_KEY", "")
        if not api_key:
            raise RuntimeError("GEMINI_API_KEY not found in config/.env")
        _gemini_client = genai.Client(api_key=api_key)
    return _gemini_client


def _flash(prompt: str, max_tokens: int = 2000) -> str:
    from google.genai import types as gtypes
    parts = [gtypes.Part.from_text(text=prompt)]
    for attempt in range(5):
        try:
            resp = _get_gemini_client().models.generate_content(
                model=_FLASH_MODEL,
                contents=parts,
                config={"temperature": 0.7, "max_output_tokens": max_tokens, **_NO_THINKING},
            )
            return resp.text.strip()
        except Exception as e:
            if "503" in str(e) or "UNAVAILABLE" in str(e):
                wait = 15 * (attempt + 1)
                log(f"[Flash] 503 overloaded, retry {attempt+1}/5 in {wait}s...")
                time.sleep(wait)
            else:
                raise
    raise RuntimeError("Flash API unavailable after 5 retries")

# ── Claude CLI setup (same pattern as visual_query_generator.py) ──────────────
_CLAUDE_DIR = Path(os.environ.get("APPDATA", "")) / "Claude/claude-code"
if _CLAUDE_DIR.exists():
    for _v in sorted(_CLAUDE_DIR.iterdir(), reverse=True):
        _exe = _v / "claude.exe"
        if _exe.exists():
            os.environ["PATH"] = str(_v) + os.pathsep + os.environ.get("PATH", "")
            break


# ── .env loader ───────────────────────────────────────────────────────────────

def load_env() -> dict:
    env_path = _PROJECT_ROOT / "config" / ".env"
    result = {}
    if env_path.exists():
        for line in env_path.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if line and not line.startswith("#") and "=" in line:
                k, v = line.split("=", 1)
                result[k.strip()] = v.strip()
    return result


# ── Logging helper ────────────────────────────────────────────────────────────

def log(msg: str) -> None:
    print(f"[META] {msg}", flush=True)


# ── PDF generation ────────────────────────────────────────────────────────────

def _register_pdf_fonts() -> str:
    """Register a Unicode-capable font for PDF. Returns font name."""
    if not _REPORTLAB_AVAILABLE:
        return "Helvetica"
    candidates = [
        str(_PROJECT_ROOT / "config" / "fonts" / "Organetto-Regular.ttf"),
        str(_PROJECT_ROOT / "config" / "fonts" / "Benzin-Regular.ttf"),
        "C:/Windows/Fonts/arial.ttf",
        "C:/Windows/Fonts/calibri.ttf",
        "C:/Windows/Fonts/verdana.ttf",
    ]
    for path in candidates:
        if Path(path).exists():
            try:
                name = Path(path).stem.replace("-", "").replace("_", "")
                pdfmetrics.registerFont(TTFont(name, path))
                # Also try bold variant
                bold_path = path.replace("Regular", "Bold").replace("regular", "bold")
                bold_name = name + "Bold"
                if Path(bold_path).exists():
                    pdfmetrics.registerFont(TTFont(bold_name, bold_path))
                return name
            except Exception:
                continue
    return "Helvetica"


def save_meta_pdf(
    output_path: Path,
    title_pairs: list[dict],
    titles_analysis: str,
    desc_pairs: list[dict],
    preview_texts_raw: str,
    top5_previews: list[str],
    tags_with: str,
    tags_without: str,
    ctr_analysis: str,
    channel: str,
    session: str,
) -> bool:
    """
    Generates a formatted PDF with all meta outputs.
    Returns True on success.
    """
    if not _REPORTLAB_AVAILABLE:
        log("WARNING: reportlab not available — skipping PDF generation")
        return False

    try:
        font_name = _register_pdf_fonts()
        bold_name = font_name + "Bold" if (font_name + "Bold") in pdfmetrics.getRegisteredFontNames() else "Helvetica-Bold"

        doc = SimpleDocTemplate(
            str(output_path),
            pagesize=A4,
            leftMargin=1.8*cm, rightMargin=1.8*cm,
            topMargin=2*cm, bottomMargin=2*cm,
        )

        # Styles
        W = A4[0] - 3.6*cm
        GOLD  = colors.HexColor("#C8A733")
        DARK  = colors.HexColor("#1A1A2E")
        MID   = colors.HexColor("#444466")
        LIGHT = colors.HexColor("#F5F5FF")

        def _p(text, size=10, bold=False, color=colors.black, leading=None):
            fn = bold_name if bold else font_name
            style = ParagraphStyle(
                "x", fontName=fn, fontSize=size,
                leading=leading or size * 1.4,
                textColor=color, wordWrap="CJK",
            )
            return Paragraph(text.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;"), style)

        def _hr():
            return HRFlowable(width="100%", thickness=0.5, color=colors.HexColor("#CCCCDD"), spaceAfter=6)

        def _section_header(text):
            return [
                Spacer(1, 10),
                Table([[_p(text, 12, bold=True, color=colors.white)]],
                      colWidths=[W],
                      style=TableStyle([
                          ("BACKGROUND", (0,0), (-1,-1), DARK),
                          ("TOPPADDING", (0,0), (-1,-1), 7),
                          ("BOTTOMPADDING", (0,0), (-1,-1), 7),
                          ("LEFTPADDING", (0,0), (-1,-1), 10),
                      ])),
                Spacer(1, 8),
            ]

        story = []

        # ── Header ──────────────────────────────────────────────────────────
        story.append(Table([[_p(f"YouTube Meta Report", 16, bold=True, color=DARK)]],
                           colWidths=[W],
                           style=TableStyle([
                               ("BACKGROUND", (0,0), (-1,-1), LIGHT),
                               ("TOPPADDING", (0,0), (-1,-1), 10),
                               ("BOTTOMPADDING", (0,0), (-1,-1), 10),
                               ("LEFTPADDING", (0,0), (-1,-1), 12),
                               ("LINEBELOW", (0,0), (-1,-1), 2, GOLD),
                           ])))
        story.append(_p(f"Канал: {channel}  |  Сессия: {session}", 9, color=MID))
        story.append(Spacer(1, 14))

        # ── Titles Top-3 ────────────────────────────────────────────────────
        story += _section_header("НАЗВАНИЯ — ТОП-3")
        for i, p in enumerate(title_pairs[:3], 1):
            label = "#1 ЛУЧШЕЕ" if i == 1 else f"#{i}"
            badge_color = GOLD if i == 1 else MID
            story.append(Table(
                [[_p(label, 9, bold=True, color=colors.white),
                  _p(p.get("es",""), 11, bold=(i==1), color=DARK)]],
                colWidths=[2.2*cm, W - 2.2*cm],
                style=TableStyle([
                    ("BACKGROUND", (0,0), (0,-1), badge_color),
                    ("BACKGROUND", (1,0), (1,-1), colors.HexColor("#FAFAFA") if i==1 else colors.white),
                    ("TOPPADDING", (0,0), (-1,-1), 6),
                    ("BOTTOMPADDING", (0,0), (-1,-1), 6),
                    ("LEFTPADDING", (0,0), (-1,-1), 8),
                    ("VALIGN", (0,0), (-1,-1), "MIDDLE"),
                    ("LINEBELOW", (0,0), (-1,-1), 0.3, colors.HexColor("#DDDDEE")),
                ])
            ))
            if p.get("ru"):
                story.append(_p(f"RU: {p['ru']}", 9, color=MID))
            story.append(Spacer(1, 4))

        # ── Titles Analysis Table ────────────────────────────────────────────
        if titles_analysis:
            story += _section_header("CTR-АНАЛИЗ ВСЕХ НАЗВАНИЙ")
            # Parse markdown table from analysis
            import re as _re
            table_lines = [l for l in titles_analysis.splitlines() if l.strip().startswith("|")]
            if len(table_lines) >= 3:
                def _parse_row(line):
                    return [c.strip() for c in line.strip().strip("|").split("|")]
                headers = _parse_row(table_lines[0])
                data_rows = [_parse_row(l) for l in table_lines[2:] if not all(c.strip("-:| ") == "" for c in _parse_row(l))]
                col_count = len(headers)
                col_w = W / col_count
                col_widths = [1.0*cm] + [2.5*cm] + [1.2*cm] * (col_count - 2)
                # Fix col widths to fit page
                col_widths = col_widths[:col_count]
                remaining = W - sum(col_widths)
                if remaining > 0 and len(col_widths) > 1:
                    col_widths[1] += remaining

                tbl_data = [[_p(h, 9, bold=True, color=colors.white) for h in headers]]
                for row in data_rows:
                    while len(row) < col_count:
                        row.append("")
                    tbl_data.append([_p(c, 9) for c in row[:col_count]])

                tbl = Table(tbl_data, colWidths=col_widths, repeatRows=1)
                tbl.setStyle(TableStyle([
                    ("BACKGROUND", (0,0), (-1,0), DARK),
                    ("ROWBACKGROUNDS", (0,1), (-1,-1), [colors.white, colors.HexColor("#F5F5FA")]),
                    ("GRID", (0,0), (-1,-1), 0.3, colors.HexColor("#CCCCDD")),
                    ("TOPPADDING", (0,0), (-1,-1), 5),
                    ("BOTTOMPADDING", (0,0), (-1,-1), 5),
                    ("LEFTPADDING", (0,0), (-1,-1), 5),
                    ("VALIGN", (0,0), (-1,-1), "MIDDLE"),
                ]))
                story.append(tbl)
                story.append(Spacer(1, 8))

            # Recommendations section (text after table)
            rec_start = titles_analysis.find("## Рекомендации")
            if rec_start == -1:
                rec_start = titles_analysis.find("## Рекомендации")
            if rec_start != -1:
                rec_text = titles_analysis[rec_start:].strip()
                for line in rec_text.splitlines():
                    line = line.strip()
                    if not line:
                        story.append(Spacer(1, 4))
                    elif line.startswith("### "):
                        story.append(_p(line[4:], 10, bold=True, color=DARK))
                    elif line.startswith("## "):
                        story.append(_p(line[3:], 11, bold=True, color=DARK))
                    elif line.startswith("- ") or line.startswith("* "):
                        story.append(_p(f"• {line[2:]}", 9, color=MID))
                    elif line.startswith("**") and line.endswith("**"):
                        story.append(_p(line.strip("*"), 10, bold=True, color=DARK))
                    else:
                        clean = _re.sub(r'\*{1,2}([^*]+)\*{1,2}', r'\1', line)
                        story.append(_p(clean, 9))

        # ── Descriptions Top-2 ───────────────────────────────────────────────
        story += _section_header("ОПИСАНИЯ — ТОП-2")
        for i, d in enumerate(desc_pairs[:2], 1):
            label = "#1 ЛУЧШЕЕ" if i == 1 else f"#{i}"
            story.append(_p(label, 10, bold=True, color=GOLD))
            story.append(Spacer(1, 4))
            story.append(_p(d.get("es",""), 9, color=DARK))
            if d.get("ru"):
                story.append(Spacer(1, 4))
                story.append(_p(f"RU: {d['ru']}", 9, color=MID))
            story.append(Spacer(1, 10))
            story.append(_hr())

        # ── Preview texts ────────────────────────────────────────────────────
        story += _section_header("ТЕКСТЫ ДЛЯ ПРЕВЬЮ")
        story.append(_p("ТОП-5 (выбраны Claude):", 10, bold=True, color=DARK))
        story.append(Spacer(1, 4))
        prev_data = [[_p(f"#{i}", 9, bold=True, color=colors.white), _p(t, 10, bold=True)]
                     for i, t in enumerate(top5_previews[:5], 1)]
        if prev_data:
            prev_tbl = Table(prev_data, colWidths=[1.2*cm, W - 1.2*cm])
            prev_tbl.setStyle(TableStyle([
                ("BACKGROUND", (0,0), (0,-1), MID),
                ("ROWBACKGROUNDS", (1,0), (1,-1), [colors.HexColor("#FFFBF0"), colors.HexColor("#FFF5DC")]),
                ("TOPPADDING", (0,0), (-1,-1), 6),
                ("BOTTOMPADDING", (0,0), (-1,-1), 6),
                ("LEFTPADDING", (0,0), (-1,-1), 8),
                ("VALIGN", (0,0), (-1,-1), "MIDDLE"),
                ("LINEBELOW", (0,0), (-1,-1), 0.3, colors.HexColor("#DDDDCC")),
            ]))
            story.append(prev_tbl)
        story.append(Spacer(1, 8))
        story.append(_p("Все 10 вариантов:", 10, bold=True, color=DARK))
        story.append(Spacer(1, 4))
        for line in preview_texts_raw.splitlines():
            line = line.strip()
            if line:
                story.append(_p(line, 9, color=MID))

        # ── Tags ────────────────────────────────────────────────────────────
        story += _section_header("ТЕГИ")
        if tags_with:
            story.append(_p("С хештегом (#):", 10, bold=True, color=DARK))
            story.append(Spacer(1, 4))
            story.append(_p(tags_with.replace("\n", "  "), 8, color=MID))
            story.append(Spacer(1, 8))
        if tags_without:
            story.append(_p("Без хештега:", 10, bold=True, color=DARK))
            story.append(Spacer(1, 4))
            story.append(_p(tags_without.replace("\n", "  "), 8, color=MID))

        # ── Thumbnail CTR note ───────────────────────────────────────────────
        if ctr_analysis:
            story += _section_header("CTR-АНАЛИЗ ТУМБНЕЙЛА")
            import re as _re2
            clean = _re2.sub(r'\*{1,2}', '', ctr_analysis).strip()
            story.append(_p(clean, 9, color=DARK))

        doc.build(story)
        return True
    except Exception as e:
        log(f"PDF generation error: {e}")
        import traceback
        traceback.print_exc()
        return False


# ── Claude Opus call ──────────────────────────────────────────────────────────

def call_claude_opus(prompt: str, timeout: int = 300) -> str:
    """
    Calls Claude Opus via CLI subprocess.
    Strips CLAUDECODE from env to avoid nested session error.
    """
    env = os.environ.copy()
    env.pop("CLAUDECODE", None)
    try:
        result = subprocess.run(
            ["claude", "-p", prompt, "--model", "claude-opus-4-5"],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=timeout,
            env=env,
        )
        if result.returncode != 0:
            raise RuntimeError(
                f"claude CLI returned {result.returncode}: {result.stderr[:400]}"
            )
        return result.stdout.strip()
    except subprocess.TimeoutExpired:
        raise RuntimeError(f"claude CLI timed out after {timeout}s")


# ── PixelAgent API ────────────────────────────────────────────────────────────

def pixel_generate_image(prompt: str, api_url: str, api_key: str,
                          retries: int = 3, backoff: int = 5) -> bytes:
    """
    Calls PixelAgent v1 API synchronously.
    Returns raw image bytes (decoded from base64).
    Raises on failure after retries.
    """
    if not _REQUESTS_AVAILABLE:
        raise RuntimeError("requests library is not installed")

    url = f"{api_url.rstrip('/')}/api/v1/image/create"
    headers = {"X-API-Key": api_key, "Content-Type": "application/json"}
    payload = {"prompt": prompt, "aspect_ratio": "16:9"}

    for attempt in range(1, retries + 1):
        try:
            resp = _requests.post(url, json=payload, headers=headers, timeout=180)
            if resp.status_code == 401:
                raise RuntimeError("PixelAgent API: 401 Unauthorized — check PIXEL_API_KEY")
            if resp.status_code != 200:
                raise ValueError(f"API error {resp.status_code}: {resp.text[:200]}")
            data = resp.json()
            img_b64 = data.get("image_b64")
            if not img_b64:
                raise ValueError(f"No image_b64 in response: {list(data.keys())}")
            return base64.b64decode(img_b64)
        except RuntimeError:
            raise  # auth errors → propagate immediately
        except Exception as e:
            log(f"  PixelAgent attempt {attempt}/{retries} failed: {e}")
            if attempt < retries:
                log(f"  Waiting {backoff}s before retry...")
                time.sleep(backoff)
            else:
                raise RuntimeError(f"PixelAgent failed after {retries} attempts: {e}") from e


# ── Thumbnail composition ─────────────────────────────────────────────────────

_FONTS_DIR = _PROJECT_ROOT / "config" / "fonts"


def _load_font(size: int):
    """
    Priority: Organetto → Benzin → any bold in config/fonts/ → Arial Bold → system fallback.
    Put Organetto.ttf / Benzin.ttf in config/fonts/ for best results.
    """
    candidates = []
    if _FONTS_DIR.exists():
        for pat in ["*rganett*", "*enzin*", "*Bold*", "*bold*", "*.ttf", "*.otf"]:
            for p in sorted(_FONTS_DIR.glob(pat)):
                candidates.append(str(p))
    candidates += [
        "C:/Windows/Fonts/arialbd.ttf",
        "C:/Windows/Fonts/ariblk.ttf",   # Arial Black
        "arialbd.ttf",
        "/usr/share/fonts/truetype/liberation/LiberationSans-Bold.ttf",
    ]
    for path in candidates:
        try:
            return ImageFont.truetype(path, size)
        except (IOError, OSError):
            continue
    return ImageFont.load_default()


def _text_size(draw, text: str, font) -> tuple[int, int]:
    try:
        bbox = draw.textbbox((0, 0), text, font=font)
        return bbox[2] - bbox[0], bbox[3] - bbox[1]
    except AttributeError:
        return draw.textsize(text, font=font)


def _fit_font_size(text: str, max_width: int, max_size: int = 140, min_size: int = 40) -> int:
    """Find largest font size that fits text in max_width."""
    tmp = Image.new("RGB", (max_width * 2, 200))
    draw = ImageDraw.Draw(tmp)
    for size in range(max_size, min_size - 1, -2):
        font = _load_font(size)
        tw, _ = _text_size(draw, text, font)
        if tw <= max_width:
            return size
    return min_size


def _split_text_lines(text: str) -> list[str]:
    """
    Smart split into up to 3 lines.
    Prefers natural break points (comma, dash, ?, !).
    Falls back to balanced word split.
    """
    text = text.strip()
    words = text.split()
    n = len(words)

    if n <= 2:
        return [text]

    # Helper: find indices right after punctuation-ending words
    def punct_breaks(word_list):
        return [i + 1 for i, w in enumerate(word_list[:-1])
                if w.rstrip().endswith((',', '—', '–', '-', '?', '!', '.'))]

    def _balanced(a: list, b: list) -> bool:
        """Reject splits where one side has ≤1 word and the other ≥3."""
        return not (len(a) <= 1 and len(b) >= 3) and not (len(b) <= 1 and len(a) >= 3)

    if n <= 5:
        # 3–5 words → 1 or 2 lines
        breaks = punct_breaks(words)
        if breaks:
            s = breaks[0]
            if 0 < s < n and _balanced(words[:s], words[s:]):
                return [" ".join(words[:s]), " ".join(words[s:])]
        # Balanced mid-split fallback
        mid = n // 2
        return [" ".join(words[:mid]), " ".join(words[mid:])]

    # 6–8 words → prefer 2 lines, allow 3
    breaks = punct_breaks(words)
    if len(breaks) >= 2:
        s1, s2 = breaks[0], breaks[1]
        if s1 < s2 < n and s1 > 0:
            return [" ".join(words[:s1]),
                    " ".join(words[s1:s2]),
                    " ".join(words[s2:])]
    if len(breaks) == 1:
        s = breaks[0]
        rest = words[s:]
        mid2 = max(1, len(rest) // 2)
        return [" ".join(words[:s]),
                " ".join(rest[:mid2]),
                " ".join(rest[mid2:])]

    # No punctuation: balanced 3-way split for 7+ words, 2-way for 6
    if n <= 6:
        mid = n // 2
        return [" ".join(words[:mid]), " ".join(words[mid:])]
    s1, s2 = n // 3, 2 * n // 3
    return [" ".join(words[:s1]),
            " ".join(words[s1:s2]),
            " ".join(words[s2:])]


def _apply_warm_grade(img: Image.Image) -> Image.Image:
    """Subtle warm color grade: boost reds/yellows, reduce blue."""
    try:
        import numpy as np
        arr = np.array(img).astype(np.int16)
        arr[:, :, 0] = np.clip(arr[:, :, 0] + 18, 0, 255)   # +red
        arr[:, :, 1] = np.clip(arr[:, :, 1] + 8, 0, 255)    # +green (warmth)
        arr[:, :, 2] = np.clip(arr[:, :, 2] - 15, 0, 255)   # -blue
        return Image.fromarray(arr.astype(np.uint8), img.mode)
    except ImportError:
        return img


def _add_vignette(img: Image.Image) -> Image.Image:
    """Smooth, gentle oval vignette — gradual darkening from 65% radius, max alpha 28."""
    w, h = img.size
    try:
        import numpy as np
        x = np.linspace(-1, 1, w)
        y = np.linspace(-1, 1, h)
        xx, yy = np.meshgrid(x, y)
        # Elliptical distance (wider than tall to match 16:9)
        dist = np.sqrt((xx / 1.1) ** 2 + yy ** 2)
        # Smooth sigmoid-like falloff starting at radius 0.65, full at 1.15
        t = np.clip((dist - 0.65) / 0.50, 0, 1)
        alpha = (t ** 2.0) * 28
        vig_arr = np.zeros((h, w, 4), dtype=np.uint8)
        vig_arr[:, :, 3] = alpha.astype(np.uint8)
        vig = Image.fromarray(vig_arr, "RGBA")
    except ImportError:
        vig = Image.new("RGBA", (w, h), (0, 0, 0, 0))
        draw_v = ImageDraw.Draw(vig)
        for y in range(h):
            t = abs(y / h - 0.5) * 2
            a = int(max(0, (t - 0.60) / 0.40) ** 2.0 * 45)
            draw_v.line([(0, y), (w, y)], fill=(0, 0, 0, a))
    return Image.alpha_composite(img, vig)


def _crop_white_borders(img: Image.Image, threshold: int = 240) -> Image.Image:
    """Remove white/near-white borders added by image generator."""
    try:
        import numpy as np
        arr = np.array(img.convert("RGB"))
        # Find rows/cols that are NOT all-white
        mask = np.any(arr < threshold, axis=2)
        rows = np.where(mask.any(axis=1))[0]
        cols = np.where(mask.any(axis=0))[0]
        if len(rows) == 0 or len(cols) == 0:
            return img
        top, bottom = rows[0], rows[-1] + 1
        left, right = cols[0], cols[-1] + 1
        # Only crop if actual border detected (>1% of dimension)
        h, w = arr.shape[:2]
        if top > h * 0.01 or bottom < h * 0.99 or left > w * 0.01 or right < w * 0.99:
            img = img.crop((left, top, right, bottom))
    except Exception:
        pass
    return img


def _add_particles(img: Image.Image) -> Image.Image:
    """Very subtle golden light particles scattered in background."""
    import random
    w, h = img.size
    particles = Image.new("RGBA", (w, h), (0, 0, 0, 0))
    draw = ImageDraw.Draw(particles)
    rng = random.Random(42)
    n = w * h // 6000
    for _ in range(n):
        x = rng.randint(0, w)
        y = rng.randint(0, h)
        r = rng.randint(1, max(2, w // 400))
        alpha = rng.randint(15, 50)
        draw.ellipse([x - r, y - r, x + r, y + r], fill=(255, 235, 160, alpha))
    particles = particles.filter(ImageFilter.GaussianBlur(radius=1.5))
    return Image.alpha_composite(img, particles)


def _add_noise(img: Image.Image, strength: float = 0.04) -> Image.Image:
    """Film grain noise, 2-6% strength."""
    try:
        import numpy as np
        arr = np.array(img).astype(np.int16)
        amp = int(255 * strength)
        noise = np.random.randint(-amp, amp + 1, (arr.shape[0], arr.shape[1]), dtype=np.int16)
        arr[:, :, 0] = np.clip(arr[:, :, 0] + noise, 0, 255)
        arr[:, :, 1] = np.clip(arr[:, :, 1] + noise, 0, 255)
        arr[:, :, 2] = np.clip(arr[:, :, 2] + noise, 0, 255)
        return Image.fromarray(arr.astype(np.uint8), img.mode)
    except ImportError:
        return img


def _add_light_rays(img: Image.Image) -> Image.Image:
    """Subtle divine golden light rays radiating from top-center."""
    import math
    w, h = img.size
    rays = Image.new("RGBA", (w, h), (0, 0, 0, 0))
    draw = ImageDraw.Draw(rays)
    cx, cy = w // 2, -h // 6
    ray_w = max(6, w // 55)
    for angle in range(-70, 71, 7):
        rad = math.radians(angle)
        ex = cx + int(math.sin(rad) * w * 1.8)
        ey = cy + int(math.cos(rad) * h * 2.2)
        draw.line([(cx, cy), (ex, ey)], fill=(255, 215, 100, 14), width=ray_w)
    rays = rays.filter(ImageFilter.GaussianBlur(radius=18))
    return Image.alpha_composite(img, rays)


def _char_width(draw, char: str, font) -> int:
    """Width of a single character."""
    return _text_size(draw, char, font)[0]


def _draw_words_colored(draw, img: "Image.Image", words: list[str], colors: list[tuple],
                         font, cx: int, y: int, bold: bool = False,
                         letter_spacing: int = 3, word_spacing_extra: int = 0):
    """
    Draw words with per-character letter spacing and soft gaussian shadow.
    letter_spacing: extra pixels between each character (default 3).
    word_spacing_extra: extra pixels between words on top of standard space.
    """
    tmp_draw = ImageDraw.Draw(img)

    def _word_render_width(word: str) -> int:
        return sum(_char_width(tmp_draw, c, font) + letter_spacing for c in word) - letter_spacing

    space_w = _char_width(tmp_draw, " ", font) + word_spacing_extra
    _, line_h = _text_size(tmp_draw, words[0] if words else "A", font)

    total_w = sum(_word_render_width(w) for w in words) + space_w * (len(words) - 1)
    x0 = cx - total_w // 2

    w_img, h_img = img.size
    shadow_layer = Image.new("RGBA", (w_img, h_img), (0, 0, 0, 0))
    sd = ImageDraw.Draw(shadow_layer)
    off = max(4, line_h // 12)

    # Shadow pass — draw each char
    x = x0
    for word in words:
        for char in word:
            cw = _char_width(sd, char, font)
            sd.text((x + off, y + off), char, font=font, fill=(0, 0, 0, 80))
            x += cw + letter_spacing
        x += space_w - letter_spacing  # correct for trailing letter_spacing

    blur_r = max(6, line_h // 9)
    shadow_layer = shadow_layer.filter(ImageFilter.GaussianBlur(radius=blur_r))
    img_out = Image.alpha_composite(img, shadow_layer)

    # Color pass — draw each char with bold stroke
    draw2 = ImageDraw.Draw(img_out)
    stroke_w = 2 if bold else 0
    x = x0
    for word, color in zip(words, colors):
        for char in word:
            cw = _char_width(draw2, char, font)
            try:
                draw2.text((x, y), char, font=font, fill=color,
                           stroke_width=stroke_w, stroke_fill=color)
            except TypeError:
                draw2.text((x + 1, y), char, font=font, fill=color)
                draw2.text((x, y), char, font=font, fill=color)
            x += cw + letter_spacing
        x += space_w - letter_spacing

    return img_out


def _detect_face_bottom(img: "Image.Image") -> float | None:
    """
    Detects the lowest face in the image using OpenCV Haar cascade.
    Returns the bottom edge of the face as a fraction of image height (0.0–1.0),
    or None if no face detected.
    """
    try:
        import cv2
        import numpy as np
        arr = np.array(img.convert("RGB"))
        gray = cv2.cvtColor(arr, cv2.COLOR_RGB2GRAY)
        cascade_path = cv2.data.haarcascades + "haarcascade_frontalface_default.xml"
        detector = cv2.CascadeClassifier(cascade_path)
        h, w = gray.shape
        # Try multiple scales — painted/illustrated faces need looser params
        for scale, neighbors in [(1.05, 3), (1.1, 2), (1.15, 2)]:
            faces = detector.detectMultiScale(
                gray, scaleFactor=scale, minNeighbors=neighbors,
                minSize=(w // 10, h // 10),
            )
            if len(faces) > 0:
                # Take the face with the highest bottom edge (lowest on screen)
                face_bottom = max((fy + fh) / h for (fx, fy, fw, fh) in faces)
                log(f"  Face detected — bottom at {face_bottom:.2f} of image height")
                return face_bottom
        return None
    except Exception as e:
        log(f"  Face detection skipped: {e}")
        return None


def _detect_face_bbox(img: "Image.Image") -> tuple | None:
    """
    Returns (cx_frac, cy_frac, w_frac, h_frac) of the largest detected face,
    all as fractions of image dimensions. Or None if no face detected.
    """
    try:
        import cv2
        import numpy as np
        arr = np.array(img.convert("RGB"))
        gray = cv2.cvtColor(arr, cv2.COLOR_RGB2GRAY)
        detector = cv2.CascadeClassifier(
            cv2.data.haarcascades + "haarcascade_frontalface_default.xml"
        )
        ih, iw = gray.shape
        for scale, neighbors in [(1.05, 3), (1.1, 2), (1.15, 2)]:
            faces = detector.detectMultiScale(
                gray, scaleFactor=scale, minNeighbors=neighbors,
                minSize=(iw // 10, ih // 10),
            )
            if len(faces) > 0:
                fx, fy, fw, fh = max(faces, key=lambda f: f[2] * f[3])
                cx = (fx + fw / 2) / iw
                cy = (fy + fh / 2) / ih
                log(f"  Face bbox: cx={cx:.2f} cy={cy:.2f} fw={fw/iw:.2f} fh={fh/ih:.2f}")
                return (cx, cy, fw / iw, fh / ih)
        return None
    except Exception as e:
        log(f"  Face bbox skipped: {e}")
        return None


def _find_best_text_y(img: "Image.Image", total_text_h_frac: float = 0.12) -> float:
    """
    Smart text position finder:
    1. Tries face detection — places text below face, centered in remaining space
    2. Falls back to brightness scan in lower 60% of image
    Returns center-y of text block as fraction of image height.
    """
    import numpy as np
    h_img, w_img = img.size[1], img.size[0]

    # ── Step 1: Face detection ─────────────────────────────────────────────
    face_bottom = _detect_face_bottom(img)
    if face_bottom is not None:
        # Safe zone: from below face to bottom of image
        safe_top    = face_bottom + 0.02          # 2% margin below face
        safe_bottom = 0.96                         # don't go past 96%
        # Center text in that zone
        zone_center = (safe_top + safe_bottom) / 2
        # Pull toward image center (0.5) with 30% weight
        center = zone_center * 0.70 + 0.50 * 0.30
        return max(safe_top + total_text_h_frac / 2,
                   min(center, safe_bottom - total_text_h_frac / 2))

    # ── Step 2: Brightness scan fallback ──────────────────────────────────
    try:
        arr = np.array(img.convert("RGB"))
        h, w = arr.shape[:2]
        brightness = (arr[:, :, 0] * 0.299 +
                      arr[:, :, 1] * 0.587 +
                      arr[:, :, 2] * 0.114)
        zone_start = int(h * 0.45)       # search from 45% down
        strip_h    = max(1, h // 35)
        best_y, best_val = zone_start, 255.0
        for y in range(zone_start, h - strip_h, strip_h // 2):
            val = brightness[y:y + strip_h, :].mean()
            if val < best_val:
                best_val = val
                best_y   = y
        center = (best_y + strip_h // 2) / h
        # Pull toward image center with 40% weight
        center = center * 0.60 + 0.50 * 0.40
        return max(0.47, min(center, 0.88))
    except Exception:
        return 0.65   # safe default


def save_thumbnail_project(img_bytes: bytes, overlay_text: str, out_path: Path, layout: str = "standard") -> None:
    """
    Saves a thumbnail_editor project.json alongside the PNG so the user
    can open it in the thumbnail editor and fine-tune.
    Text elements are positioned to match the HTML-rendered thumbnail exactly.
    """
    import base64 as _b64
    try:
        from html_thumbnail import _split_lines, _font_size_for_layout
    except ImportError:
        try:
            from agents.meta_agent.html_thumbnail import _split_lines, _font_size_for_layout
        except ImportError:
            _split_lines = lambda t: [t]
            _font_size_for_layout = lambda l, n, c: 140

    try:
        src = "data:image/png;base64," + _b64.b64encode(img_bytes).decode()

        CANVAS_H = 1080
        CANVAS_W = 1920
        ACCENT_WORDS = {"LÁGRIMA", "LÁGRIMAS", "EXTRAÑO", "ORGULLOSO", "NUNCA",
                        "SIEMPRE", "AQUÍ", "URGENTE", "CASUALIDAD", "TODO", "TRAJE"}

        lines = _split_lines(overlay_text)
        n_lines = len(lines)
        max_chars = max(len(l) for l in lines)
        base_size = _font_size_for_layout(layout, n_lines, max_chars)

        # Scale per line: line 0 full size, rest 72% (mirrors html_thumbnail)
        line_sizes = [base_size if i == 0 else int(base_size * 0.72) for i in range(n_lines)]
        GAP = 8  # px between lines (matches CSS gap: 8px)

        # Total height of the text block
        total_h = sum(line_sizes) + GAP * (n_lines - 1)

        # X/Y origin of text block — mirrors CSS layout positions
        if layout == "right_col":
            block_x = CANVAS_W - 48 - 840 + 32   # right:48 width:840 padding:32
            block_y = (CANVAS_H - total_h) // 2   # vertically centered
        elif layout == "left_col":
            block_x = 48 + 32
            block_y = (CANVAS_H - total_h) // 2
        elif layout == "split":
            block_x = 80
            block_y = CANVAS_H - 80 - total_h
        else:  # standard
            block_x = 80
            block_y = CANVAS_H - 72 - total_h

        # Line 0 = red (agent-generated default), rest = white
        def _line_color(i: int) -> str:
            return "#FF3333" if i == 0 else "#FFFFFF"

        # Image as first layer (bottom of stack), then text on top
        elements = [
            {
                "id": 1,
                "type": "image",
                "name": "Background",
                "src": src,
                "x": 0, "y": 0,
                "width": CANVAS_W, "height": CANVAS_H,
                "opacity": 100, "scale": 100,
                "anchorX": 0.5, "anchorY": 0.5, "rotation": 0,
            }
        ]
        y_cursor = block_y
        for i, line in enumerate(lines):
            elements.append({
                "id": i + 2,
                "type": "text",
                "text": line,
                "font": "Impact",
                "size": line_sizes[i],
                "bold": True,
                "color": _line_color(i),
                "x": block_x,
                "y": y_cursor,
                "shadow": True,
                "stroke_width": 3,
                "stroke_color": "#000000",
                "opacity": 100,
                "scale": 100,
                "anchorX": 0.5,
                "anchorY": 0.5,
                "rotation": 0,
            })
            y_cursor += line_sizes[i] + GAP

        project = {
            "version": 2,
            "bg_data": None,
            "effects": {"brightness": 85, "contrast": 110, "saturation": 90, "blur": 0, "vignette": 50, "noise": 0},
            "elements": elements,
            "export_path": str(out_path),
        }

        proj_path = out_path.with_name(out_path.stem + "_project.json")
        proj_path.write_text(
            __import__("json").dumps(project, indent=2, ensure_ascii=False),
            encoding="utf-8"
        )
        log(f"  Project saved → {proj_path.name}")
    except Exception as e:
        log(f"  WARNING: could not save project.json: {e}")


def compose_thumbnail(img_bytes: bytes, overlay_text: str, out_path: Path) -> bool:
    """
    Composes a YouTube thumbnail with:
      - Warm color grade
      - Oval vignette
      - Subtle divine light rays from top
      - Multi-line text with hero (big) + supporting (small) sizes
      - First line GOLD, second WHITE; text glow + stroke
    Returns True on success.
    """
    if not _PIL_AVAILABLE:
        log("  Skipping thumbnail composition — Pillow not available")
        return False
    try:
        img = Image.open(io.BytesIO(img_bytes)).convert("RGBA")
        w, h = img.size
        padding = int(w * 0.07)
        max_w = w - 2 * padding
        cx = w // 2

        # Crop white borders if present
        img = _crop_white_borders(img)

        # Effects pipeline (before upscale — faster at original res)
        img = _apply_warm_grade(img)
        img = _add_light_rays(img)
        img = _add_vignette(img)

        # Bottom gradient — covers text zone. Starts at 55% for longest text, soft ramp.
        # Adaptive max-alpha: bright images (sky, light backgrounds) need a stronger gradient.
        bot = Image.new("RGBA", (w, h), (0, 0, 0, 0))
        n_words_approx = len(overlay_text.split())
        grad_start = 0.70 if n_words_approx <= 4 else (0.62 if n_words_approx <= 7 else 0.55)
        try:
            import numpy as np
            # Measure average brightness in the lower 45% of the image
            arr_rgb = np.array(img.convert("RGB"))
            lower_zone = arr_rgb[int(h * 0.55):, :]
            avg_brightness = lower_zone.mean()
            # Adaptive alpha: bright zone gets stronger gradient, dark zone lighter
            # Range 140–210 (was 190–240) — keeps image lighter overall
            max_alpha = int(140 + max(0, avg_brightness - 80) * 0.40)
            max_alpha = min(max_alpha, 210)

            arr = np.zeros((h, w, 4), dtype=np.uint8)
            bot_start = int(h * grad_start)
            rows = h - bot_start
            for y in range(bot_start, h):
                arr[y, :, 3] = int(max_alpha * ((y - bot_start) / rows) ** 1.3)
            bot = Image.fromarray(arr, "RGBA")
        except ImportError:
            draw_b = ImageDraw.Draw(bot)
            max_alpha = 210
            for y in range(int(h * grad_start), h):
                a = int(max_alpha * (y - h * grad_start) / (h * (1 - grad_start)))
                draw_b.line([(0, y), (w, y)], fill=(0, 0, 0, a))
        img = Image.alpha_composite(img, bot)

        # Split text into lines
        lines = _split_text_lines(overlay_text)
        RED    = (218, 32, 32, 255)
        YELLOW = (255, 210, 40, 255)
        WHITE  = (255, 255, 255, 255)

        # Word color assignment: first word RED, middle YELLOW, last WHITE
        def _assign_colors(words: list[str]) -> list[tuple]:
            n = len(words)
            if n == 1:   return [RED]
            if n == 2:   return [RED, WHITE]
            if n == 3:   return [RED, YELLOW, WHITE]
            # 4+ words: RED RED YELLOW WHITE
            colors = [RED] * (n - 2) + [YELLOW, WHITE]
            return colors[:n]

        # Upscale to HD (1920×1080) for sharp text rendering
        TARGET_W, TARGET_H = 1920, 1080
        img = img.resize((TARGET_W, TARGET_H), Image.LANCZOS)
        w, h = TARGET_W, TARGET_H
        cx = w // 2

        MAX_TEXT_W = int(w * 0.84)
        SAFE_TOP   = int(h * 0.55)   # text block must never start above this

        lines   = _split_text_lines(overlay_text)
        n_lines = len(lines)

        # ── Layout mode: OpenCV face detection ────────────────────────────────
        face_bbox = _detect_face_bbox(img)
        layout_mode = "standard"
        if face_bbox is not None and n_lines >= 2:
            fcx, fcy, fw_f, fh_f = face_bbox
            left_space  = fcx - fw_f / 2
            right_space = 1.0 - fcx - fw_f / 2
            if fh_f > 0.58:
                layout_mode = "standard"
            elif fcx < 0.40:
                layout_mode = "right_col"
            elif fcx > 0.60:
                layout_mode = "left_col"
            elif fw_f < 0.16 and fh_f < 0.30 and left_space > 0.30 and right_space > 0.30:
                layout_mode = "split"
            else:
                layout_mode = "standard"
        log(f"  [OpenCV] layout={layout_mode}" + (
            f" (face cx={face_bbox[0]:.2f} fw={face_bbox[2]:.2f} fh={face_bbox[3]:.2f})"
            if face_bbox is not None else " (no face detected)"
        ))
        COL_MAX_W    = int(w * 0.46)
        col_cx_left  = int(w * 0.24)
        col_cx_right = int(w * 0.76)

        # Font sizing: use column width for split/side layouts, full width for standard
        fit_width = COL_MAX_W if layout_mode != "standard" else MAX_TEXT_W
        if layout_mode == "standard":
            max_font = {1: 180, 2: 150, 3: 112}[min(n_lines, 3)]
        else:
            max_font = {1: 155, 2: 130, 3: 100}[min(n_lines, 3)]

        # Fit hero line (line 0)
        size1     = _fit_font_size(lines[0], fit_width, max_size=max_font, min_size=45)
        size_rest = max(int(size1 * 0.65), 45)
        draw_tmp  = ImageDraw.Draw(img)
        for _ in range(30):
            if all(_text_size(draw_tmp, ln, _load_font(size_rest))[0] <= fit_width
                   for ln in lines[1:]):
                break
            size_rest -= 3

        font1   = _load_font(size1)
        fonts   = [font1] + [_load_font(size_rest)] * (n_lines - 1)
        line_hs = [_text_size(draw_tmp, ln, f)[1] for ln, f in zip(lines, fonts)]

        def _gap(i):
            if i + 1 >= n_lines:
                return 0
            return int((line_hs[i] + line_hs[i + 1]) / 2 * 0.28)

        gaps    = [_gap(i) for i in range(n_lines)]
        total_h = sum(line_hs) + sum(gaps[:-1])

        # Letter spacing: ~1.8% of hero font size
        letter_sp = max(1, int(size1 * 0.018))

        # Per-line colors: line0=RED, middle=YELLOW, last=YELLOW→WHITE
        def _line_colors(words, line_idx):
            if line_idx == 0:
                return [RED] * len(words)
            elif line_idx == n_lines - 1:
                n = len(words)
                if n == 1:   return [YELLOW]
                if n == 2:   return [YELLOW, WHITE]
                return [YELLOW] + [WHITE] * (n - 1)
            else:
                return [YELLOW] * len(words)

        # ── Compute per-line (cx, y) positions based on layout mode ──────────
        line_positions: list[tuple[int, int]] = []   # (cx_px, y_px)

        # Restore split col geometry to symmetric for split layout
        if layout_mode == "split":
            col_cx_left  = int(w * 0.24)
            col_cx_right = int(w * 0.76)

        if layout_mode == "split":
            # Diagonal split: line 0 LEFT column, line 1 RIGHT column, line 2 bottom center
            # Anchor y_start: face bottom, fallback fixed
            if face_bbox is not None:
                face_bot_px = int((face_bbox[1] + face_bbox[3] / 2) * h)
                y_start = max(SAFE_TOP, face_bot_px + int(h * 0.025))
            else:
                y_start = max(SAFE_TOP, int(h * 0.57))
            # If text would go off screen, shrink fonts until it fits
            while y_start + sum(line_hs) + int(h * 0.06) > h - int(h * 0.02) and size1 > 45:
                size1     -= 5
                size_rest  = max(int(size1 * 0.65), 45)
                font1      = _load_font(size1)
                fonts      = [font1] + [_load_font(size_rest)] * (n_lines - 1)
                line_hs    = [_text_size(draw_tmp, ln, f)[1] for ln, f in zip(lines, fonts)]
                gaps       = [_gap(i) for i in range(n_lines)]
                letter_sp  = max(1, int(size1 * 0.018))
            for i, (lh, gap) in enumerate(zip(line_hs, gaps)):
                if i == 0:
                    line_positions.append((col_cx_left,  y_start))
                elif i == 1:
                    # Right column staggered slightly below left
                    y1 = y_start + int(line_hs[0] * 0.40)
                    line_positions.append((col_cx_right, y1))
                else:
                    # Line 2+: full-width center, anchored to bottom area
                    prev_cx, prev_y = line_positions[i - 1]
                    y2 = max(prev_y + line_hs[i - 1] + gaps[i - 1] + int(h * 0.03),
                             int(h * 0.76))
                    line_positions.append((w // 2, y2))

        elif layout_mode in ("left_col", "right_col"):
            col_cx = col_cx_left if layout_mode == "left_col" else col_cx_right
            text_h_frac      = total_h / h
            zone_center_frac = _find_best_text_y(img, total_text_h_frac=text_h_frac)
            zone_center_px   = int(h * zone_center_frac)
            base_y = max(int(h * 0.05), min(zone_center_px - total_h // 2,
                                            h - total_h - int(h * 0.02)))
            y_cur = base_y
            for lh, gap in zip(line_hs, gaps):
                line_positions.append((col_cx, y_cur))
                y_cur += lh + gap

        else:  # standard
            text_h_frac      = total_h / h
            zone_center_frac = _find_best_text_y(img, total_text_h_frac=text_h_frac)
            zone_center_px   = int(h * zone_center_frac)
            base_y = max(SAFE_TOP, min(zone_center_px - total_h // 2,
                                       h - total_h - int(h * 0.02)))
            # Shrink fonts if block still above safe zone
            while base_y < SAFE_TOP and size1 > 45:
                size1     -= 5
                size_rest  = max(int(size1 * 0.65), 45)
                font1      = _load_font(size1)
                fonts      = [font1] + [_load_font(size_rest)] * (n_lines - 1)
                line_hs    = [_text_size(draw_tmp, ln, f)[1] for ln, f in zip(lines, fonts)]
                gaps       = [_gap(i) for i in range(n_lines)]
                total_h    = sum(line_hs) + sum(gaps[:-1])
                base_y     = max(SAFE_TOP, zone_center_px - total_h // 2)
                letter_sp  = max(1, int(size1 * 0.018))
            y_cur = base_y
            for lh, gap in zip(line_hs, gaps):
                line_positions.append((cx, y_cur))
                y_cur += lh + gap

        # ── Unified glow layer ─────────────────────────────────────────────────
        glow = Image.new("RGBA", (w, h), (0, 0, 0, 0))
        gd   = ImageDraw.Draw(glow)
        for i, (ln, f, lh) in enumerate(zip(lines, fonts, line_hs)):
            cx_i, y_i = line_positions[i]
            tw, _ = _text_size(gd, ln, f)
            gd.text((cx_i - tw // 2, y_i), ln, font=f, fill=(0, 0, 0, 110))
        glow = glow.filter(ImageFilter.GaussianBlur(radius=28))
        img  = Image.alpha_composite(img, glow)

        # ── Draw each line at its computed position ────────────────────────────
        for i, (ln, f, lh, gap) in enumerate(zip(lines, fonts, line_hs, gaps)):
            cx_i, y_i = line_positions[i]
            words_i   = ln.split()
            colors_i  = _line_colors(words_i, i)
            draw      = ImageDraw.Draw(img)
            img       = _draw_words_colored(draw, img, words_i, colors_i, f, cx_i, y_i,
                                            bold=True, letter_spacing=letter_sp)

        # Film grain noise (after text so noise sits on top of everything)
        import random as _random
        img = _add_noise(img, strength=_random.uniform(0.02, 0.034))

        out_path.parent.mkdir(parents=True, exist_ok=True)
        img.convert("RGB").save(str(out_path), format="PNG")
        log(f"  Saved thumbnail: {out_path.name} ({out_path.stat().st_size // 1024}KB)")
        return True
    except Exception as e:
        log(f"  ERROR composing thumbnail: {e}")
        import traceback; traceback.print_exc()
        return False


# ── Script reconstruction ─────────────────────────────────────────────────────

def reconstruct_script(result_json: Path) -> str:
    """Joins all segment texts from result.json in order."""
    with open(result_json, encoding="utf-8") as f:
        data = json.load(f)
    segments = data.get("segments", [])
    if not segments:
        raise ValueError(f"No segments found in {result_json}")
    # Sort by id (or start time) to preserve order
    segments_sorted = sorted(segments, key=lambda s: (s.get("id", 0), s.get("start", 0.0)))
    texts = [s.get("text", "").strip() for s in segments_sorted if s.get("text", "").strip()]
    return " ".join(texts)


# ── Prompt builders ───────────────────────────────────────────────────────────

def _build_competitor_block(competitors_data: dict) -> str:
    """Converts competitors JSON into a concise, prompt-friendly insight block."""
    try:
        insights = competitors_data.get("key_insights", {})
        channels = competitors_data.get("channels", [])

        lines = ["\n\n=== АНАЛИЗ КОНКУРЕНТОВ (EN → адаптировать на ES) ==="]

        # Best formula
        best = insights.get("best_performing_formula", "")
        if best:
            lines.append(f"Лучшая формула названия: {best}")

        # Top performing titles
        lines.append("\nТоп видео конкурентов (EN, 2000–6800 просмотров):")
        for ch in channels:
            if ch.get("performance", "").startswith("BEST"):
                for v in ch.get("top_videos", [])[:5]:
                    lines.append(f"  • \"{v['title']}\" — {v['views']} views")
                break

        # Winning hooks
        winning_hooks = []
        for ch in channels:
            winning_hooks.extend(ch.get("winning_hooks", []))
        if winning_hooks:
            lines.append("\nРаботающие крючки:")
            for hook in winning_hooks[:8]:
                lines.append(f"  • {hook}")

        # ES title patterns
        patterns = insights.get("title_patterns_that_work", [])
        if patterns:
            lines.append("\nФормулы названий (адаптировать на ES):")
            for p in patterns:
                lines.append(f"  • {p}")

        # ES prefix/suffix options
        sp = insights.get("spanish_adaptation", {})
        prefixes = sp.get("prefix_options", [])
        suffixes = sp.get("suffix_options", [])
        if prefixes:
            lines.append(f"\nПрефикс ES: {' / '.join(prefixes[:3])}")
        if suffixes:
            lines.append(f"Суффикс ES: {' / '.join(suffixes[:2])}")

        # Thumbnail text ideas
        thumb = insights.get("thumbnail_text_patterns", [])
        if thumb:
            lines.append(f"\nИдеи для текста превью (ES): {', '.join(thumb[:6])}")

        # Avoid
        avoid = insights.get("avoid", "")
        if avoid:
            lines.append(f"\nИЗБЕГАТЬ: {avoid}")

        lines.append("=== КОНЕЦ АНАЛИЗА КОНКУРЕНТОВ ===")
        return "\n".join(lines)
    except Exception:
        return ""


def build_meta_prompt(script: str, competitors_data: dict | None = None) -> str:
    competitor_block = ""
    if competitors_data:
        competitor_block = _build_competitor_block(competitors_data)

    return f"""Ты специалист по YouTube для христианского духовного канала на испанском языке.
Канал — "Dios Te Habla": Бог говорит напрямую к зрителю, тёплое послание, надежда, утешение, исцеление.
Аудитория: верующие испаноязычные люди, переживающие боль, одиночество, сомнения или ищущие знак от Бога.
{competitor_block}
ЗАДАЧА — по сценарию ниже создай:

=== НАЗВАНИЯ (10) RU/ES ===
КАНАЛ: "Dios Te Habla" — Бог говорит лично тебе. Не проповедь, не мотивация — ЛИЧНОЕ ПОСЛАНИЕ.

ФОРМУЛА: "Dios Dice: «[прямая речь Бога]»[эмодзи?] | Mensaje de Dios Para Ti Hoy"

Прямая речь — это что Бог говорит ТЕБЕ прямо сейчас. Интимно, лично, провокационно.

ТЕХНИКИ (каждое название — одна техника):
• ЛИЧНАЯ ЛЮБОВЬ: "Eres Mi Hijo — No Puedo Vivir Sin Ti", "Daría Todo Por Ti Otra Vez"
• FOMO/ИНТРИГА: "Todos Se Lo Perdieron… Y Ya No Pueden Revertirlo" 😱, "Lo Que Estoy A Punto De Decirte Cambia Todo"
• ОБРАТНАЯ ПСИХОЛОГИЯ: "Omite Esto Si Me Odias", "Pasa De Largo Si No Te Importo"
• СРОЧНОСТЬ/ЛИЧНЫЙ ВЫЗОВ: "¿Puedes Darme 3 Minutos?", "Necesito Hablar Contigo Ahora Mismo"
• ИЗБРАННОСТЬ: "Te Elegí Antes De Que Nacieras — Por Eso Estás Aquí", "No Es Coincidencia Que Estés Viendo Esto"
• ТАЙНА/ОТКРОВЕНИЕ: "Hay Algo Que Nunca Te Dijeron — Y Necesitas Saberlo Hoy", "Lo Que Guardé Para Ti Nadie Más Lo Tiene"
• ПРЕРЫВАНИЕ: "Detente — Esto Llegó A Ti Por Una Razón", "Para. Tengo Algo Urgente Que Decirte"
• ЭКСКЛЮЗИВ: "Este Mensaje Es Solo Para Ti — Nadie Más Puede Verlo", "Te Lo Digo A Ti Primero"

ФОРМАТ каждого:
RU: Бог говорит: «[фраза]» | Послание Бога тебе сегодня
ES: Dios Dice: «[frase]»[эмодзи опционально] | Mensaje de Dios Para Ti Hoy

Строго до 100 символов в основной части ES (без суффикса | ...).

СТРОГО ЗАПРЕЩЕНО — никогда не использовать эти темы:
✗ Враг/битва/война ("enemigo", "batalla", "lucha", "guerra")
✗ Выжил/победил/преодолел ("sobreviviste", "venciste", "superaste", "resististe")
✗ Огонь/испытания ("fuego", "pruebas", "tormenta", "atravesaste")
✗ Общие духовные фразы без личного укола
✗ Любые военные метафоры

=== ОПИСАНИЯ (3-5) RU/ES ===
ЦЕЛЬ: Бог говорит лично тебе — тепло, интимно, как самый близкий человек в мире. Не проповедь, не урок. Только личная любовь и присутствие.

СТРУКТУРА (5 блоков — каждый 2-4 предложения):
1. ЛИЧНЫЙ УДАР: Бог говорит напрямую — одно предложение как личное письмо. Зритель чувствует: "это мне". Бог обращается с любовью, нежностью, срочностью.
2. ПРИСУТСТВИЕ В БОЛИ: Бог видел его ночи, слёзы, одиночество, сомнения. Он был там. Он никогда не уходил. "Я видел каждую слезу. Я был рядом, даже когда ты думал, что один."
3. ОТКРОВЕНИЕ ЛЮБВИ: почему зритель важен Богу лично. Не "ты справился" — а "Я никогда тебя не отпускал", "Ты моё дитя", "Ты мой — и я твой".
4. ЛИЧНОЕ ОБЕЩАНИЕ: что Бог обещает прямо сейчас — близость, ответ на молитву, исцеление, новое начало, Его присутствие всегда.
5. ПРИЗЫВ К ДЕЙСТВИЮ: посмотри до конца / поделись с кем-то кто в это проходит / подпишись

Тон: Бог говорит от первого лица ("Yo te vi", "Te sostengo", "Preparé esto para ti", "Te amo", "Eres mío", "Siempre estuve aquí").
Обращение только "tú". Без воды, без общих фраз.
Каждый блок — новый абзац.
НЕ включать стихи из Библии и молитвы.
Минимум 12 предложений в описании.

СТРОГО ЗАПРЕЩЕНО в описаниях:
✗ enemigo, batalla, lucha, guerra — враг/битва/война
✗ sobreviviste, venciste, superaste, resististe — выжил/победил/преодолел
✗ fuego, prueba, tormenta, atravesaste — огонь/испытания/буря
✗ eres fuerte, eres valiente, guerrero — мотивационные лозунги без личного обращения Бога
✗ Третье лицо про Бога ("Él te ama", "Dios quiere") — только первое лицо ("Te amo", "Quiero")
✗ Общие религиозные штампы без личного укола

=== ПРЕВЬЮ (10) RU/ES ===
Текст на тумбнейле. КАПСЛОК. 4–10 слов. Бог говорит от первого лица.
ТОЛЬКО: личная любовь Бога, FOMO, обратная психология, срочное личное послание.

Техники (10 вариантов — по одной технике каждый):
• ЛИЧНАЯ ЛЮБОВЬ: "ERES MI HIJO, NO PUEDO VIVIR SIN TI", "TE NECESITO MÁS DE LO QUE IMAGINAS", "DARÍA TODO POR TI OTRA VEZ"
• ОБРАТНАЯ ПСИХОЛОГИЯ: "OMITE ESTO SI ME ODIAS", "PASA DE LARGO SI NO TE IMPORTO", "SÁLTATE ESTO SI NO ME CREES"
• FOMO/ПОТЕРЯ: "SI TE VAS AHORA — LO PIERDES TODO", "ESTO SOLO PASA UNA VEZ", "NO LO DEJES PASAR"
• СРОЧНОЕ ЛИЧНОЕ: "NECESITO HABLAR CONTIGO AHORA", "DETENTE — TENGO ALGO QUE DECIRTE", "ESPERA. ESTO ES URGENTE"
• ВЫЗОВ: "¿PUEDES DARME 3 MINUTOS?", "SI ME RESPETAS, QUÉDATE", "DEMUÉSTRAME QUE TE IMPORTO"
• ЭКСКЛЮЗИВ: "ESTE MENSAJE ES SOLO TUYO", "TE ELEGÍ A TI — NO ES COINCIDENCIA", "NADIE MÁS PUEDE ESCUCHAR ESTO"
• НАГРАДА: "ACABAS DE GANAR — TE ESCUCHÉ", "LO QUE PEDISTE YA ESTÁ EN CAMINO", "HOY TE RESPONDO"
• ПРИСУТСТВИЕ: "ESTOY AQUÍ — SIEMPRE ESTUVE AQUÍ", "NO ESTÁS SOLO — SOY YO", "TE VEO. SIEMPRE TE VEO"

Формат: "N. **RU:** [текст] | **ES:** [текст]"

СТРОГО ЗАПРЕЩЕНО — эти слова и темы нельзя использовать:
✗ enemigo, batalla, lucha — враг/битва
✗ sobreviviste, venciste, resististe, superaste — выжил/победил
✗ fuego, prueba, tormenta — огонь/испытания
✗ eres fuerte, eres valiente — мотивационные лозунги без личного обращения Бога
✗ Третье лицо про Бога ("Él te ama") — только первое лицо ("Te amo", "Soy yo")

Стиль ВСЕГО: тёплый, духовный, личный — как письмо от Бога, не как реклама.

ТЕГИ (добавить в конце):
Ты специалист по YouTube, твоя главная задача создавать байтовые и интересные теги для YouTube видео по сценарию.
Теги должны быть короткими, интересными и такими, которые помогают продвигать и развивать канал на испанском языке.
Сделай 20 тегов по сценарию С хештегом (#) и 20 тегов по сценарию БЕЗ хештегов.

Выдай всё строго в формате:

=== НАЗВАНИЯ (10) RU/ES ===

=== ОПИСАНИЯ (3-5) RU/ES ===

=== ПРЕВЬЮ (10) RU/ES ===

=== ТЕГИ С # (20) ===

=== ТЕГИ БЕЗ # (20) ===

СЦЕНАРИЙ:
{script}"""


def build_image_prompt(script: str) -> str:
    excerpt = script[:500]
    return f"""You create image prompts for YouTube thumbnail backgrounds for a warm Christian spiritual channel.

STYLE RULES (strictly follow):
- Oil painting style with SUBTLE, FINE brushstrokes — visible but not heavy, like Old Masters
- Photorealistic human anatomy and proportions — NOT cartoon, NOT anime, NOT digital art, NOT fantasy illustration
- Skin texture realistic, facial features anatomically correct
- BRIGHT, LUMINOUS palette: brilliant whites, warm golds, soft sky blues, radiant ambers — LIGHT and HEAVENLY, NOT dark
- Atmosphere: celestial, heavenly, glowing with divine light — like paradise
- Background: bright heavenly sky, soft glowing clouds, golden rays of light, ethereal mist — BRIGHT not dark

CRITICAL — Jesus Christ appearance (ALWAYS):
Long brown wavy hair, short trimmed beard, deep kind eyes, white or light blue flowing robes.
Recognizable as Jesus Christ from classical religious paintings (Renaissance style). NOT a generic man.

COMPOSITION RULES:
- WAIST-UP or FULL BODY only — NEVER extreme face close-up
- Bottom 30% of image: slightly softer/hazier than top, gentle fade — allows text overlay (NOT pitch black, just softer)
- Jesus's face and body must be in the TOP 65% of the image
- No text in image

Create exactly 5 prompts, each a different composition:

1. Jesus Christ waist-up, traditional appearance (long brown hair, beard, white robes), oil painting style, brilliant golden heavenly light surrounding him, bright luminous clouds behind, warm radiant glow, compassionate gaze toward viewer, sky blue and gold palette, photorealistic skin, soft bottom area, no text

2. Jesus Christ full body, arms open wide in welcoming embrace, traditional appearance (brown hair, beard, white robe), oil painting style, standing before bright heavenly sky with glowing white and gold clouds, divine light rays streaming down, brilliant warm atmosphere, figure positioned upper-center, softer lower area, no text

3. Jesus Christ chest-up, traditional appearance (brown hair, beard), oil painting style, single tear on cheek, serene expression, surrounded by brilliant white and golden divine light, soft glowing halo, bright celestial clouds, luminous warm sky, photorealistic anatomy, gentle fade at bottom, no text

4. Jesus Christ waist-up, positioned LEFT side of frame, traditional appearance (long brown hair, beard, white robes), reaching one hand toward viewer, oil painting style, bright heavenly light and clouds on right side, warm golden celestial atmosphere, RIGHT SIDE OF IMAGE relatively clear and bright for text, no text

5. Theme-based: {excerpt[:200]} — Jesus Christ waist-up, traditional Christian appearance (brown hair, beard, white robes), oil painting style, bright divine light, heavenly clouds, celestial golden atmosphere, emotional expression, photorealistic, soft lower portion, no text

Output: exactly 5 prompts, one per line, no numbering, no labels, no extra text."""


# ── Output parsers ────────────────────────────────────────────────────────────

def parse_section(raw: str, section_header: str, next_headers: list[str]) -> str:
    """Extracts a named section from the raw Opus output."""
    start_idx = raw.find(section_header)
    if start_idx == -1:
        return ""
    start_idx += len(section_header)
    end_idx = len(raw)
    for h in next_headers:
        idx = raw.find(h, start_idx)
        if idx != -1 and idx < end_idx:
            end_idx = idx
    return raw[start_idx:end_idx].strip()


def _find_section_header(raw: str, keyword: str) -> str:
    """
    Find the actual === ... === header line containing keyword.
    Claude sometimes varies the exact format (e.g. '(5)' vs '(3-5)'),
    so we search by keyword rather than exact string.
    Returns the actual header string found, or empty string if not found.
    """
    import re
    pattern = r'===\s*[^=]*' + re.escape(keyword) + r'[^=]*==='
    m = re.search(pattern, raw)
    return m.group() if m else ""


def parse_meta_output(raw: str) -> dict:
    """
    Parses the structured Opus output into sections.
    Returns dict with keys: titles, descriptions, preview_texts, tags_with_hash, tags_without_hash
    """
    # Resolve actual headers as Claude wrote them (handles format variations)
    h_titles   = _find_section_header(raw, "НАЗВАНИЯ")
    h_descs    = _find_section_header(raw, "ОПИСАНИЯ")
    h_previews = _find_section_header(raw, "ПРЕВЬЮ")
    h_tags_w   = _find_section_header(raw, "ТЕГИ С #")
    h_tags_wo  = _find_section_header(raw, "ТЕГИ БЕЗ #")

    # Order matters for parse_section's "stop at next header" logic
    ordered = [h for h in [h_titles, h_descs, h_previews, h_tags_w, h_tags_wo] if h]

    def extract(header: str) -> str:
        if not header:
            return ""
        idx = ordered.index(header) if header in ordered else -1
        next_hdrs = ordered[idx + 1:] if idx >= 0 else []
        return parse_section(raw, header, next_hdrs)

    return {
        "titles":            extract(h_titles),
        "descriptions":      extract(h_descs),
        "preview_texts":     extract(h_previews),
        "tags_with_hash":    extract(h_tags_w),
        "tags_without_hash": extract(h_tags_wo),
    }


def parse_image_prompts(raw: str) -> list[str]:
    """
    Parses 3 image prompts from Opus output.
    Each prompt is a non-empty line (strips numbering like '1. ' if present).
    """
    lines = [l.strip() for l in raw.splitlines() if l.strip()]
    prompts = []
    for line in lines:
        # Strip leading numbering: "1. ", "1) ", etc.
        if len(line) > 2 and line[0].isdigit() and line[1] in ".):- ":
            line = line[2:].strip()
        elif len(line) > 3 and line[0].isdigit() and line[1].isdigit() and line[2] in ".):- ":
            line = line[3:].strip()
        if line:
            prompts.append(line)
    return prompts[:3]  # max 3


def _strip_md_bold(s: str) -> str:
    """Remove markdown bold markers **text** → text."""
    import re
    return re.sub(r"\*\*([^*]+)\*\*", r"\1", s).strip()


def extract_preview_texts(preview_section: str) -> list[str]:
    """
    Extracts ES thumbnail text variants from the ПРЕВЬЮ section.
    Handles multiple formats:
      - "**RU:** text | **ES:** ES_TEXT"
      - "RU: text\\nES: ES_TEXT"
      - "ES: ES_TEXT"
      - plain numbered lines
    Returns up to 10 short ES texts.
    """
    lines = [l.strip() for l in preview_section.splitlines() if l.strip()]
    es_texts: list[str] = []

    for line in lines:
        clean = _strip_md_bold(line)

        # Remove leading numbering "1." / "1)" / "1 "
        if clean and clean[0].isdigit():
            for sep in (".", ")", " "):
                parts = clean.split(sep, 1)
                if len(parts) == 2 and parts[0].strip().isdigit():
                    clean = parts[1].strip()
                    break

        clean_up = clean.upper()

        # Format: "RU: ... | ES: ..." (pipe-separated on same line)
        if "|" in clean:
            for part in clean.split("|"):
                part = part.strip()
                part_up = _strip_md_bold(part).upper()
                if part_up.startswith("ES:"):
                    text = _strip_md_bold(part)[3:].strip()
                    if text:
                        es_texts.append(text)
            continue

        # Format: explicit "ES:" line
        if clean_up.startswith("ES:"):
            text = clean[3:].strip()
            if text:
                es_texts.append(text)
            continue

        # Skip RU lines
        if clean_up.startswith("RU:"):
            continue

    if es_texts:
        return es_texts[:10]

    # Fallback: short Latin-char lines
    fallback = []
    for line in lines:
        clean = _strip_md_bold(line)
        if clean and clean[0].isdigit():
            for sep in (".", ")", " "):
                parts = clean.split(sep, 1)
                if len(parts) == 2 and parts[0].strip().isdigit():
                    clean = parts[1].strip()
                    break
        if clean.upper().startswith("RU:"):
            continue
        words = clean.split()
        if 2 <= len(words) <= 6:
            has_latin = any(c.isalpha() and ord(c) < 256 for c in clean)
            if has_latin:
                fallback.append(clean)

    return fallback[:10] if fallback else ["DIOS TE VE", "NO ESTÁS SOLO", "ÉL TE ELIGIÓ"]


# ── Thread A: image prompts ────────────────────────────────────────────────────

def thread_a_image_prompts(script: str) -> dict:
    """
    Thread A: calls Claude Opus to generate 5 image prompts.
    Returns {"image_prompts": [...], "raw": "..."}
    """
    log("Thread A: Generating 5 image prompts via Claude Opus...")
    t0 = time.time()
    prompt = build_image_prompt(script)
    try:
        raw = call_claude_opus(prompt, timeout=180)
        image_prompts = parse_image_prompts(raw)
        # Ensure exactly 5 — pad if Claude returned fewer
        while len(image_prompts) < 5:
            image_prompts.append(image_prompts[0] if image_prompts else
                "Jesus Christ warm golden light compassionate eyes cinematic 8K no text")
        image_prompts = image_prompts[:5]
        log(f"Thread A done in {time.time()-t0:.1f}s — got {len(image_prompts)} image prompts")
        return {"image_prompts": image_prompts, "raw": raw}
    except Exception as e:
        log(f"Thread A ERROR: {e}")
        return {"image_prompts": [], "raw": "", "error": str(e)}


# ── Gemini Flash: image prompts with baked-in text (religion style) ───────────

_RELIGION_STYLE = """Oil painting style with fine brushstrokes in the manner of Old Masters (Rembrandt, Raphael, Caravaggio).
Photorealistic human anatomy and proportions. Skin texture realistic, facial features anatomically correct.
BRIGHT LUMINOUS palette: brilliant whites, warm golds, soft sky blues, radiant ambers. Light and heavenly, NOT dark.
Atmosphere: celestial, divine, glowing with heavenly light. Background: bright heavenly sky, soft glowing clouds, golden light rays, ethereal mist.
Jesus Christ appearance: long brown wavy hair, short trimmed beard, deep kind eyes, white or light blue flowing robes.
Recognizable as Jesus Christ from classical Renaissance religious paintings. NOT a generic man.
Composition: waist-up or full body. Jesus's face and body in the TOP 60% of the image."""

_TEXT_STYLE = """The overlay text must appear in the LOWER 30% of the image.
Text style: ultra-bold condensed white letters, slightly golden or warm tint, massive (25-35% of frame width).
Add subtle dark atmospheric haze behind text zone (soft vignette at bottom, NOT a hard rectangle).
Text must appear as if PAINTED or ENGRAVED into the scene — part of the composition, not a sticker.
The text itself should have a subtle warm golden glow/aura around the letters."""


def build_image_prompts_with_text(preview_texts: list[str], script: str) -> list[str]:
    """
    Uses Gemini Flash to generate 5 image prompts — one per preview text —
    with the Spanish text visually baked into the composition (religion style).
    Returns list of 5 prompt strings.
    """
    log("Gemini Flash: generating 5 image prompts with baked-in text...")
    t0 = time.time()
    excerpt = script[:400]

    prompts_block = "\n".join(
        f'{i+1}. Text for image: "{txt}"'
        for i, txt in enumerate(preview_texts[:5])
    )

    system = f"""You are an expert image prompt writer for PixelAgent (an AI image generator).
You create photorealistic image prompts for a warm Christian spiritual YouTube channel in Spanish.

VISUAL STYLE (always follow):
{_RELIGION_STYLE}

TEXT INTEGRATION (always follow):
{_TEXT_STYLE}

SCRIPT EXCERPT (for thematic context):
{excerpt}"""

    user = f"""Create exactly 5 image prompts. Each prompt corresponds to one preview text that must appear in the image.

{prompts_block}

RULES:
- Each prompt must be a single dense paragraph (no newlines inside), 80-120 words
- Include the EXACT Spanish text from each entry — spelled out verbatim — as it should appear in the image (lower portion, large baked-in text)
- Vary the composition: waist-up, full body, side-lit, arms open, looking at viewer, etc.
- All prompts must include: Jesus Christ traditional appearance, oil painting Old Masters style, bright heavenly light, celestial atmosphere
- Output exactly 5 prompts, one per line, no numbering, no labels, no extra text"""

    try:
        raw = _flash(f"{system}\n\n{user}", max_tokens=2000)
        prompts = [line.strip() for line in raw.splitlines() if line.strip()]
        prompts = [p for p in prompts if len(p) > 30][:5]
        # Pad if fewer than 5
        while len(prompts) < 5:
            txt = preview_texts[len(prompts) % len(preview_texts)] if preview_texts else "DIOS TE VE"
            prompts.append(
                f'Jesus Christ waist-up traditional appearance long brown hair beard white robes, '
                f'oil painting Old Masters style, bright heavenly golden light, celestial clouds, '
                f'warm divine atmosphere, large bold text "{txt}" in lower portion of image, '
                f'baked into scene with warm golden glow around letters'
            )
        log(f"Gemini Flash prompts done in {time.time()-t0:.1f}s — {len(prompts)} prompts")
        return prompts
    except Exception as e:
        log(f"Gemini Flash prompt ERROR: {e} — using fallback prompts")
        fallbacks = []
        for txt in (preview_texts[:5] or ["DIOS TE VE"] * 5):
            fallbacks.append(
                f'Jesus Christ waist-up traditional appearance long brown hair beard white robes, '
                f'oil painting Old Masters style, bright heavenly golden light, celestial atmosphere, '
                f'large ultra-bold white text "{txt}" baked into lower portion of image with warm glow'
            )
        return fallbacks


# ── Thread B: titles + descriptions + tags ────────────────────────────────────

def thread_b_meta(script: str, competitors_data: dict | None = None) -> dict:
    """
    Thread B: calls Claude Opus to generate titles, descriptions, tags.
    Returns parsed sections dict + raw output.
    """
    log("Thread B: Generating titles/descriptions/tags via Claude Opus...")
    t0 = time.time()
    prompt = build_meta_prompt(script, competitors_data)
    try:
        raw = call_claude_opus(prompt, timeout=300)
        parsed = parse_meta_output(raw)
        log(f"Thread B done in {time.time()-t0:.1f}s")
        return {"parsed": parsed, "raw": raw}
    except Exception as e:
        log(f"Thread B ERROR: {e}")
        return {"parsed": {}, "raw": "", "error": str(e)}


# ── Preview text selectors ────────────────────────────────────────────────────

def select_top5_previews(preview_texts: list[str], script: str) -> list[str]:
    """Claude Opus picks 5 best preview texts from 10."""
    if len(preview_texts) <= 5:
        return preview_texts

    options = "\n".join(f"{i+1}. {t}" for i, t in enumerate(preview_texts))
    prompt = f"""Ты эксперт по YouTube CTR для христианского духовного канала на испанском.

Из 10 вариантов текста для тумбнейла выбери 5 ЛУЧШИХ.

Критерии:
• Максимальная байтовость — хочется кликнуть
• Духовная теплота, сила, тепло — как послание от Бога
• 3-4 слова, читается мгновенно
• Эмоциональный удар — интрига, утешение или откровение
• Соответствие теме сценария

Варианты:
{options}

Тема: {script[:250]}

Ответь ТОЛЬКО пятью номерами через запятую, например: "2, 4, 6, 8, 10"
Выбери ровно 5."""
    try:
        import re
        raw = call_claude_opus(prompt, timeout=60)
        nums = [int(x) for x in re.findall(r'\b(\d+)\b', raw) if 1 <= int(x) <= len(preview_texts)]
        seen, result = set(), []
        for n in nums:
            if n not in seen:
                seen.add(n)
                result.append(preview_texts[n - 1])
            if len(result) >= 5:
                break
        # Fill if needed
        if len(result) < 5:
            for t in preview_texts:
                if t not in result:
                    result.append(t)
                if len(result) >= 5:
                    break
        log(f"Top 5 previews: {result}")
        return result[:5]
    except Exception as e:
        log(f"select_top5 error: {e}")
        return preview_texts[:5]


def _clean_title(t: str) -> str:
    """Strip markdown bold, leading **, and trailing parenthetical notes like (73 символа)."""
    import re as _re
    t = _re.sub(r'^\*+\s*', '', _strip_md_bold(t)).strip()
    t = _re.sub(r'\s*\(\d+\s*[а-яёa-z]+\.?\)$', '', t, flags=_re.IGNORECASE).strip()
    return t


def _extract_title_pairs(titles_section: str) -> list[dict]:
    """Extracts list of {ru, es} dicts from titles section."""
    import re as _re
    pairs = []
    ru, es = None, None
    for line in titles_section.splitlines():
        line = line.strip()
        m_ru = _re.search(r'\*{0,2}RU:\*{0,2}\s*(.+)', line)
        m_es = _re.search(r'\*{0,2}ES:\*{0,2}\s*(.+)', line)
        if m_ru:
            ru = _clean_title(m_ru.group(1))
        if m_es:
            es = _clean_title(m_es.group(1))
            pairs.append({"ru": ru or "", "es": es or ""})
            ru, es = None, None
    return pairs


def analyze_titles_for_ctr(titles_section: str, script: str) -> tuple:
    """
    Claude Opus scores all titles: CTR/SEO table + top 3 recommendation.
    Returns (top3_pairs, analysis_table_text).
    Each pair is {ru, es}.
    """
    import re
    pairs = _extract_title_pairs(titles_section)

    if not pairs:
        for line in titles_section.splitlines():
            m = re.match(r'^\d+[\.\)]\s*(.+)', line.strip())
            if m:
                pairs.append({"ru": "", "es": _clean_title(m.group(1))})

    if len(pairs) <= 3:
        return pairs, ""

    options = "\n".join(f"{i+1}. {p['es']}" for i, p in enumerate(pairs))

    prompt = f"""Ты эксперт по YouTube CTR и SEO для христианского духовного канала "Dios Te Habla" на испанском.

Канал: Бог говорит лично тебе — интимно, провокационно, через личную связь. НЕ война/битвы.

Оцени {len(pairs)} названий видео. Для каждого:
- CTR: 1-10 (байтовость: FOMO, обратная психология, интимность, интрига — высокий балл)
- CTR штраф -3 если есть: "enemigo/враг", "batalla/битва", "sobreviviste/выжил", "venciste/победил", "fuego/огонь", "prueba/испытание" — это банально, не байтово
- SEO: 1-10 (поисковый потенциал)
- Тип: FOMO / Личная любовь / Обратная психология / Срочность / Избранность / Тайна / Прерывание

Тема: {script[:200]}

Названия:
{options}

ОТВЕТ СТРОГО В ЭТОМ ФОРМАТЕ:

## Сравнительная таблица всех вариантов

| № | Заголовок | Длина | CTR | SEO | Тип |
|---|-----------|-------|-----|-----|-----|
| 1 | название здесь | 47 | 8 | 7 | FOMO |
(все {len(pairs)} строк)

## Рекомендации

### Лучший для CTR (максимальные клики)
Вариант #N: название

Обоснование:
- причина 1
- причина 2
- причина 3

### ТОП-3 в порядке приоритета: N, N, N"""

    try:
        raw = call_claude_opus(prompt, timeout=90)

        m = re.search(r'ТОП-3[^:]*:\s*([\d,\s]+)', raw)
        if m:
            nums = [int(x) for x in re.findall(r'\b(\d+)\b', m.group(1)) if 1 <= int(x) <= len(pairs)]
        else:
            nums = [int(x) for x in re.findall(r'Вариант\s*#?(\d+)', raw) if 1 <= int(x) <= len(pairs)]

        seen, top3 = set(), []
        for n in nums:
            if n not in seen:
                seen.add(n)
                top3.append(pairs[n - 1])
            if len(top3) >= 3:
                break
        if len(top3) < 3:
            for p in pairs:
                if p not in top3:
                    top3.append(p)
                if len(top3) >= 3:
                    break

        log(f"Top 3 titles: {[p['es'] for p in top3[:3]]}")
        return top3[:3], raw.strip()
    except Exception as e:
        log(f"analyze_titles_for_ctr error: {e}")
        return pairs[:3], ""


def select_top_descriptions(descriptions_section: str, script: str) -> list[dict]:
    """
    Claude Opus ranks descriptions by quality.
    Returns top 2 as list of {es, ru} dicts, best first.
    """
    import re
    # Split on block separators: "**N.**" or plain "N." at start of line
    blocks = re.split(r'\n\s*(?:\*\*\d+[\.\)]\*\*|\d+[\.\)])\s*\n', "\n" + descriptions_section.strip())
    desc_pairs = []
    for block in blocks:
        block = block.strip()
        if not block or len(block) < 20:
            continue
        m_es = re.search(r'\*{0,2}ES:\*{0,2}\s*(.+?)(?=\n\*{0,2}RU:\*{0,2}|\n\*{0,2}ES:\*{0,2}|\Z)', block, re.DOTALL)
        m_ru = re.search(r'\*{0,2}RU:\*{0,2}\s*(.+?)(?=\n\*{0,2}ES:\*{0,2}|\n\*{0,2}RU:\*{0,2}|\Z)', block, re.DOTALL)
        if m_es:
            es = m_es.group(1).strip()
            ru = m_ru.group(1).strip() if m_ru else ""
            desc_pairs.append({"es": es, "ru": ru})
        else:
            cleaned = re.sub(r'\*{1,2}(?:RU|ES):\*{0,2}\s*', '', block).strip()
            if cleaned:
                desc_pairs.append({"es": cleaned, "ru": ""})

    if len(desc_pairs) <= 2:
        return desc_pairs

    options = "\n\n".join(f"Вариант {i+1}:\n{d['es'][:400]}" for i, d in enumerate(desc_pairs))
    prompt = f"""Ты эксперт по YouTube для христианского духовного канала на испанском языке.

Из {len(desc_pairs)} вариантов описания видео выбери 2 ЛУЧШИХ.

Критерии:
• Первое предложение — сильный крючок, сразу захватывает
• Личное обращение к зрителю на «tú»
• Духовная теплота + интрига
• Хороший призыв к действию
• Правильный испанский язык

Варианты:
{options}

Тема: {script[:200]}

Ответь ДВУМЯ номерами в порядке приоритета (лучший первый), например: "2, 1"
Выбери ровно 2."""
    try:
        raw = call_claude_opus(prompt, timeout=60)
        nums = [int(x) for x in re.findall(r'\b(\d+)\b', raw) if 1 <= int(x) <= len(desc_pairs)]
        seen, result = set(), []
        for n in nums:
            if n not in seen:
                seen.add(n)
                result.append(desc_pairs[n - 1])
            if len(result) >= 2:
                break
        if len(result) < 2:
            for d in desc_pairs:
                if d not in result:
                    result.append(d)
                if len(result) >= 2:
                    break
        log(f"Top 2 descriptions selected")
        return result[:2]
    except Exception as e:
        log(f"select_top_descriptions error: {e}")
        return desc_pairs[:2]


def analyze_thumbnails_for_ctr(compositions: list[dict], script: str) -> tuple[list[int], str]:
    """
    Claude Opus ranks 5 thumbnail compositions and returns top 3 zero-based indices.
    Each composition: {"text": str, "image_desc": str}
    Returns (top3_indices, analysis_text) — indices in order (best first).
    """
    items = ""
    for i, c in enumerate(compositions, 1):
        items += f"\nВариант {i}:\n  Текст на тумбнейле: {c['text']}\n  Описание изображения: {c['image_desc'][:180]}\n"

    prompt = f"""Ты эксперт по YouTube CTR и визуальному маркетингу для христианского канала "Dios Te Habla".

Оцени {len(compositions)} вариантов тумбнейла. Выбери ТОП-3 в порядке приоритета.

Критерии:
1. Текст на тумбнейле — байтовый, провокационный, личный (Бог говорит к тебе)
2. Соответствие теме сценария — текст + изображение создают единое послание
3. YouTube-байтовость: эмоция, лицо, контраст, читаемость текста
4. CTR-факторы: интрига, тепло, духовная сила, личное обращение
5. ВАЖНО: Отдавай предпочтение тумбнейлам где лицо Иисуса ЧЁТКО ВИДНО и не перекрыто текстом

{items}
Тема сценария: {script[:300]}

Ответь ТРЕМЯ номерами в порядке приоритета (лучший первый), например: "3, 1, 5"
Затем 3-4 предложения — краткий анализ почему именно эти три и какой #1."""

    try:
        import re
        raw = call_claude_opus(prompt, timeout=90)
        nums = [int(x) for x in re.findall(r'\b(\d+)\b', raw) if 1 <= int(x) <= len(compositions)]
        seen, top3 = set(), []
        for n in nums:
            if n not in seen:
                seen.add(n)
                top3.append(n - 1)  # zero-based
            if len(top3) >= 3:
                break
        # Fill if fewer than 3 returned
        for i in range(len(compositions)):
            if i not in top3:
                top3.append(i)
            if len(top3) >= 3:
                break
        analysis = raw.strip()
        log(f"CTR top 3: variants {[i+1 for i in top3[:3]]}")
        log(f"Analysis: {analysis[:300]}")
        return top3[:3], analysis
    except Exception as e:
        log(f"analyze_thumbnails error: {e}")
        return list(range(min(3, len(compositions)))), ""


# ── Gemini visual evaluation of generated thumbnails ─────────────────────────

_EVAL_PASS_THRESHOLD = 7  # overall score to accept (religion style is softer than cosmos)

def _build_religion_eval_prompt(preview_text: str, script_topic: str) -> str:
    return f"""\
You are a YouTube thumbnail analyst for a warm Christian spiritual channel in Spanish.
You understand what drives CTR in the faith/religion/spiritual niche.

Evaluate this thumbnail. The image should contain text "{preview_text}" baked directly into the scene.

VIDEO TOPIC: {script_topic}

Score 1-10 on EACH criterion. Be honest — most AI images deserve 5-6:

TEXT QUALITY:
1. text_correctness: Is the text "{preview_text}" actually visible and correctly spelled in Spanish?
   Score 1 if text is missing, distorted, misspelled, gibberish, or invisible.
2. text_readability: Readable on a 120px mobile thumbnail? Bold enough? High contrast vs background?
3. text_size: Is the text LARGE — at least 25% of frame width? Small text = 1-3.
4. text_placement: Is text in the lower portion of the image with clear separation from the figure?

VISUAL QUALITY:
5. jesus_recognizable: Is the figure recognizable as Jesus Christ (long brown hair, beard, robes)?
   Generic man or wrong appearance = 1-4.
6. composition: Is the composition balanced? Figure in upper portion, text in lower? Clear focal point?
7. visual_impact: Does this image feel divine, warm, spiritual? Would it stop someone scrolling?
8. color_quality: Bright luminous palette — warm golds, whites, sky blues? Dark or muddy = 1-3.

SEMANTIC:
9. topic_match: Does the visual connect to the video topic and the preview text message?
10. niche_fit: Does this look like a high-performing Christian spiritual YouTube thumbnail?
11. ctr_potential: Would a Spanish-speaking viewer click this? Emotional pull?
12. overall: Weighted overall. 8+ only if this could genuinely compete with top Spanish Christian channels.

Return ONLY valid JSON:
{{
  "scores": {{
    "text_correctness": <1-10>,
    "text_readability": <1-10>,
    "text_size": <1-10>,
    "text_placement": <1-10>,
    "jesus_recognizable": <1-10>,
    "composition": <1-10>,
    "visual_impact": <1-10>,
    "color_quality": <1-10>,
    "topic_match": <1-10>,
    "niche_fit": <1-10>,
    "ctr_potential": <1-10>,
    "overall": <1-10>
  }},
  "passed": <true if overall >= {_EVAL_PASS_THRESHOLD} else false>,
  "text_issues": "<specific text problems: missing/misspelled/too small/wrong placement, or null>",
  "visual_issues": "<specific visual problems, or null>",
  "what_works": "<what is genuinely strong about this thumbnail>"
}}
"""


def evaluate_religion_thumbnails(
    saved_paths: list[Path],
    preview_texts: list[str],
    script_topic: str,
) -> list[dict]:
    """
    Gemini Flash looks at each generated thumbnail and scores it.
    Returns list of dicts with path, scores, overall, passed, text_issues.
    """
    import re as _re
    results = []
    for idx, path in enumerate(saved_paths):
        if not path.exists():
            continue
        text = preview_texts[idx] if idx < len(preview_texts) else ""
        img_bytes = path.read_bytes()
        eval_prompt = _build_religion_eval_prompt(text, script_topic)
        try:
            raw = _flash_with_image(eval_prompt, img_bytes, max_tokens=800)
            raw_clean = _re.sub(r'^```(?:json)?\s*|\s*```$', '', raw, flags=_re.DOTALL).strip()
            data = json.loads(raw_clean)
            scores  = data.get("scores", {})
            overall = scores.get("overall", 0)
            passed  = data.get("passed", False)
            txt_issues = data.get("text_issues") or ""
            vis_issues = data.get("visual_issues") or ""
            good       = data.get("what_works", "")

            log(f"  [{path.name}] overall={overall} passed={passed}")
            log(f"    text: correct={scores.get('text_correctness','?')} "
                f"read={scores.get('text_readability','?')} "
                f"size={scores.get('text_size','?')} "
                f"place={scores.get('text_placement','?')}")
            log(f"    visual: jesus={scores.get('jesus_recognizable','?')} "
                f"comp={scores.get('composition','?')} "
                f"impact={scores.get('visual_impact','?')} "
                f"color={scores.get('color_quality','?')}")
            if passed:
                log(f"    ✅ {good}")
            else:
                if txt_issues:
                    log(f"    ❌ TEXT: {txt_issues}")
                if vis_issues:
                    log(f"    ❌ VISUAL: {vis_issues}")

            results.append({
                "path":        path,
                "preview_text": text,
                "scores":      scores,
                "overall":     overall,
                "passed":      passed,
                "text_issues": txt_issues,
                "visual_issues": vis_issues,
                "what_works":  good,
            })
        except Exception as e:
            log(f"  Eval error for {path.name}: {e}")
            results.append({
                "path": path, "preview_text": text,
                "scores": {}, "overall": 0, "passed": False,
                "text_issues": "eval_error", "visual_issues": "", "what_works": "",
            })
    return results


def _flash_with_image(prompt: str, img_bytes: bytes, max_tokens: int = 800) -> str:
    from google.genai import types as gtypes
    import io as _io
    try:
        from PIL import Image as _PILImage
        pil = _PILImage.open(_io.BytesIO(img_bytes)).convert("RGB")
        buf = _io.BytesIO()
        pil.save(buf, format="JPEG", quality=92)
        jpeg_bytes = buf.getvalue()
    except Exception:
        jpeg_bytes = img_bytes  # fallback: send as-is

    parts = [
        gtypes.Part.from_bytes(data=jpeg_bytes, mime_type="image/jpeg"),
        gtypes.Part.from_text(text=prompt),
    ]
    for attempt in range(5):
        try:
            resp = _get_gemini_client().models.generate_content(
                model=_FLASH_MODEL,
                contents=parts,
                config={"temperature": 0.2, "max_output_tokens": max_tokens, **_NO_THINKING},
            )
            return resp.text.strip()
        except Exception as e:
            if "503" in str(e) or "UNAVAILABLE" in str(e):
                wait = 15 * (attempt + 1)
                log(f"[Flash] 503, retry {attempt+1}/5 in {wait}s...")
                time.sleep(wait)
            else:
                raise
    raise RuntimeError("Flash API unavailable after 5 retries")


# ── Image generation + thumbnail composition ──────────────────────────────────

def generate_and_compose_thumbnails(
    image_prompts: list[str],
    preview_texts: list[str],
    meta_dir: Path,
    api_url: str,
    api_key: str,
    script_topic: str = "",
) -> tuple[list[Path], list[dict]]:
    """
    Generates 5 images in parallel, evaluates each with Gemini Flash.
    Returns (saved_paths, eval_results).
    """
    if not image_prompts:
        log("No image prompts — skipping thumbnail generation")
        return [], []
    if not api_url or not api_key:
        log("PIXEL_API_URL or PIXEL_API_KEY not set — skipping image generation")
        return [], []

    while len(preview_texts) < len(image_prompts):
        preview_texts.append(preview_texts[0] if preview_texts else "DIOS TE VE")

    log(f"Generating {len(image_prompts)} thumbnail images in parallel...")
    t0 = time.time()

    raw_images: dict[int, bytes] = {}

    def _gen_one(idx_prompt: tuple[int, str]) -> tuple[int, bytes | None]:
        idx, prompt = idx_prompt
        log(f"  Generating image {idx+1}/{len(image_prompts)}...")
        try:
            img_bytes = pixel_generate_image(prompt, api_url, api_key)
            log(f"  Image {idx+1} generated ({len(img_bytes)//1024}KB)")
            return idx, img_bytes
        except Exception as e:
            log(f"  Image {idx+1} FAILED: {e}")
            return idx, None

    with ThreadPoolExecutor(max_workers=3) as exe:
        futures = {exe.submit(_gen_one, (i, p)): i for i, p in enumerate(image_prompts)}
        for future in as_completed(futures):
            idx, img_bytes = future.result()
            if img_bytes is not None:
                raw_images[idx] = img_bytes

    log(f"Image generation done in {time.time()-t0:.1f}s — {len(raw_images)}/{len(image_prompts)} succeeded")

    saved_paths = []

    # Text is baked directly into the image — save raw bytes, no PIL overlay.
    for idx in range(len(image_prompts)):
        img_bytes = raw_images.get(idx)
        if img_bytes is None:
            log(f"  Skipping thumbnail_{idx+1}.png — image generation failed")
            continue
        out_path = meta_dir / f"thumbnail_{idx+1}.png"
        try:
            out_path.write_bytes(img_bytes)
            saved_paths.append(out_path)
            log(f"  Saved: {out_path.name} ({len(img_bytes)//1024}KB)")
        except Exception as e:
            log(f"  ERROR saving {out_path.name}: {e}")

    # ── Gemini Flash visual evaluation ────────────────────────────────────────
    eval_results: list[dict] = []
    if saved_paths:
        log(f"\nGemini Flash: evaluating {len(saved_paths)} thumbnails...")
        eval_results = evaluate_religion_thumbnails(saved_paths, preview_texts, script_topic)
        passed_count = sum(1 for r in eval_results if r["passed"])
        log(f"Evaluation done — {passed_count}/{len(eval_results)} passed (threshold={_EVAL_PASS_THRESHOLD})")

    return saved_paths, eval_results


# ── Main ──────────────────────────────────────────────────────────────────────

def main_thumbnails_only(channel_id: str, session: str) -> None:
    """
    Re-generates only thumbnails for an existing session.
    Loads image_prompts and top-5 preview texts from meta_raw.json,
    re-fetches images from PixelAgent, recomposes and picks top-3.
    Does NOT touch titles, descriptions, tags, or PDF.
    """
    log(f"[THUMBNAILS ONLY] Regenerating thumbnails for {channel_id} / {session}")
    t0 = time.time()

    session_dir = get_session_dir(channel_id, session)
    meta_dir    = session_dir / "meta"
    raw_json    = meta_dir / "meta_raw.json"

    if not raw_json.exists():
        log(f"ERROR: meta_raw.json not found at {raw_json} — run full meta generation first")
        sys.exit(1)

    with open(raw_json, encoding="utf-8") as f:
        raw_data = json.load(f)

    # Load top-5 preview texts first, then regenerate image prompts with text baked in
    try:
        script_for_prompts = reconstruct_script(
            get_transcripts_dir(channel_id, session) / "result.json"
        )
    except Exception:
        script_for_prompts = ""

    # Load top-5 preview texts from preview_texts.txt
    preview_txt = meta_dir / "preview_texts.txt"
    top5_previews: list[str] = []
    if preview_txt.exists():
        content = preview_txt.read_text(encoding="utf-8")
        in_top5 = False
        for line in content.splitlines():
            if "ТОП-5" in line:
                in_top5 = True
                continue
            if in_top5 and line.strip().startswith("==="):
                break
            if in_top5:
                m = __import__("re").match(r'^\d+\.\s+(.+)', line.strip())
                if m:
                    top5_previews.append(m.group(1).strip())

    if not top5_previews:
        top5_previews = raw_data.get("preview_text_top5", [])
    if not top5_previews:
        log("WARNING: could not load top-5 previews, using defaults")
        top5_previews = ["DIOS TE VE"] * 5

    log(f"Top-5 previews: {top5_previews}")

    env = load_env()
    pixel_api_url = env.get("PIXEL_API_URL", "").rstrip("/")
    pixel_api_key = env.get("PIXEL_API_KEY", "")
    if not pixel_api_url or not pixel_api_key:
        log("ERROR: PIXEL_API_URL or PIXEL_API_KEY not set in config/.env")
        sys.exit(1)

    # Generate fresh image prompts with text baked in via Gemini Flash
    log("Generating fresh image prompts with text via Gemini Flash...")
    image_prompts = build_image_prompts_with_text(top5_previews, script_for_prompts)
    if not image_prompts:
        log("ERROR: no image_prompts generated")
        sys.exit(1)

    # Load script for CTR analysis
    transcripts_dir = get_transcripts_dir(channel_id, session)
    try:
        script = reconstruct_script(transcripts_dir / "result.json")
    except Exception:
        script = ""

    all_thumb_paths, eval_results = generate_and_compose_thumbnails(
        image_prompts=image_prompts[:5],
        preview_texts=top5_previews[:5],
        meta_dir=meta_dir,
        api_url=pixel_api_url,
        api_key=pixel_api_key,
        script_topic=script_for_prompts[:300],
    )
    log(f"Generated {len(all_thumb_paths)} thumbnails")

    if not all_thumb_paths:
        log("ERROR: no thumbnails generated")
        sys.exit(1)

    # Sort by Gemini overall score → top 3
    if eval_results:
        sorted_evals = sorted(eval_results, key=lambda r: r["overall"], reverse=True)
        top3_indices = [all_thumb_paths.index(r["path"]) for r in sorted_evals[:3]
                        if r["path"] in all_thumb_paths]
    else:
        top3_indices = list(range(min(3, len(all_thumb_paths))))

    save_names = ["thumbnail.png", "thumbnail_2.png", "thumbnail_3.png"]
    top3_set = set(top3_indices[:3])
    temp_paths: dict[int, Path] = {}

    for i, path in enumerate(all_thumb_paths):
        try:
            if i in top3_set:
                tmp = meta_dir / f"_tmp_thumb_{i}.png"
                if tmp.exists(): tmp.unlink()
                path.rename(tmp)
                temp_paths[i] = tmp
            else:
                if path.exists(): path.unlink()
                log(f"  Deleted: {path.name}")
        except Exception as e:
            log(f"  File op error: {e}")

    for rank, i in enumerate(top3_indices[:3]):
        tmp = temp_paths.get(i)
        if not tmp or not tmp.exists():
            continue
        dest = meta_dir / save_names[rank]
        try:
            if dest.exists(): dest.unlink()
            tmp.rename(dest)
            log(f"  Top {rank+1}: variant {i+1} → {save_names[rank]}")
        except Exception as e:
            log(f"  File op error: {e}")

    log(f"Done in {time.time()-t0:.1f}s")


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Generate YouTube meta (titles, descriptions, tags, thumbnails) for a session"
    )
    parser.add_argument("--channel", required=True, help="Channel ID, e.g. channel_003_religion_es")
    parser.add_argument("--session", required=True, help="Session name, e.g. Video_20260404_174933")
    parser.add_argument("--thumbnails-only", action="store_true",
                        help="Re-generate thumbnails only (reuse existing prompts from meta_raw.json)")
    parser.add_argument("--engine", choices=["pixel", "canva"], default="pixel",
                        help="Thumbnail engine: 'pixel' (PixelAgent+PIL, default) or 'canva' (skip thumbnails, let Claude generate via Canva MCP)")
    args = parser.parse_args()

    channel_id = args.channel
    session    = args.session

    if args.thumbnails_only:
        main_thumbnails_only(channel_id, session)
        return

    log(f"Starting meta generation for {channel_id} / {session}")
    t_total = time.time()

    # ── Resolve paths ─────────────────────────────────────────────────────────
    session_dir   = get_session_dir(channel_id, session)
    transcripts_dir = get_transcripts_dir(channel_id, session)
    result_json   = transcripts_dir / "result.json"
    meta_dir      = session_dir / "meta"
    meta_dir.mkdir(parents=True, exist_ok=True)

    log(f"Session dir:    {session_dir}")
    log(f"Transcripts:    {transcripts_dir}")
    log(f"Meta output:    {meta_dir}")

    # ── Load result.json ──────────────────────────────────────────────────────
    if not result_json.exists():
        log(f"ERROR: result.json not found at {result_json}")
        sys.exit(1)

    log(f"Loading transcript from {result_json}")
    try:
        script = reconstruct_script(result_json)
        log(f"Script reconstructed: {len(script)} chars, ~{len(script.split())} words")
    except Exception as e:
        log(f"ERROR reconstructing script: {e}")
        sys.exit(1)

    # ── Load competitor reference data ────────────────────────────────────────
    competitors_data = None
    competitors_path = _PROJECT_ROOT / "config" / "meta_references" / channel_id / "competitors.json"
    if competitors_path.exists():
        try:
            with open(competitors_path, encoding="utf-8") as f:
                competitors_data = json.load(f)
            log(f"Loaded competitors.json from {competitors_path}")
        except Exception as e:
            log(f"WARNING: Could not load competitors.json: {e}")

    # ── Load env ──────────────────────────────────────────────────────────────
    env           = load_env()
    thumb_engine  = args.engine          # "pixel" | "canva"
    pixel_api_url = env.get("PIXEL_API_URL", "").rstrip("/")
    pixel_api_key = env.get("PIXEL_API_KEY", "")
    imgbb_api_key = env.get("IMGBB_API_KEY", "")
    if thumb_engine == "pixel" and (not pixel_api_url or not pixel_api_key):
        log("WARNING: PIXEL_API_URL or PIXEL_API_KEY not found in config/.env — image generation will be skipped")

    # ── Thread B: titles + descriptions + tags + preview texts ───────────────
    log("Launching Claude Opus (Thread B: meta)...")
    t_claude = time.time()

    thread_b_result: dict = {}
    thread_b_result = thread_b_meta(script, competitors_data)

    log(f"Claude Opus done in {time.time()-t_claude:.1f}s")

    # ── Extract data from results ─────────────────────────────────────────────
    parsed_meta    = thread_b_result.get("parsed", {})
    raw_meta       = thread_b_result.get("raw", "")

    # ── Extract 10 preview texts → Claude picks top 5 ────────────────────────
    preview_section = parsed_meta.get("preview_texts", "")
    all_previews    = extract_preview_texts(preview_section)
    log(f"All 10 preview candidates: {all_previews}")

    log("Claude selecting top 5 preview texts...")
    top5_previews = select_top5_previews(all_previews, script)

    # ── Gemini Flash: generate 5 image prompts with text baked in ─────────────
    image_prompts: list[str] = []
    if thumb_engine == "pixel" and pixel_api_url and pixel_api_key:
        image_prompts = build_image_prompts_with_text(top5_previews, script)

    # ── Generate 5 thumbnails → Claude picks 3 best ───────────────────────────
    thumbnail_paths = []
    winner_paths    = []
    ctr_analysis    = ""

    if thumb_engine == "canva":
        # Canva engine: thumbnails will be generated via Canva MCP by Claude
        # after this script exits. We just print a CANVA_JOB marker.
        log("Engine: canva — skipping PixelAgent/PIL thumbnail generation")
        log("CANVA_JOB: " + json.dumps({
            "session_dir":    str(session_dir),
            "meta_dir":       str(meta_dir),
            "channel_id":     channel_id,
            "session":        session,
            "preview_texts":  top5_previews,
        }, ensure_ascii=False))
    elif image_prompts and pixel_api_url and pixel_api_key:
        script_topic = script[:300]
        all_thumb_paths, eval_results = generate_and_compose_thumbnails(
            image_prompts=image_prompts,
            preview_texts=top5_previews,
            meta_dir=meta_dir,
            api_url=pixel_api_url,
            api_key=pixel_api_key,
            script_topic=script_topic,
        )
        log(f"All {len(all_thumb_paths)} thumbnails generated and evaluated")

        # Sort by Gemini overall score → pick top 3
        if eval_results:
            sorted_evals = sorted(eval_results, key=lambda r: r["overall"], reverse=True)
            top3_evals = sorted_evals[:3]
            top3_indices = [all_thumb_paths.index(r["path"]) for r in top3_evals
                            if r["path"] in all_thumb_paths]
            ctr_analysis = " | ".join(
                f"{r['path'].name}: overall={r['overall']} text={r['scores'].get('text_correctness','?')}"
                for r in top3_evals
            )
        else:
            top3_indices = list(range(min(3, len(all_thumb_paths))))
            ctr_analysis = ""

        # Save top 3 as thumbnail.png, thumbnail_2.png, thumbnail_3.png; delete the rest.
        save_names = ["thumbnail.png", "thumbnail_2.png", "thumbnail_3.png"]
        top3_set = set(top3_indices[:3])
        temp_paths: dict[int, Path] = {}

        # Phase 1: move winners to temp, delete losers
        for i, path in enumerate(all_thumb_paths):
            try:
                if i in top3_set:
                    tmp = meta_dir / f"_tmp_thumb_{i}.png"
                    if tmp.exists():
                        tmp.unlink()
                    path.rename(tmp)
                    temp_paths[i] = tmp
                else:
                    if path.exists():
                        path.unlink()
                        log(f"  Deleted: {path.name}")
            except Exception as e:
                log(f"  File op error (phase 1): {e}")

        # Phase 2: rename temps to final names
        for rank, i in enumerate(top3_indices[:3]):
            tmp = temp_paths.get(i)
            if tmp is None or not tmp.exists():
                log(f"  WARNING: temp file missing for variant {i+1}")
                continue
            dest = meta_dir / save_names[rank]
            try:
                if dest.exists():
                    dest.unlink()
                tmp.rename(dest)
                winner_paths.append(dest)
                log(f"  Top {rank+1}: variant {i+1} → {save_names[rank]}")
            except Exception as e:
                log(f"  File op error (phase 2): {e}")

        thumbnail_paths = winner_paths
        log(f"Gemini scores summary: {ctr_analysis}")
    elif thumb_engine == "pixel":
        log("Skipping thumbnail generation (missing image prompts or API credentials)")

    # ── Save preview_texts.txt (all 10 with RU/ES) ───────────────────────────
    preview_txt_path = meta_dir / "preview_texts.txt"
    try:
        preview_section_raw = parsed_meta.get("preview_texts", "")
        preview_content = "=== ВАРИАНТЫ ТЕКСТА ДЛЯ ПРЕВЬЮ (все 10) ===\n\n"
        preview_content += preview_section_raw.strip()
        preview_content += "\n\n=== ТОП-5 (выбраны Claude) ===\n"
        for i, t in enumerate(top5_previews, 1):
            preview_content += f"{i}. {t}\n"
        preview_content += f"\n=== ПОБЕДИТЕЛИ (3 лучших тумбнейла) ===\n"
        preview_content += f"CTR-анализ:\n{ctr_analysis}\n"
        preview_txt_path.write_text(preview_content, encoding="utf-8")
        log(f"Saved preview_texts.txt ({len(preview_content)} chars)")
    except Exception as e:
        log(f"ERROR saving preview_texts.txt: {e}")

    # ── Rank titles and descriptions by priority ─────────────────────────────
    titles_section = parsed_meta.get("titles", "")
    desc_section   = parsed_meta.get("descriptions", "")

    log("Claude analyzing titles (CTR table + top 3)...")
    top3_title_pairs, titles_analysis = analyze_titles_for_ctr(titles_section, script)

    log("Claude ranking descriptions (top 2)...")
    top2_desc_pairs = select_top_descriptions(desc_section, script)

    # ── Save text outputs ─────────────────────────────────────────────────────
    titles_path = meta_dir / "titles.txt"
    desc_path   = meta_dir / "descriptions.txt"
    tags_path   = meta_dir / "tags.txt"
    final_path  = meta_dir / "final.txt"

    try:
        titles_content = ""
        if top3_title_pairs:
            for i, p in enumerate(top3_title_pairs, 1):
                label = "#1 ЛУЧШЕЕ" if i == 1 else f"#{i}"
                titles_content += f"{label}\nES: {p['es']}\nRU: {p['ru']}\n\n"
        if titles_analysis:
            titles_content += titles_analysis + "\n\n"
        titles_content += "=== ВСЕ ВАРИАНТЫ ===\n" + (titles_section or "(нет)")
        titles_path.write_text(titles_content, encoding="utf-8")
        log(f"Saved titles.txt ({len(titles_content)} chars)")
    except Exception as e:
        log(f"ERROR saving titles.txt: {e}")

    try:
        desc_content = ""
        if top2_desc_pairs:
            for i, d in enumerate(top2_desc_pairs, 1):
                label = "#1 ЛУЧШЕЕ" if i == 1 else f"#{i}"
                desc_content += f"{label}\n{d['es']}\n\n"
            desc_content += "=== ВСЕ ВАРИАНТЫ ===\n" + (desc_section or "(нет)")
        else:
            desc_content = desc_section or "(no descriptions generated)"
        desc_path.write_text(desc_content, encoding="utf-8")
        log(f"Saved descriptions.txt ({len(desc_content)} chars)")
    except Exception as e:
        log(f"ERROR saving descriptions.txt: {e}")

    # ── Save final.txt (best title + best description, ES + RU) ─────────────
    try:
        best_title = top3_title_pairs[0] if top3_title_pairs else {"es": "", "ru": ""}
        best_desc  = top2_desc_pairs[0]  if top2_desc_pairs  else {"es": "", "ru": ""}
        final_content  = "=== НАЗВАНИЕ ===\n\n"
        final_content += f"ES: {best_title['es']}\n"
        if best_title.get('ru'):
            final_content += f"RU: {best_title['ru']}\n"
        final_content += "\n\n=== ОПИСАНИЕ ===\n\n"
        final_content += f"ES:\n{best_desc['es']}\n"
        if best_desc.get('ru'):
            final_content += f"\n---\nRU:\n{best_desc['ru']}\n"
        final_path.write_text(final_content, encoding="utf-8")
        log(f"Saved final.txt")
    except Exception as e:
        log(f"ERROR saving final.txt: {e}")

    tags_with    = parsed_meta.get("tags_with_hash", "")
    tags_without = parsed_meta.get("tags_without_hash", "")
    try:
        tags_content = ""
        if tags_with:
            tags_content += "=== ТЕГИ С # (20) ===\n" + tags_with + "\n\n"
        if tags_without:
            tags_content += "=== ТЕГИ БЕЗ # (20) ===\n" + tags_without
        if not tags_content:
            tags_content = "(no tags generated)"
        tags_path.write_text(tags_content, encoding="utf-8")
        log(f"Saved tags.txt ({len(tags_content)} chars)")
    except Exception as e:
        log(f"ERROR saving tags.txt: {e}")

    # ── Generate PDF report ───────────────────────────────────────────────────
    pdf_path = meta_dir / "meta_report.pdf"
    log("Generating PDF report...")
    pdf_ok = save_meta_pdf(
        output_path     = pdf_path,
        title_pairs     = top3_title_pairs,
        titles_analysis = titles_analysis,
        desc_pairs      = top2_desc_pairs,
        preview_texts_raw = parsed_meta.get("preview_texts", ""),
        top5_previews   = top5_previews,
        tags_with       = tags_with,
        tags_without    = tags_without,
        ctr_analysis    = ctr_analysis,
        channel         = channel_id,
        session         = session,
    )
    if pdf_ok:
        log(f"Saved meta_report.pdf")

    # ── Save meta_raw.json ────────────────────────────────────────────────────
    meta_raw_path = meta_dir / "meta_raw.json"
    try:
        meta_raw = {
            "channel":          channel_id,
            "session":          session,
            "generated_at":     time.strftime("%Y-%m-%dT%H:%M:%S"),
            "script_chars":     len(script),
            "script_words":     len(script.split()),
            "image_prompts":    image_prompts,
            "preview_text_top5": top5_previews,
            "preview_text_all":  all_previews,
            "ctr_analysis":      ctr_analysis,
            "raw_meta_output":  raw_meta,
            "parsed_sections":  parsed_meta,
            "thumbnail_paths":  [str(p) for p in thumbnail_paths],
            "errors": {
                "thread_b": thread_b_result.get("error"),
            },
        }
        meta_raw_path.write_text(
            json.dumps(meta_raw, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        log(f"Saved meta_raw.json")
    except Exception as e:
        log(f"ERROR saving meta_raw.json: {e}")

    # ── Summary ───────────────────────────────────────────────────────────────
    elapsed = time.time() - t_total
    log(f"")
    log(f"=== META GENERATION COMPLETE ===")
    log(f"Total time:    {elapsed:.1f}s")
    log(f"Session:       {session_dir}")
    log(f"Meta dir:      {meta_dir}")
    log(f"Thumbnails:    {len(thumbnail_paths)}")
    log(f"Titles:        {titles_path}")
    log(f"Descriptions:  {desc_path}")
    log(f"Tags:          {tags_path}")
    log(f"Final:         {final_path}")
    if pdf_ok:
        log(f"PDF Report:    {pdf_path}")
    log(f"Raw JSON:      {meta_raw_path}")
    if thread_b_result.get("error"):
        log(f"Thread B error: {thread_b_result['error']}")


if __name__ == "__main__":
    main()
