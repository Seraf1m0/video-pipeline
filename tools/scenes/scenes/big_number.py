"""
BigNumber — motion design scene.

Panel slides up from bottom, counter counts up inside with rush_into curve.
Tags: число, статистика, расстояние, температура, возраст, космос
Duration: 6-14s (default 9s)

Usage:
    manim tools/scenes/scenes/big_number.py BigNumber -qh --media_dir tools/manim_out
"""

import sys
from pathlib import Path

from manim import *

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from smart_scene import SmartScene

BG     = "#04040e"
ACCENT = "#4fc3f7"
GOLD   = "#ffd54f"
DIM    = "#546e7a"
WHITE  = "#e8eaf6"

try:
    Text("x", font="Montserrat")
    FONT = "Montserrat"
except Exception:
    FONT = "Arial"


def _fmt(n: float) -> str:
    return f"{int(n):,}".replace(",", ".")


def rush_into(t: float) -> float:
    return 1 - (1 - t) ** 3


class BigNumber(SmartScene):
    number:    int | float = 40_208_000_000_000
    unit:      str         = "KILOMETER"
    tag:       str         = "ENTFERNUNG ZUM NÄCHSTEN STERN"
    total_dur: float       = 9.0

    def construct(self):
        dur   = self.total_dur
        FW    = config.frame_width
        FH    = config.frame_height

        # Timing
        SLIDE_IN  = 0.5    # панель въезжает
        REVEAL    = 0.55   # тег + разделитель + юнит
        HOLD_END  = 1.2    # стоп после счёта
        SLIDE_OUT = 0.5    # выход
        count_dur = max(1.0, dur - SLIDE_IN - REVEAL - HOLD_END - SLIDE_OUT)
        tick_n    = max(10, int(count_dur * 10))

        self.camera.background_color = BG

        # ── Панель ───────────────────────────────────────────────────────────
        PW, PH = FW * 0.72, FH * 0.52
        panel = RoundedRectangle(
            corner_radius=0.18, width=PW, height=PH,
            fill_color="#07080f", fill_opacity=0.97,
            stroke_color=ACCENT, stroke_width=1.6,
        )
        glow = RoundedRectangle(
            corner_radius=0.18, width=PW + 0.18, height=PH + 0.18,
            fill_opacity=0, stroke_color=ACCENT,
            stroke_width=14, stroke_opacity=0.07,
        )
        pg = VGroup(panel, glow).move_to(ORIGIN)

        # ── Контент ──────────────────────────────────────────────────────────
        tag_lbl = Text(self.tag, font=FONT, font_size=13,
                       color=ACCENT, weight=BOLD)
        tag_lbl.move_to([0, PH / 2 - 0.44, 0])

        div = Line(LEFT * (PW / 2 - 0.45), RIGHT * (PW / 2 - 0.45),
                   color=DIM, stroke_width=0.7)
        div.move_to([0, PH / 2 - 0.82, 0])

        unit_lbl = Text(self.unit, font=FONT, font_size=16, color=DIM)
        unit_lbl.move_to([0, -PH / 2 + 0.44, 0])

        tracker = ValueTracker(0)
        counter = always_redraw(lambda: Text(
            _fmt(tracker.get_value() * self.number),
            font=FONT, font_size=68, color=GOLD, weight=BOLD,
        ).move_to([0, -0.1, 0]))

        # ── Старт: всё внизу экрана ───────────────────────────────────────
        pg.shift(DOWN * FH * 0.78)
        for mob in [tag_lbl, div, unit_lbl]:
            mob.set_opacity(0)

        self.add(pg, tag_lbl, div, unit_lbl, counter)

        # ── 1. Панель въезжает снизу ──────────────────────────────────────
        self.go(pg.animate.shift(UP * FH * 0.78),
                dur=SLIDE_IN, rf=smooth, sfx="whoosh_fast")

        # ── 2. Reveal контента ────────────────────────────────────────────
        self.go(tag_lbl.animate.set_opacity(1),
                div.animate.set_opacity(1),
                dur=0.28, rf=smooth)
        self.go(unit_lbl.animate.set_opacity(1), dur=0.22, rf=smooth)

        # ── 3. Счёт ───────────────────────────────────────────────────────
        self.go(tracker.animate.set_value(1.0),
                dur=count_dur, rf=rush_into,
                sfx="tick", sfx_n=tick_n)

        # ── 4. Done ───────────────────────────────────────────────────────
        self.hold(0.15, sfx="done")
        self.hold(HOLD_END - 0.15)

        # ── 5. Выход вниз ─────────────────────────────────────────────────
        self.go(
            pg.animate.shift(DOWN * FH * 0.78),
            VGroup(tag_lbl, div, unit_lbl, counter).animate
                .shift(DOWN * FH * 0.78).set_opacity(0),
            dur=SLIDE_OUT, rf=rush_into, sfx="whoosh",
        )
