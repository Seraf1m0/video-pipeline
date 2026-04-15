"""
test_motion_v3.py v3 — DE — Motion Design auf Basis SmartScene.

Запуск:
  manim tools/test_motion_v3.py CounterHUD      -qh --media_dir tools/manim_out
  manim tools/test_motion_v3.py BounceGraph     -qh --media_dir tools/manim_out
  manim tools/test_motion_v3.py CharacterMotion -qh --media_dir tools/manim_out
  manim tools/test_motion_v3.py MotivVsDisc     -qh --media_dir tools/manim_out
"""

import sys
sys.path.insert(0, str(__import__("pathlib").Path(__file__).parent))

from smart_scene import SmartScene
from manim import *
import numpy as np

BG    = "#05050f"
ACC   = "#4fc3f7"
GOLD  = "#ffd54f"
DIM   = "#37474f"
WHITE = "#e8eaf6"
GREEN = "#69f0ae"
RED   = "#ff5252"
FONT  = "Arial"
FW    = config.frame_width
FH    = config.frame_height


def _fmt(n: float) -> str:
    return f"{int(n):,}".replace(",", ".")


# ═══════════════════════════════════════════════════════════════════════════════
# 1. CounterHUD
# ═══════════════════════════════════════════════════════════════════════════════

class CounterHUD(SmartScene):
    def construct(self):
        self.camera.background_color = BG

        TARGET    = 13_800_000_000
        TAG       = "ALTER DES UNIVERSUMS"
        UNIT      = "JAHRE"
        COUNT_DUR = 2.2

        for y in np.arange(-FH/2, FH/2 + 0.1, 0.5):
            self.add(Line(LEFT*FW/2, RIGHT*FW/2,
                          color=ACC, stroke_width=0.25, stroke_opacity=0.06
                          ).move_to([0, y, 0]))

        blen    = 0.55
        corners = VGroup()
        for sx, sy in [(-1,-1),(1,-1),(-1,1),(1,1)]:
            cx, cy = sx*FW/2*0.90, sy*FH/2*0.84
            corners.add(
                Line([cx,cy,0],[cx+sx*blen,cy,0], color=ACC, stroke_width=1.8),
                Line([cx,cy,0],[cx,cy+sy*blen,0], color=ACC, stroke_width=1.8),
            )
        corners.set_opacity(0)
        self.add(corners)

        tag_t  = Text(TAG,  font=FONT, font_size=13, color=ACC, weight=BOLD)
        tag_t.move_to([0, 2.0, 0]).set_opacity(0)
        unit_t = Text(UNIT, font=FONT, font_size=15, color=ACC, weight=BOLD)
        unit_t.move_to([0, -1.2, 0]).set_opacity(0)
        div = Line(LEFT*4.5, RIGHT*4.5, color=DIM, stroke_width=0.7)
        div.move_to([0, -0.72, 0]).set_opacity(0)
        prog_bg   = Line(LEFT*4.5, RIGHT*4.5, color=DIM, stroke_width=2)
        prog_line = Line(LEFT*4.5, LEFT*4.5,  color=ACC, stroke_width=2)
        prog_bg.move_to([0, -1.65, 0]).set_opacity(0)
        prog_line.move_to([0, -1.65, 0]).set_opacity(0)

        tracker = ValueTracker(0)
        NUM_POS = [0, 0.35, 0]
        num_mob = always_redraw(lambda: Text(
            _fmt(tracker.get_value()), font=FONT, font_size=96, color=WHITE,
        ).move_to(NUM_POS))
        self.add(tag_t, unit_t, div, prog_bg, prog_line, num_mob)

        # Появление
        self.go(corners.animate.set_opacity(1), tag_t.animate.set_opacity(1),
                unit_t.animate.set_opacity(1), div.animate.set_opacity(1),
                prog_bg.animate.set_opacity(0.4),
                dur=1.0, sfx="whoosh_fast")

        # Счёт
        self.go(
            tracker.animate.set_value(TARGET),
            prog_line.animate.put_start_and_end_on(
                [-4.5, -1.65, 0], [4.5, -1.65, 0]
            ).set_opacity(1),
            dur=COUNT_DUR, rf=rush_into,
            sfx="tick", sfx_n=14,
        )

        # Финал
        self.go(num_mob.animate.set_color(GOLD), prog_line.animate.set_color(GOLD),
                corners.animate.set_color(GOLD), dur=0.25, sfx="done")
        self.hold(2.8)

        self.go(Group(*self.mobjects).animate.shift(RIGHT*FW*1.1).set_opacity(0),
                dur=0.4, rf=rush_into, sfx="whoosh")


