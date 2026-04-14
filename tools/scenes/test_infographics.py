"""
test_infographics.py v3 — DE — Infografiken auf Basis SmartScene.

Запуск:
  manim tools/test_infographics.py CounterPanel   -qh --media_dir tools/manim_out
  manim tools/test_infographics.py HistogramPanel -qh --media_dir tools/manim_out
  manim tools/test_infographics.py InfographicsDemo -qh --media_dir tools/manim_out
"""

import sys
sys.path.insert(0, str(__import__("pathlib").Path(__file__).parent))

from smart_scene import SmartScene
from manim import *

BG     = "#05050f"
ACCENT = "#4fc3f7"
GOLD   = "#ffd54f"
DIM    = "#546e7a"
WHITE  = "#e8eaf6"
RED    = "#ef5350"
GREEN  = "#66bb6a"
FONT   = "Arial"
FW = config.frame_width
FH = config.frame_height


def _fmt(n: float) -> str:
    return f"{int(n):,}".replace(",", ".")


# ═══════════════════════════════════════════════════════════════════════════════
# 1. CounterPanel
# ═══════════════════════════════════════════════════════════════════════════════

class CounterPanel(SmartScene):
    def construct(self):
        self.camera.background_color = BG
        TAG    = "WELTRAUMFAKT"
        LABEL  = "Entfernung bis Alpha Centauri"
        TARGET = 4_240_000_000_000
        UNIT   = "Kilometer  ≈  4,24 Lichtjahre"

        pw, ph = FW * 0.6, FH * 0.48
        panel = RoundedRectangle(
            corner_radius=0.18, width=pw, height=ph,
            fill_color="#07080f", fill_opacity=0.97,
            stroke_color=ACCENT, stroke_width=1.6)
        glow = RoundedRectangle(
            corner_radius=0.18, width=pw+0.16, height=ph+0.16,
            fill_opacity=0, stroke_color=ACCENT, stroke_width=12, stroke_opacity=0.07)
        panel_group = VGroup(panel, glow)

        cy       = 0.0
        tag_lbl  = Text(TAG,   font=FONT, font_size=13, color=ACCENT, weight=BOLD)
        div      = Line(LEFT*(pw/2-0.45), RIGHT*(pw/2-0.45), color=DIM, stroke_width=0.7)
        main_lbl = Text(LABEL, font=FONT, font_size=16, color=WHITE)
        unit_lbl = Text(UNIT,  font=FONT, font_size=13, color=DIM)
        tag_lbl .move_to([0, cy + ph/2 - 0.45, 0])
        div     .move_to([0, cy + ph/2 - 0.80, 0])
        main_lbl.move_to([0, cy + 0.22, 0])
        unit_lbl.move_to([0, cy - ph/2 + 0.42, 0])

        tracker = ValueTracker(0)
        counter = always_redraw(lambda: Text(
            _fmt(tracker.get_value()), font=FONT, font_size=56, color=GOLD,
        ).move_to([0, cy - 0.22, 0]))

        panel_group.shift(DOWN * (FH * 0.75))
        content = VGroup(tag_lbl, div, main_lbl, unit_lbl).set_opacity(0)
        self.add(panel_group, content, counter)

        # Карточка влетает
        self.go(panel_group.animate.shift(UP * (FH * 0.75)), dur=0.52, sfx="whoosh_fast")
        self.go(tag_lbl.animate.set_opacity(1), div.animate.set_opacity(1),
                main_lbl.animate.set_opacity(1), unit_lbl.animate.set_opacity(1), dur=0.5)
        # Счёт
        self.go(tracker.animate.set_value(TARGET), dur=2.1, rf=rush_into,
                sfx="tick", sfx_n=25)
        # Финал
        self.hold(0.2, sfx="done")
        self.hold(2.5)
        self.go(Group(*self.mobjects).animate.shift(DOWN * (FH * 0.75)).set_opacity(0),
                dur=0.40, rf=rush_into, sfx="whoosh")


# ═══════════════════════════════════════════════════════════════════════════════
# 2. HistogramPanel
# ═══════════════════════════════════════════════════════════════════════════════

