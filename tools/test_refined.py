"""
test_refined.py — Эталонные сцены на базе SmartScene.

Запуск:
  manim tools/test_refined.py ArcProgressV2  -qh --media_dir tools/manim_out
  manim tools/test_refined.py CounterV3      -qh --media_dir tools/manim_out
  manim tools/test_refined.py ShockwaveV2    -qh --media_dir tools/manim_out
"""

import sys
sys.path.insert(0, str(__import__("pathlib").Path(__file__).parent))

from smart_scene import SmartScene
from manim import *

BG    = "#05050f"
ACC   = "#4fc3f7"
GOLD  = "#ffd54f"
DIM   = "#546e7a"
WHITE = "#eceff1"
FONT  = "Arial"
FW    = config.frame_width
FH    = config.frame_height


# ═══════════════════════════════════════════════════════════════════════════════
# 1. ARC PROGRESS V2 — круговой прогресс
# ═══════════════════════════════════════════════════════════════════════════════

class ArcProgressV2(SmartScene):
    def construct(self):
        self.camera.background_color = BG
        PERCENT = 94.3

        label = Text("ТЁМНАЯ МАТЕРИЯ + ТЁМНАЯ ЭНЕРГИЯ",
                     font=FONT, font_size=13, color=DIM)
        label.move_to([0, 2.6, 0]).set_opacity(0)

        arc_bg = Arc(radius=2.0, angle=TAU*0.78, start_angle=PI*0.61,
                     color=DIM, stroke_width=14, stroke_opacity=0.22)

        tracker = ValueTracker(0)

        def _arc():
            return Arc(radius=2.0, angle=TAU*0.78*tracker.get_value()/100,
                       start_angle=PI*0.61, stroke_width=14,
                       ).set_stroke(color=[ACC, GOLD], width=14)

        arc_fill = always_redraw(_arc)
        dot = always_redraw(lambda: (
            Dot(_arc().get_end(), radius=0.12, color=GOLD)
            if tracker.get_value() > 0.5 else VMobject()
        ))
        num = always_redraw(lambda: Text(
            f"{tracker.get_value():.1f}%", font=FONT, font_size=82, color=WHITE,
        ).move_to(ORIGIN))
        sublabel = Text("состав вселенной", font=FONT, font_size=14, color=DIM)
        sublabel.move_to([0, -0.85, 0]).set_opacity(0)

        self.add(arc_bg, arc_fill, dot, num, label, sublabel)

        # 1. Лейбл появляется
        self.go(label.animate.set_opacity(1), dur=0.5, rf=smooth, sfx="whoosh_fast")
        # 2. Дуга заполняется — тики
        self.go(tracker.animate.set_value(PERCENT), sublabel.animate.set_opacity(0.7),
                dur=2.2, rf=smooth, sfx="tick", sfx_n=12)
        # 3. Финал
        self.go(num.animate.set_color(GOLD), dur=0.2, rf=linear, sfx="done")
        # 4. Hold + exit
        self.hold(2.5)
        self.go(Group(*self.mobjects).animate.set_opacity(0).shift(UP*0.4),
                dur=0.45, rf=smooth, sfx="whoosh")


# ═══════════════════════════════════════════════════════════════════════════════
# 2. COUNTER V3 — счётчик
# ═══════════════════════════════════════════════════════════════════════════════

class CounterV3(SmartScene):
    def construct(self):
        self.camera.background_color = BG
        TARGET = 13_800_000_000

        def _fmt(n): return f"{int(n):,}".replace(",", " ")

        tag = Text("ВОЗРАСТ ВСЕЛЕННОЙ", font=FONT, font_size=13, color=DIM, weight=BOLD)
        tag.move_to([0, 1.6, 0]).set_opacity(0)

        tracker = ValueTracker(0)
        num = always_redraw(lambda: Text(
            _fmt(tracker.get_value()), font=FONT, font_size=96, color=WHITE,
        ).move_to([0, 0.15, 0]))

        line = Line(LEFT*3.5, RIGHT*3.5, color=DIM, stroke_width=0.8)
        line.move_to([0, -0.85, 0]).set_opacity(0)
        unit = Text("ЛЕТ", font=FONT, font_size=14, color=ACC)
        unit.move_to([0, -1.3, 0]).set_opacity(0)

        self.add(tag, num, line, unit)

        # 1. Появление
        self.go(tag.animate.set_opacity(1), line.animate.set_opacity(1),
                unit.animate.set_opacity(1), dur=0.6, rf=smooth, sfx="whoosh_fast")
        # 2. Счёт с тиками
        self.go(tracker.animate.set_value(TARGET), dur=2.2, rf=rush_into,
                sfx="tick", sfx_n=18)
        # 3. Финал
        self.go(num.animate.set_color(GOLD), unit.animate.set_color(GOLD),
                dur=0.2, rf=linear, sfx="done", sfx_delta=+2)
        # 4. Hold + exit
        self.hold(2.5)
        self.go(Group(*self.mobjects).animate.shift(RIGHT*FW*1.1).set_opacity(0),
                dur=0.4, rf=rush_into, sfx="whoosh")


# ═══════════════════════════════════════════════════════════════════════════════
# 3. SHOCKWAVE V2 — удар + волны
# ═══════════════════════════════════════════════════════════════════════════════

class ShockwaveV2(SmartScene):
    def construct(self):
        self.camera.background_color = BG
        HEADLINE = "4 246 000 000 000 км"
        SUB      = "до ближайшей звезды"
        TAG      = "АЛЬФА ЦЕНТАВРА"

        tag_t = Text(TAG, font=FONT, font_size=12, color=ACC, weight=BOLD)
        tag_t.move_to([0, 2.3, 0]).set_opacity(0)
        line = Line(LEFT*4.0, RIGHT*4.0, color=DIM, stroke_width=0.7)
        line.move_to([0, 1.75, 0]).set_opacity(0)
        main_t = Text(HEADLINE, font=FONT, font_size=68, color=WHITE, weight=BOLD)
        main_t.move_to([0, 0.25, 0]).set_opacity(0)
        sub_t = Text(SUB, font=FONT, font_size=17, color=DIM)
        sub_t.move_to([0, -0.85, 0]).set_opacity(0)

        self.add(tag_t, line, main_t, sub_t)

        # 1. Тег + линия
        self.go(tag_t.animate.set_opacity(1), line.animate.set_opacity(0.4),
                dur=0.5, rf=smooth, sfx="whoosh_fast")
        # 2. Число врывается
        self.go(main_t.animate.set_opacity(1), dur=0.15, rf=rush_from, sfx="whoosh_big")

        rings = []
        for i in range(3):
            r = Circle(radius=0.05, color=ACC,
                       stroke_width=max(0.5, 2.5 - i*0.7),
                       stroke_opacity=0.5 - i*0.1, fill_opacity=0)
            r.move_to(main_t.get_center())
            self.add(r)
            rings.append(r)

        self.go(*[r.animate.scale(55 - i*10).set_stroke(opacity=0)
                  for i, r in enumerate(rings)],
                sub_t.animate.set_opacity(0.75), dur=1.0, rf=rush_from)

        # 3. Золото
        self.go(main_t.animate.set_color(GOLD), dur=0.2, rf=linear, sfx="done")
        # 4. Hold + exit
        self.hold(2.5)
        self.go(Group(*self.mobjects).animate.shift(UP*FH*0.7).set_opacity(0),
                dur=0.45, rf=smooth, sfx="whoosh")