# ═══════════════════════════════════════════════════════════════════════════════
# 2. BounceGraph — Sprungkurve
# ═══════════════════════════════════════════════════════════════════════════════

class BounceGraph(SmartScene):
    def construct(self):
        self.camera.background_color = BG

        X_START = -5.5
        X_END   =  5.5
        Y_BASE  = -2.5

        axis_x = Line([X_START, Y_BASE, 0], [X_END, Y_BASE, 0], color=DIM, stroke_width=1.5)
        axis_y = Line([X_START, Y_BASE, 0], [X_START, Y_BASE+5.5, 0], color=DIM, stroke_width=1.5)
        label  = Text("ZEIT", font=FONT, font_size=12, color=DIM)
        label.move_to([X_END - 0.6, Y_BASE - 0.35, 0])
        self.add(axis_x, axis_y, label)

        BOUNCES = 6
        DECAY   = 0.55

        def bounce_height(x):
            period = 11.0 / BOUNCES
            b_num  = int(x / period)
            phase  = (x % period) / period
            h      = np.sin(phase * np.pi)
            amp    = DECAY ** b_num
            return h * amp * 4.5

        N_PTS     = 300
        xs        = np.linspace(0, 11, N_PTS)
        verts     = []
        bounce_xs = []

        for i, x in enumerate(xs):
            px = X_START + (x / 11.0) * (X_END - X_START)
            py = Y_BASE  + bounce_height(x)
            verts.append([px, py, 0])
            if i > 0 and bounce_height(x) < 0.01 and bounce_height(xs[i-1]) >= 0.01:
                bounce_xs.append(px)

        path_curve = VMobject(stroke_width=0)
        path_curve.set_points_as_corners(verts)

        ball       = Dot(radius=0.14, color=GOLD, stroke_width=0)
        ball.move_to(verts[0])
        trail      = TracedPath(ball.get_center, stroke_color=ACC, stroke_width=3, stroke_opacity=0.9)
        trail_glow = TracedPath(ball.get_center, stroke_color=ACC, stroke_width=14, stroke_opacity=0.12)
        self.add(trail_glow, trail, ball)

        title = Text("SPRUNGBAHN", font=FONT, font_size=13, color=ACC, weight=BOLD)
        title.move_to([0, 2.7, 0]).set_opacity(0)
        self.add(title)

        self.go(title.animate.set_opacity(1), dur=0.3, sfx="whoosh_fast")

        # Тик на каждый отскок — идеальный fit
        RUN_TIME = 4.5
        for bx in bounce_xs:
            t_frac = (bx - X_START) / (X_END - X_START)
            self.sound("tick", at=self._cur + t_frac * RUN_TIME, gain_delta=-3)

        self.go(MoveAlongPath(ball, path_curve), dur=RUN_TIME, rf=linear)

        self.go(ball.animate.scale(2.5).set_color(WHITE), dur=0.3, rf=there_and_back, sfx="done")
        self.hold(2.0)
        self.go(Group(*self.mobjects).animate.set_opacity(0), dur=0.5, sfx="whoosh")