class HistogramPanel(SmartScene):
    def construct(self):
        self.camera.background_color = BG

        DATA = [
            ("α Centauri",  4.24, ACCENT),
            ("Barnard",     5.96, GREEN),
            ("Wolf 359",    7.78, GOLD),
            ("Sirius",      8.61, RED),
        ]
        TITLE   = "NÄCHSTE STERNE  (Lichtjahre)"
        MAX_H   = 3.4
        BAR_W   = 1.25
        STEP    = 2.1
        Y_BASE  = -2.0
        max_val = max(v for _, v, _ in DATA)
        n       = len(DATA)

        title = Text(TITLE, font=FONT, font_size=13, color=ACCENT, weight=BOLD)
        title.move_to([0, FH/2 - 0.6, 0]).set_opacity(0)
        ax_w = (n-1)*STEP + BAR_W + 0.8
        axis = Line(LEFT*ax_w/2, RIGHT*ax_w/2, color=DIM, stroke_width=1.2)
        axis.move_to([0, Y_BASE - 0.02, 0]).set_opacity(0)
        self.add(title, axis)

        self.go(title.animate.set_opacity(1), axis.animate.set_opacity(1),
                dur=0.35, sfx="whoosh_fast")

        for i, (name, val, color) in enumerate(DATA):
            x = -(n-1)*STEP/2 + i*STEP
            h = MAX_H * val / max_val

            bar = Rectangle(width=BAR_W, height=0.01,
                            fill_color=color, fill_opacity=0.85, stroke_width=0)
            bar.move_to([x, Y_BASE, 0]).align_to(
                Line(LEFT, RIGHT).move_to([x, Y_BASE, 0]), DOWN)

            name_lbl = Text(name, font=FONT, font_size=11, color=DIM)
            name_lbl.move_to([x, Y_BASE - 0.44, 0]).set_opacity(0)
            val_lbl  = Text(f"{val} Lj.", font=FONT, font_size=14, color=color, weight=BOLD)
            val_lbl.move_to([x, Y_BASE + h + 0.32, 0]).set_opacity(0)
            self.add(bar, name_lbl, val_lbl)

            full_bar = Rectangle(width=BAR_W, height=h,
                                 fill_color=color, fill_opacity=0.85, stroke_width=0)
            full_bar.move_to([x, Y_BASE + h/2, 0])

            # Бары растут молча — whoosh на каждый был лишним
            self.go(Transform(bar, full_bar), name_lbl.animate.set_opacity(1), dur=0.44)
            self.go(val_lbl.animate.set_opacity(1), dur=0.18)

        # done когда все бары на месте
        self.hold(0.1, sfx="done")
        self.hold(2.8)
        self.go(Group(*self.mobjects).animate.set_opacity(0).shift(DOWN*0.3),
                dur=0.4, sfx="whoosh")


# ═══════════════════════════════════════════════════════════════════════════════
# 3. InfographicsDemo — Zähler + Histogramm
# ═══════════════════════════════════════════════════════════════════════════════

