"""
style_engine.py — загружает channel_styles.json и возвращает стиль для канала.

Использование:
    from style_engine import get_channel_style
    style = get_channel_style("channel_001_cosmos_de")
    clip_transition = style["clip_transition"]          # "fade" | "pixelize" | "slideleft" | ...
    intro_transition = style["intro_transition"]         # "smooth_zoom" | "whip_pan"
    sfx_enabled = style["sfx_enabled"]                   # bool
"""

import json
from pathlib import Path

# ─── Пути ────────────────────────────────────────────────────────────────────

_BASE    = Path(__file__).resolve().parent.parent.parent
_CONFIG  = _BASE / "config" / "channel_styles.json"

# ─── Дефолтный стиль (если канал не найден в конфиге) ────────────────────────

_DEFAULTS = {
    "name":                    "Unknown",
    "intro_transition":        "smooth_zoom",
    "clip_transition":         "fade",
    "clip_transition_duration": 0.32,
    "sfx_enabled":             False,
    "sfx_whoosh_chance":       0.0,
    "subtitle_size_offset":    12,
    "subtitle_rise_extra_px":  15,
    "color_grade":             "warm_cinematic",
}

# ─── Кэш конфига ─────────────────────────────────────────────────────────────

_STYLES: dict | None = None


def _load() -> dict:
    global _STYLES
    if _STYLES is None:
        try:
            _STYLES = json.loads(_CONFIG.read_text(encoding="utf-8"))
        except Exception:
            _STYLES = {}
    return _STYLES


def get_channel_style(channel_id: str) -> dict:
    """Вернуть стиль для канала. При отсутствии — дефолт."""
    styles = _load()

    # Прямое совпадение
    if channel_id in styles:
        return {**_DEFAULTS, **styles[channel_id]}

    # Попытка по суффиксу _de / _fr
    for key, val in styles.items():
        if channel_id.endswith("_de") and key.endswith("_de"):
            return {**_DEFAULTS, **val}
        if channel_id.endswith("_fr") and key.endswith("_fr"):
            return {**_DEFAULTS, **val}

    return dict(_DEFAULTS)


def get_intro_transition_fn(style: dict):
    """
    Вернуть функцию перехода интро→видеоряд из transitions.py.
    smooth_zoom → smooth_zoom_transition
    whip_pan    → whip_pan_transition
    (fallback)  → intro_to_main_transition
    """
    from transitions import (
        smooth_zoom_transition,
        whip_pan_transition,
        zoom_blur_transition,
        intro_to_main_transition,
    )
    name = style.get("intro_transition", "smooth_zoom")
    return {
        "smooth_zoom": smooth_zoom_transition,
        "whip_pan":    whip_pan_transition,
        "zoom_blur":   zoom_blur_transition,
    }.get(name, intro_to_main_transition)