# ═══════════════════════════════════════════════════════════════════════════════
# 3. CharacterMotion — Strichmännchen
# ═══════════════════════════════════════════════════════════════════════════════

def make_figure(color=WHITE, scale=1.0) -> VGroup:
    s = scale
    head  = Circle(radius=0.22*s, color=color, stroke_width=2.5*s, fill_opacity=0)
    head.move_to([0, 0.72*s, 0])
    body  = Line([0, 0.5*s, 0],  [0, -0.1*s, 0],   color=color, stroke_width=2.5*s)
    arm_l = Line([0, 0.25*s, 0], [-0.4*s, 0.0*s, 0],  color=color, stroke_width=2*s)
    arm_r = Line([0, 0.25*s, 0], [ 0.4*s, 0.0*s, 0],  color=color, stroke_width=2*s)
    leg_l = Line([0, -0.1*s, 0], [-0.3*s, -0.65*s, 0], color=color, stroke_width=2*s)
    leg_r = Line([0, -0.1*s, 0], [ 0.3*s, -0.65*s, 0], color=color, stroke_width=2*s)
    return VGroup(head, body, arm_l, arm_r, leg_l, leg_r)


class CharacterMotion(SmartScene):
    def construct(self):
        self.camera.background_color = BG

        GROUND_Y = -1.8
        FINISH_X =  5.5
        START_X  = -5.5

        ground   = Line([-7, GROUND_Y, 0], [7, GROUND_Y, 0], color=DIM, stroke_width=1.2)
        finish   = DashedLine([FINISH_X, GROUND_Y-0.3, 0], [FINISH_X, GROUND_Y+2.4, 0],
                              color=GOLD, stroke_width=1.5, dash_length=0.15)
        fin_lbl  = Text("ZIEL", font=FONT, font_size=13, color=GOLD, weight=BOLD)
        fin_lbl.move_to([FINISH_X, GROUND_Y + 2.7, 0])
        self.add(ground, finish, fin_lbl)

        Y_MOTIV = GROUND_Y + 0.65
        Y_DISC  = GROUND_Y + 0.65

        lbl_motiv = Text("MOTIVATION",  font=FONT, font_size=14, color=RED,   weight=BOLD)
        lbl_disc  = Text("DISZIPLIN",   font=FONT, font_size=14, color=GREEN, weight=BOLD)
        lbl_motiv.move_to([START_X + 0.5, Y_MOTIV + 1.4, 0])
        lbl_disc .move_to([START_X + 0.5, Y_DISC  - 1.7, 0])

        fig_m = make_figure(color=RED,   scale=0.85).move_to([START_X, Y_MOTIV + 0.7, 0])
        fig_d = make_figure(color=GREEN, scale=0.85).move_to([START_X, Y_DISC  - 0.6, 0])
        self.add(fig_m, fig_d, lbl_motiv, lbl_disc)

        self.go(FadeIn(fig_m, shift=UP*0.2), FadeIn(fig_d, shift=UP*0.2),
                FadeIn(lbl_motiv), FadeIn(lbl_disc), dur=0.5, sfx="whoosh_fast")

        disc_dx = FINISH_X - START_X
        TOTAL   = 8.0
        # Шаги — тик каждые ~0.8s (10 шагов за 8 секунд)
        for step in range(1, 10):
            self.sound("tick", at=self._cur + step * (TOTAL / 10))
        self.go(fig_d.animate.shift(RIGHT * disc_dx), lbl_disc.animate.shift(RIGHT * disc_dx),
                dur=TOTAL, rf=linear)

        win_text = Text("✓ ZIEL ERREICHT", font=FONT, font_size=18, color=GREEN, weight=BOLD)
        win_text.move_to([FINISH_X - 0.5, Y_DISC + 0.5, 0]).set_opacity(0)
        self.add(win_text)
        self.go(win_text.animate.set_opacity(1), dur=0.4, sfx="done")
        self.hold(2.5)
        self.go(Group(*self.mobjects).animate.set_opacity(0), dur=0.5, sfx="whoosh")