class InfographicsDemo(SmartScene):
    def construct(self):
        self.camera.background_color = BG
        self._counter_block()
        self.hold(0.4)
        self._histogram_block()

    def _counter_block(self):
        TAG    = "ALTER DES UNIVERSUMS"
        TARGET = 13_800_000_000

        pw, ph = FW * 0.52, FH * 0.44
        panel = RoundedRectangle(corner_radius=0.18, width=pw, height=ph,
                                 fill_color="#07080f", fill_opacity=0.97,
                                 stroke_color=ACCENT, stroke_width=1.6)
        glow = RoundedRectangle(corner_radius=0.18, width=pw+0.16, height=ph+0.16,
                                fill_opacity=0, stroke_color=ACCENT,
                                stroke_width=12, stroke_opacity=0.08)
        pg = VGroup(panel, glow)

        tag_lbl  = Text(TAG,    font=FONT, font_size=13, color=ACCENT, weight=BOLD)
        div      = Line(LEFT*(pw/2-0.5), RIGHT*(pw/2-0.5), color=DIM, stroke_width=0.7)
        unit_lbl = Text("JAHRE", font=FONT, font_size=16, color=DIM)
        tag_lbl .move_to([0,  ph/2 - 0.44, 0])
        div     .move_to([0,  ph/2 - 0.80, 0])
        unit_lbl.move_to([0, -ph/2 + 0.44, 0])

        tracker = ValueTracker(0)
        counter = always_redraw(lambda: Text(
            _fmt(tracker.get_value()), font=FONT, font_size=54, color=GOLD,
        ).move_to([0, -0.15, 0]))

        pg.shift(DOWN * (FH * 0.75))
        content = VGroup(tag_lbl, div, unit_lbl).set_opacity(0)
        self.add(pg, content, counter)

        self.go(pg.animate.shift(UP * (FH * 0.75)), dur=0.5, sfx="whoosh_fast")
        self.go(tag_lbl.animate.set_opacity(1), div.animate.set_opacity(1), dur=0.28)
        self.go(unit_lbl.animate.set_opacity(1), dur=0.2)
        self.go(tracker.animate.set_value(TARGET), dur=2.0, rf=rush_into,
                sfx="tick", sfx_n=28)
        self.hold(0.2, sfx="done")
        self.hold(2.2)
        self.go(Group(*self.mobjects).animate.shift(DOWN * (FH * 0.75)).set_opacity(0),
                dur=0.38, rf=rush_into, sfx="whoosh")

    def _histogram_block(self):
        DATA = [
            ("α Centauri",  4.24, ACCENT),
            ("Barnard",     5.96, GREEN),
            ("Wolf 359",    7.78, GOLD),
            ("Sirius",      8.61, RED),
        ]
        TITLE   = "NÄCHSTE STERNE  (Lichtjahre)"
        MAX_H   = 3.4
        BAR_W   = 1.25
        STEP    = 2.1
        Y_BASE  = -2.0
        max_val = max(v for _, v, _ in DATA)
        n       = len(DATA)

        title = Text(TITLE, font=FONT, font_size=13, color=ACCENT, weight=BOLD)
        title.move_to([0, FH/2 - 0.6, 0]).set_opacity(0)
        ax_w = (n-1)*STEP + BAR_W + 0.8
        axis = Line(LEFT*ax_w/2, RIGHT*ax_w/2, color=DIM, stroke_width=1.2)
        axis.move_to([0, Y_BASE - 0.02, 0]).set_opacity(0)
        self.add(title, axis)

        self.go(title.animate.set_opacity(1), axis.animate.set_opacity(1),
                dur=0.35, sfx="whoosh_fast")

        for i, (name, val, color) in enumerate(DATA):
            x = -(n-1)*STEP/2 + i*STEP
            h = MAX_H * val / max_val
            bar = Rectangle(width=BAR_W, height=0.01,
                            fill_color=color, fill_opacity=0.85, stroke_width=0)
            bar.move_to([x, Y_BASE, 0]).align_to(
                Line(LEFT, RIGHT).move_to([x, Y_BASE, 0]), DOWN)
            name_lbl = Text(name, font=FONT, font_size=11, color=DIM)
            name_lbl.move_to([x, Y_BASE - 0.44, 0]).set_opacity(0)
            val_lbl  = Text(f"{val} Lj.", font=FONT, font_size=14, color=color, weight=BOLD)
            val_lbl.move_to([x, Y_BASE + h + 0.32, 0]).set_opacity(0)
            self.add(bar, name_lbl, val_lbl)
            full_bar = Rectangle(width=BAR_W, height=h,
                                 fill_color=color, fill_opacity=0.85, stroke_width=0)
            full_bar.move_to([x, Y_BASE + h/2, 0])
            self.go(Transform(bar, full_bar), name_lbl.animate.set_opacity(1), dur=0.44)
            self.go(val_lbl.animate.set_opacity(1), dur=0.18)

        self.hold(0.1, sfx="done")
        self.hold(3.0)
        self.go(Group(*self.mobjects).animate.set_opacity(0), dur=0.4, sfx="whoosh")
