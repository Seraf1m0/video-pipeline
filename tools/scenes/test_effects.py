"""
test_effects.py v3 — DE — Effektanimationen auf Basis SmartScene.

Запуск:
  manim tools/test_effects.py ArcProgress      -qh --media_dir tools/manim_out
  manim tools/test_effects.py ShockwaveReveal  -qh --media_dir tools/manim_out
  manim tools/test_effects.py ScaleCompare     -qh --media_dir tools/manim_out
  manim tools/test_effects.py GlitchReveal     -qh --media_dir tools/manim_out
  manim tools/test_effects.py CosmosTimeline   -qh --media_dir tools/manim_out
"""

import sys
sys.path.insert(0, str(__import__("pathlib").Path(__file__).parent))

from smart_scene import SmartScene
from manim import *
import numpy as np
import random

BG    = "#05050f"
ACC   = "#4fc3f7"
GOLD  = "#ffd54f"
DIM   = "#37474f"
WHITE = "#e8eaf6"
CYAN2 = "#00e5ff"
GREEN = "#69f0ae"
RED   = "#ff5252"
PURP  = "#ce93d8"
FONT  = "Arial"
FW    = config.frame_width
FH    = config.frame_height


# ═══════════════════════════════════════════════════════════════════════════════
# 1. ArcProgress — Kreisfortschritt
# ═══════════════════════════════════════════════════════════════════════════════

class ArcProgress(SmartScene):
    def construct(self):
        self.camera.background_color = BG
        PERCENT   = 94.3

        arc_bg = Arc(radius=2.2, angle=TAU*0.75, start_angle=PI*0.625,
                     color=DIM, stroke_width=18, stroke_opacity=0.35)

        tracker  = ValueTracker(0)

        def make_arc(pct):
            return Arc(radius=2.2, angle=TAU*0.75*(pct/100), start_angle=PI*0.625,
                       color=ACC, stroke_width=18,
                       ).set_stroke(color=[ACC, CYAN2, GOLD], width=18)

        arc_fill = always_redraw(lambda: make_arc(tracker.get_value()))
        arc_glow = always_redraw(lambda: Arc(
            radius=2.2, angle=TAU*0.75*(tracker.get_value()/100),
            start_angle=PI*0.625, color=ACC, stroke_width=40, stroke_opacity=0.08,
        ))
        num = always_redraw(lambda: Text(
            f"{tracker.get_value():.1f}%", font=FONT, font_size=72, color=WHITE,
        ).move_to([0, 0.25, 0]))

        label_mid = Text("Dunkle Materie\n+ Dunkle Energie",
                         font=FONT, font_size=16, color=DIM)
        label_mid.move_to([0, -0.65, 0]).set_opacity(0)
        label_top = Text("AUFBAU DES UNIVERSUMS",
                         font=FONT, font_size=14, color=ACC, weight=BOLD)
        label_top.move_to([0, 3.0, 0]).set_opacity(0)
        dot = always_redraw(lambda: (
            Dot(point=arc_fill.get_end(), radius=0.14, color=GOLD)
            if tracker.get_value() > 0.5 else VMobject()
        ))
        self.add(arc_bg, arc_glow, arc_fill, dot, num, label_mid, label_top)

        # Лейбл появляется — минимальный whoosh_fast
        self.go(label_top.animate.set_opacity(1), dur=0.6, rf=smooth, sfx="whoosh_fast")
        # Дуга заполняется с тиками
        self.go(tracker.animate.set_value(PERCENT), label_mid.animate.set_opacity(1),
                dur=2.2, rf=smooth, sfx="tick", sfx_n=12)
        # Финал
        self.go(num.animate.set_color(GOLD), dur=0.3, sfx="done")
        self.hold(2.8)
        self.go(Group(*self.mobjects).animate.set_opacity(0).scale(0.85),
                dur=0.4, sfx="whoosh")


# ═══════════════════════════════════════════════════════════════════════════════
# 2. ShockwaveReveal — Schockwellen-Enthüllung
# ═══════════════════════════════════════════════════════════════════════════════