# ═══════════════════════════════════════════════════════════════════════════════
# 4. MotivVsDisc — Motivation vs. Disziplin parallel
# ═══════════════════════════════════════════════════════════════════════════════

class MotivVsDisc(SmartScene):
    def construct(self):
        self.camera.background_color = BG

        GROUND_Y = -0.5
        START_X  = -5.2
        FINISH_X =  5.2

        track_m  = Line([-6.5, GROUND_Y+1.4, 0], [6.5, GROUND_Y+1.4, 0],
                        color=DIM, stroke_width=0.8, stroke_opacity=0.5)
        track_d  = Line([-6.5, GROUND_Y-1.4, 0], [6.5, GROUND_Y-1.4, 0],
                        color=DIM, stroke_width=0.8, stroke_opacity=0.5)
        finish_m = DashedLine([FINISH_X, GROUND_Y+0.6,  0], [FINISH_X, GROUND_Y+2.3, 0],
                              color=GOLD, stroke_width=1.5, dash_length=0.12)
        finish_d = DashedLine([FINISH_X, GROUND_Y-2.3, 0], [FINISH_X, GROUND_Y-0.6, 0],
                              color=GOLD, stroke_width=1.5, dash_length=0.12)
        self.add(track_m, track_d, finish_m, finish_d)

        lbl_m = Text("MOTIVATION", font=FONT, font_size=13, color=RED,   weight=BOLD)
        lbl_m.move_to([-5.8, GROUND_Y + 2.0, 0])
        lbl_d = Text("DISZIPLIN",  font=FONT, font_size=13, color=GREEN, weight=BOLD)
        lbl_d.move_to([-5.8, GROUND_Y - 2.0, 0])

        fig_m = make_figure(color=RED,   scale=0.8).move_to([START_X, GROUND_Y+1.4, 0])
        fig_d = make_figure(color=GREEN, scale=0.8).move_to([START_X, GROUND_Y-1.4, 0])
        self.add(fig_m, fig_d, lbl_m, lbl_d)

        self.go(FadeIn(VGroup(fig_m, fig_d, lbl_m, lbl_d)), dur=0.4, sfx="whoosh_fast")

        DX = FINISH_X - START_X

        motiv_seq = [
            (DX * 0.50, 2.0, rush_from),
            (0,          2.5, linear),
            (DX * 0.28, 1.5, rush_from),
            (0,          1.5, linear),
            (DX * 0.22, 1.5, smooth),
        ]  # итого 9s

        anim_disc  = AnimationGroup(
            fig_d.animate.shift(RIGHT * DX),
            lbl_d.animate.shift(RIGHT * DX),
            run_time=9.0, rate_func=linear,
        )
        motiv_anims = []
        for dx, dur, rf in motiv_seq:
            if dx > 0:
                motiv_anims.append(AnimationGroup(
                    fig_m.animate(run_time=dur, rate_func=rf).shift(RIGHT * dx),
                    lbl_m.animate(run_time=dur, rate_func=rf).shift(RIGHT * dx),
                ))
            else:
                motiv_anims.append(Wait(dur))
        anim_motiv = Succession(*motiv_anims)

        # Тик на полпути — единственный звук во время движения
        self.sound("tick", at=self._cur + 4.5, gain_delta=-3)
        self.play(anim_disc, anim_motiv)
        self._cur += 9.0

        win = Text("DISZIPLIN GEWINNT", font=FONT, font_size=20, color=GREEN, weight=BOLD)
        win.move_to([1.5, GROUND_Y - 2.8, 0]).set_opacity(0)
        self.add(win)
        self.go(win.animate.set_opacity(1).shift(UP*0.15), dur=0.5, sfx="done")
        self.hold(3.0)
        self.go(Group(*self.mobjects).animate.set_opacity(0), dur=0.5, sfx="whoosh")
