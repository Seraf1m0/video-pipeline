"""
test_push_panel.py — Push-panel эффект на базе SmartScene.

Запуск:
  manim tools/test_push_panel.py PushPanel -qh --media_dir tools/manim_out
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
FONT   = "Arial"

FW = config.frame_width
FH = config.frame_height

SPLIT_X    = -2.0
FOOTAGE_DX = 3.0
PANEL_W    = 22.0


class PushPanel(SmartScene):
    def construct(self):
        self.camera.background_color = BG

        # Footage placeholder
        footage = Rectangle(width=FW+2, height=FH+2,
                            fill_color="#0d2137", fill_opacity=1, stroke_width=0)
        nebula  = Circle(radius=2.0, color="#1565c0", fill_opacity=0.45,
                         stroke_width=0).shift(RIGHT*2 + UP*0.2)
        nebula2 = Circle(radius=1.2, color="#6a1b9a", fill_opacity=0.35,
                         stroke_width=0).shift(RIGHT*3.5 + DOWN*0.9)
        footage_group = VGroup(footage, nebula, nebula2)
        self.add(footage_group)

        # Panel group
        panel = Rectangle(width=PANEL_W, height=FH+2,
                          fill_color="#000000", fill_opacity=1, stroke_width=0)
        border      = Line(UP*(FH/2+1), DOWN*(FH/2+1), color=ACCENT, stroke_width=2.5)
        border_glow = Line(UP*(FH/2+1), DOWN*(FH/2+1), color=ACCENT,
                           stroke_width=18, stroke_opacity=0.10)

        panel_group = VGroup(panel, border, border_glow)
        border.align_to(panel, RIGHT)
        border_glow.align_to(panel, RIGHT)

        panel_cx_final = SPLIT_X - PANEL_W / 2
        panel_group.move_to([panel_cx_final, 0, 0])
        panel_group.shift(LEFT * (FW * 2))
        self.add(panel_group)

        # Content
        cx = (-FW/2 + SPLIT_X) / 2
        tag       = Text("КОСМИЧЕСКИЙ ФАКТ", font=FONT, font_size=14,
                         color=ACCENT, weight=BOLD).move_to([cx, 2.8, 0]).set_opacity(0)
        divider   = Line([cx-2.3, 2.35, 0], [cx+2.3, 2.35, 0],
                         color=DIM, stroke_width=0.6).set_opacity(0)
        headline  = Text("Туманность Орла", font=FONT, font_size=30,
                         color=WHITE, weight=BOLD).move_to([cx, 1.5, 0]).set_opacity(0)
        sub1      = Text("Расстояние от Земли", font=FONT, font_size=15,
                         color=DIM).move_to([cx, 0.6, 0]).set_opacity(0)
        dist      = Text("7 000", font=FONT, font_size=56,
                         color=GOLD, weight=BOLD).move_to([cx, -0.3, 0]).set_opacity(0)
        unit      = Text("световых лет", font=FONT, font_size=16,
                         color=DIM).move_to([cx, -1.1, 0]).set_opacity(0)
        discovered = Text("Открыта: 1746 г.  •  Де Шезо", font=FONT, font_size=12,
                          color=DIM).move_to([cx, -2.7, 0]).set_opacity(0)
        self.add(tag, divider, headline, sub1, dist, unit, discovered)

        content = VGroup(tag, divider, headline, sub1, dist, unit, discovered)

        # 1. Панель въезжает — whoosh
        self.go(panel_group.animate.shift(RIGHT * (FW * 2)),
                footage_group.animate.shift(RIGHT * FOOTAGE_DX),
                dur=0.65, rf=smooth, sfx="whoosh_fast")

        # Контент появляется последовательно
        self.go(tag.animate.set_opacity(1).shift(RIGHT*0.12), dur=0.3)
        self.go(divider.animate.set_opacity(1), headline.animate.set_opacity(1).shift(RIGHT*0.1),
                dur=0.4)
        self.go(sub1.animate.set_opacity(1), dur=0.25)
        self.go(dist.animate.set_opacity(1), dur=0.4)
        self.go(unit.animate.set_opacity(1), discovered.animate.set_opacity(1), dur=0.3)

        # 2. Hold
        self.hold(2.8)

        # 3. Контент уходит
        self.go(content.animate.set_opacity(0).shift(LEFT*0.2), dur=0.3)

        # 4. Панель уходит — whoosh
        self.go(panel_group.animate.shift(LEFT * (FW * 2)),
                footage_group.animate.shift(LEFT * FOOTAGE_DX),
                dur=0.55, rf=rush_into, sfx="whoosh")
        self.hold(0.4)