class ShockwaveReveal(SmartScene):
    def construct(self):
        self.camera.background_color = BG
        HEADLINE = "4 240 000 000 000"
        UNIT     = "Kilometer bis Alpha Centauri"
        TAG      = "NÄCHSTER STERN"

        tag_t  = Text(TAG,      font=FONT, font_size=13, color=ACC, weight=BOLD)
        tag_t.move_to([0, 1.9, 0]).set_opacity(0)
        main_t = Text(HEADLINE, font=FONT, font_size=78, color=WHITE, weight=BOLD)
        main_t.move_to([0, 0.1, 0]).set_opacity(0)
        unit_t = Text(UNIT,     font=FONT, font_size=16, color=DIM)
        unit_t.move_to([0, -1.1, 0]).set_opacity(0)
        div    = Line(LEFT*5.5, RIGHT*5.5, color=DIM, stroke_width=0.7)
        div.move_to([0, -0.55, 0]).set_opacity(0)
        self.add(tag_t, main_t, unit_t, div)

        # Тег + линия — лёгкий вход
        self.go(tag_t.animate.set_opacity(1), div.animate.set_opacity(0.5),
                dur=1.0, rf=smooth, sfx="whoosh_fast")
        # Число — удар
        self.go(main_t.animate.set_opacity(1), dur=0.18, rf=rush_from, sfx="whoosh_big")

        N_WAVES = 5
        waves = [Circle(radius=0.01, color=ACC,
                        stroke_width=max(1, 8 - r*1.5),
                        stroke_opacity=0.6, fill_opacity=0)
                 for r in range(N_WAVES)]
        for w in waves:
            self.add(w)

        self.go(*[w.animate.scale(80).set_stroke(opacity=0) for w in waves],
                unit_t.animate.set_opacity(1), dur=1.4, rf=rush_from)

        # Золото
        self.go(main_t.animate.set_color(GOLD), dur=0.25, sfx="done")
        self.hold(2.8)
        self.go(Group(*self.mobjects).animate.shift(UP*FH*0.8).set_opacity(0),
                dur=0.4, sfx="whoosh")


# ═══════════════════════════════════════════════════════════════════════════════
# 3. ScaleCompare — Grössenvergleich
# ═══════════════════════════════════════════════════════════════════════════════

class ScaleCompare(SmartScene):
    def construct(self):
        self.camera.background_color = BG

        OBJECTS = [
            ("Erde",    0.20, "#4fc3f7", "1×"),
            ("Neptun",  0.38, "#5c6bc0", "4×"),
            ("Jupiter", 0.70, "#ff8f00", "11×"),
            ("Sonne",   1.55, "#ffd54f", "109×"),
        ]
        TITLE = "GRÖSSENVERGLEICH"
        title = Text(TITLE, font=FONT, font_size=14, color=ACC, weight=BOLD)
        title.move_to([0, 3.3, 0]).set_opacity(0)
        self.add(title)

        self.go(title.animate.set_opacity(1), dur=0.8, rf=smooth, sfx="whoosh_fast")

        BASE_Y = -2.8
        n  = len(OBJECTS)
        xs = np.linspace(-(n-1)*2.8/2, (n-1)*2.8/2, n)

        for i, ((name, r, color, mult), x) in enumerate(zip(OBJECTS, xs)):
            circle = Circle(radius=r, color=color,
                            fill_color=color, fill_opacity=0.18, stroke_width=2.5)
            circle.move_to([x, BASE_Y + r, 0]).scale(0.01)
            name_lbl = Text(name, font=FONT, font_size=12, color=WHITE)
            name_lbl.move_to([x, BASE_Y - 0.35, 0]).set_opacity(0)
            mult_lbl = Text(mult, font=FONT, font_size=14, color=color, weight=BOLD)
            mult_lbl.move_to([x, BASE_Y + r*2 + 0.28, 0]).set_opacity(0)
            self.add(circle, name_lbl, mult_lbl)

            sfx_i = "done" if i == len(OBJECTS) - 1 else "tick"
            self.go(circle.animate.scale(100), dur=0.55, rf=smooth, sfx=sfx_i)
            self.go(name_lbl.animate.set_opacity(1), mult_lbl.animate.set_opacity(1), dur=0.22)

        self.hold(0.05)
        self.hold(2.8)
        self.go(Group(*self.mobjects).animate.set_opacity(0).shift(DOWN*0.4),
                dur=0.45, sfx="whoosh")


# ═══════════════════════════════════════════════════════════════════════════════
# 4. GlitchReveal — Glitch-Enthüllung
# ═══════════════════════════════════════════════════════════════════════════════

