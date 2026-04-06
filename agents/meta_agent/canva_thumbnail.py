"""
canva_thumbnail.py — генерация превью через Canva AI + ImgBB

Флоу:
  base64_image (PixelAgent)
    → ImgBB upload → public URL
    → Canva upload-asset-from-url → asset_id
    → Canva generate-design (youtube_thumbnail) × 4 варианта
    → выбираем лучший
    → create-design-from-candidate → design_id + edit_url
    → export-design → PNG bytes → thumbnail.png

Если Canva недоступна — возвращает None (фоллбэк на PIL).
"""

from __future__ import annotations

import base64
import io
import time
from pathlib import Path
from typing import Optional

import requests
from PIL import Image


# ─── ImgBB ────────────────────────────────────────────────────────────────��───

def upload_to_imgbb(img: Image.Image, api_key: str, name: str = "thumbnail") -> str | None:
    """Upload PIL image to ImgBB. Returns public URL or None on error."""
    try:
        buf = io.BytesIO()
        img.convert("RGB").save(buf, format="PNG", optimize=True)
        b64 = base64.b64encode(buf.getvalue()).decode()

        r = requests.post(
            "https://api.imgbb.com/1/upload",
            data={"key": api_key, "image": b64, "name": name},
            timeout=30,
        )
        r.raise_for_status()
        j = r.json()
        if j.get("success"):
            return j["data"]["url"]
        print(f"  [ImgBB] Error: {j}", flush=True)
        return None
    except Exception as e:
        print(f"  [ImgBB] Upload failed: {e}", flush=True)
        return None


# ─── Canva (через MCP — вызывается из meta_generator через subprocess-bridge) ─
# Canva MCP инструменты не доступны напрямую как Python функции.
# Они вызываются через Claude агента. Поэтому canva_thumbnail предоставляет
# только утилиты (imgbb upload), а сами Canva-вызовы делаются в meta_generator
# через прямой вызов MCP tools внутри Claude сессии.
#
# Этот модуль экспортирует:
#   upload_to_imgbb()     — загрузить картинку, получить URL
#   build_canva_prompt()  — собрать промпт для generate-design
#   parse_export_png()    — скачать PNG по URL экспорта

def build_canva_prompt(preview_text: str, channel_style: str = "religion_es") -> str:
    """
    Build a Canva generate-design query for a YouTube thumbnail.

    preview_text: e.g. "TE EXTRAÑO" or "DETENTE — ESTO ES URGENTE"
    """
    text_escaped = preview_text.strip().upper()

    return (
        f'YouTube thumbnail for a Spanish Christian motivational channel. '
        f'Background: Jesus Christ figure on the LEFT side, gentle emotional expression, '
        f'golden glowing halo, soft heavenly clouds, warm golden light rays from above. '
        f'Right side has clear bright sky — space for bold text. '
        f'Bright luminous palette: brilliant whites, warm golds, soft sky blues. '
        f'Cinematic realistic painting style. '
        f'On the RIGHT side, place LARGE bold uppercase white text: "{text_escaped}" '
        f'— impactful font with subtle drop shadow. No other text elements.'
    )


def download_export(url: str) -> bytes | None:
    """Download exported PNG from Canva export URL."""
    try:
        r = requests.get(url, timeout=60)
        r.raise_for_status()
        return r.content
    except Exception as e:
        print(f"  [Canva] Download failed: {e}", flush=True)
        return None


def save_thumbnail_from_url(export_url: str, dest_path: Path) -> bool:
    """Download exported design and save as PNG to dest_path."""
    data = download_export(export_url)
    if data is None:
        return False
    try:
        img = Image.open(io.BytesIO(data)).convert("RGB")
        img.save(str(dest_path), "PNG", optimize=True)
        size_kb = dest_path.stat().st_size // 1024
        print(f"  [Canva] Saved {dest_path.name} ({size_kb}KB)", flush=True)
        return True
    except Exception as e:
        print(f"  [Canva] Save failed: {e}", flush=True)
        return False