class GlitchReveal(SmartScene):
    def construct(self):
        self.camera.background_color = BG
        HEADLINE = "DUNKLE MATERIE"
        SUB      = "macht 27% des Universums aus"
        TAG      = "WISSENSCHAFTLICH UNBEKANNT"

        tag_t = Text(TAG, font=FONT, font_size=12, color=RED, weight=BOLD)
        tag_t.move_to([0, 2.2, 0]).set_opacity(0)
        self.add(tag_t)

        self.go(tag_t.animate.set_opacity(1), dur=0.3, sfx="whoosh_fast")

        main_pos = [0, 0.3, 0]

        def glitch_frame(offset_x, offset_y, color, opacity):
            t = Text(HEADLINE, font=FONT, font_size=70, color=color)
            t.move_to([main_pos[0]+offset_x, main_pos[1]+offset_y, 0])
            t.set_opacity(opacity)
            return t

        configs = [
            ( 0.08, 0.0, RED,   0.6),
            (-0.06, 0.0, ACC,   0.5),
            ( 0.0,  0.0, WHITE, 0.9),
        ]
        glitch_layers = VGroup(*[glitch_frame(ox, oy, col, op) for ox, oy, col, op in configs])
        self.add(glitch_layers)

        # Глитч-фреймы с тиками — звук + картинка идеально сочетаются
        for _ in range(7):
            new_layers = VGroup(*[
                glitch_frame(ox + random.uniform(-0.15, 0.15),
                             oy + random.uniform(-0.05, 0.05), col, op)
                for ox, oy, col, op in configs
            ])
            self.go(Transform(glitch_layers, new_layers), dur=0.06, rf=linear, sfx="tick")

        # Стабилизация
        final_text = Text(HEADLINE, font=FONT, font_size=70, color=WHITE, weight=BOLD)
        final_text.move_to(main_pos)
        self.go(Transform(glitch_layers, final_text), dur=0.2, sfx="done")

        sub_t = Text(SUB, font=FONT, font_size=18, color=ACC)
        sub_t.move_to([0, -0.75, 0]).set_opacity(0)
        div = Line(LEFT*4.0, RIGHT*4.0, color=DIM, stroke_width=0.7)
        div.move_to([0, -0.2, 0]).set_opacity(0)
        self.add(sub_t, div)
        self.go(div.animate.set_opacity(0.6), sub_t.animate.set_opacity(1), dur=0.4)
        self.hold(2.8)
        self.go(Group(*self.mobjects).animate.set_opacity(0).shift(LEFT*0.3),
                dur=0.4, sfx="whoosh")


# ═══════════════════════════════════════════════════════════════════════════════
# 5. CosmosTimeline — Zeitstrahl des Universums
# ═══════════════════════════════════════════════════════════════════════════════

class CosmosTimeline(SmartScene):
    def construct(self):
        self.camera.background_color = BG
        TITLE = "GESCHICHTE DES UNIVERSUMS"

        EVENTS = [
            (0.0,  "Urknall",       GOLD,  "vor 13,8 Mrd. J."),
            (0.18, "Erste\nSterne",  ACC,   "vor 13,4 Mrd. J."),
            (0.38, "Milch-\nstraße", GREEN, "vor 10 Mrd. J."),
            (0.62, "Sonnen-\nsystem",WHITE, "vor 4,6 Mrd. J."),
            (0.80, "Leben\nauf Erde",PURP,  "vor 3,8 Mrd. J."),
            (1.0,  "Heute",         RED,   "2025"),
        ]

        LINE_Y  = 0.0
        X_START = -5.8
        X_END   =  5.8

        title = Text(TITLE, font=FONT, font_size=14, color=ACC, weight=BOLD)
        title.move_to([0, 3.1, 0]).set_opacity(0)
        self.add(title)

        self.go(title.animate.set_opacity(1), dur=0.8, rf=smooth, sfx="whoosh_fast")

        timeline = Line([X_START, LINE_Y, 0], [X_START, LINE_Y, 0], color=DIM, stroke_width=2)
        self.add(timeline)
        self.go(timeline.animate.put_start_and_end_on([X_START, LINE_Y, 0], [X_END, LINE_Y, 0]),
                dur=1.2, rf=smooth, sfx="whoosh_fast")

        for i, (frac, name, color, year) in enumerate(EVENTS):
            x     = X_START + frac * (X_END - X_START)
            above = (i % 2 == 0)
            sign  = 1 if above else -1

            tick     = Line([x, LINE_Y-0.15, 0], [x, LINE_Y+0.15, 0],
                            color=color, stroke_width=2.5)
            dot      = Dot([x, LINE_Y, 0], radius=0.1, color=color).scale(0.01)
            name_lbl = Text(name, font=FONT, font_size=11, color=color)
            name_lbl.move_to([x, LINE_Y + sign*1.4, 0]).set_opacity(0)
            year_lbl = Text(year, font=FONT, font_size=9, color=DIM)
            year_lbl.move_to([x, LINE_Y + sign*2.15, 0]).set_opacity(0)
            vline    = DashedLine([x, LINE_Y + sign*0.15, 0], [x, LINE_Y + sign*0.85, 0],
                                  color=color, stroke_width=0.8, dash_length=0.08, stroke_opacity=0.5)
            self.add(tick, dot, name_lbl, year_lbl, vline)

            # tick на каждую точку, done только на последнюю
            sfx = "done" if i == len(EVENTS) - 1 else "tick"
            self.go(dot.animate.scale(100), name_lbl.animate.set_opacity(1),
                    year_lbl.animate.set_opacity(0.7), dur=0.35, rf=smooth, sfx=sfx)

        self.hold(3.0)
        self.go(Group(*self.mobjects).animate.set_opacity(0).shift(DOWN*0.3),
                dur=0.45, sfx="whoosh")
