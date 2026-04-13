"""
test_manim.py v2 — Улучшенные моушн-графики для космос-документалки.

Запуск:
  manim tools/test_manim.py Counter -q h --media_dir tools/manim_out
  manim tools/test_manim.py SizeComparison -q h --media_dir tools/manim_out
  manim tools/test_manim.py FactPlate -q h --media_dir tools/manim_out
  manim tools/test_manim.py MassChart -q h --media_dir tools/manim_out
"""

from manim import *
import numpy as np
import random

BG     = "#05050f"
ACCENT = "#4fc3f7"
GOLD   = "#ffd54f"
DIM    = "#37474f"
WHITE  = "#e8eaf6"
FONT   = "Arial"

random.seed(42)


def make_starfield(n=120, spread_x=7.5, spread_y=4.5):
    """Случайные точки-звёзды на фоне."""
    stars = VGroup()
    for _ in range(n):
        x = random.uniform(-spread_x, spread_x)
        y = random.uniform(-spread_y, spread_y)
        r = random.uniform(0.012, 0.045)
        op = random.uniform(0.15, 0.55)
        dot = Dot(point=[x, y, 0], radius=r, color=WHITE, fill_opacity=op)
        stars.add(dot)
    return stars


def glow(mob, color, layers=4, max_opacity=0.18):
    """Симуляция glow — несколько копий с увеличенным stroke и низкой opacity."""
    g = VGroup()
    for i in range(1, layers + 1):
        c = mob.copy()
        c.set_stroke(color, width=i * 6, opacity=max_opacity / i)
        c.set_fill(opacity=0)
        g.add(c)
    return g


# ── 1. КАУНТЕР ────────────────────────────────────────────────────────────────

class Counter(Scene):
    def construct(self):
        self.camera.background_color = BG

        # Звёздный фон
        stars = make_starfield(100)
        self.add(stars)

        # Кольцо-прогресс
        ring_bg = Arc(radius=1.9, start_angle=PI/2, angle=-TAU,
                      stroke_color=DIM, stroke_width=4, stroke_opacity=0.4)
        ring    = Arc(radius=1.9, start_angle=PI/2, angle=0,
                      stroke_color=ACCENT, stroke_width=6)
        ring_glow = Arc(radius=1.9, start_angle=PI/2, angle=0,
                        stroke_color=ACCENT, stroke_width=14, stroke_opacity=0.12)

        tracker = ValueTracker(0)

        def update_ring(mob):
            prog = tracker.get_value() / 4.246
            mob.become(Arc(
                radius=1.9, start_angle=PI/2,
                angle=-TAU * prog,
                stroke_color=ACCENT, stroke_width=6,
            ))

        def update_glow(mob):
            prog = tracker.get_value() / 4.246
            mob.become(Arc(
                radius=1.9, start_angle=PI/2,
                angle=-TAU * prog,
                stroke_color=ACCENT, stroke_width=18, stroke_opacity=0.12,
            ))

        ring.add_updater(update_ring)
        ring_glow.add_updater(update_glow)

        number = always_redraw(lambda: Text(
            f"{tracker.get_value():.3f}",
            font=FONT, font_size=88, color=WHITE, weight=BOLD,
        ).move_to(ORIGIN + UP * 0.18))

        label_top = Text("DISTANCE TO", font=FONT, font_size=17,
                         color=DIM, weight=BOLD).move_to(UP * 2.75)
        label_mid = Text("PROXIMA CENTAURI", font=FONT, font_size=19,
                         color=ACCENT, weight=BOLD).move_to(UP * 2.38)
        label_unit = Text("LIGHT  YEARS", font=FONT, font_size=16,
                          color=DIM).move_to(DOWN * 0.42)

        dot_start = Dot(point=UP * 1.9, radius=0.07, color=ACCENT)
        dot_glow  = Dot(point=UP * 1.9, radius=0.18, color=ACCENT, fill_opacity=0.25)

        self.play(FadeIn(stars, run_time=0.5))
        self.play(
            Create(ring_bg),
            FadeIn(label_top, shift=DOWN*0.1),
            FadeIn(label_mid, shift=DOWN*0.1),
            FadeIn(label_unit),
            run_time=0.5,
        )
        self.add(ring_glow, ring, number, dot_start, dot_glow)

        self.play(
            tracker.animate.set_value(4.246),
            rate_func=rush_into,
            run_time=2.6,
        )
        ring.remove_updater(update_ring)
        ring_glow.remove_updater(update_glow)

        # Финал — число золотое + flash
        final_num = Text("4.246", font=FONT, font_size=88, color=GOLD, weight=BOLD)
        final_num.move_to(ORIGIN + UP * 0.18)
        self.play(
            Transform(number, final_num),
            Flash(ORIGIN, color=GOLD, line_length=0.4,
                  num_lines=20, flash_radius=0.6),
            run_time=0.45,
        )
        self.wait(2.0)


# ── 2. SIZE COMPARISON ────────────────────────────────────────────────────────

class SizeComparison(Scene):
    def construct(self):
        self.camera.background_color = BG

        stars = make_starfield(80)
        self.add(stars)

        title = Text("PLANET SIZE COMPARISON",
                     font=FONT, font_size=24, color=DIM, weight=BOLD)
        title.to_edge(UP, buff=0.45)
        self.play(FadeIn(title), FadeIn(stars), run_time=0.4)

        data = [
            ("EARTH",    0.22, "#4fc3f7", "1×"),
            ("NEPTUNE",  0.40, "#5c6bc0", "3.9×"),
            ("JUPITER",  0.82, "#ff8a65", "11.2×"),
            ("SUN",      1.55, "#ffd54f", "109×"),
        ]
        xs = [-4.3, -2.1, 0.7, 4.1]

        for (name, r, color, ratio), x in zip(data, xs):
            # Glow кольца
            for i in range(3, 0, -1):
                glow_ring = Circle(
                    radius=r + i*0.08,
                    color=color,
                    fill_opacity=0,
                    stroke_width=8,
                    stroke_opacity=0.06 * (4 - i),
                ).move_to([x, -0.2, 0])
                self.add(glow_ring)

            circle = Circle(
                radius=r, color=color,
                fill_opacity=0.82,
                stroke_color=WHITE, stroke_width=0.6,
            ).move_to([x, -0.2, 0])

            # Линия от подписи к кругу
            label_y = -3.3
            line = DashedLine(
                start=[x, label_y + 0.22, 0],
                end=[x, -0.2 - r - 0.12, 0],
                color=DIM, stroke_width=0.8, dash_length=0.06,
            )

            nm = Text(name, font=FONT, font_size=14, color=WHITE, weight=BOLD)
            nm.move_to([x, label_y, 0])

            rt = Text(ratio, font=FONT, font_size=13, color=color)
            rt.next_to(circle, UP, buff=0.22)

            self.play(
                GrowFromCenter(circle),
                Create(line),
                FadeIn(nm, shift=UP*0.08),
                FadeIn(rt),
                run_time=0.55,
            )

        self.wait(2.2)


# ── 3. ФАКТ-ПЛАШКА ───────────────────────────────────────────────────────────

class FactPlate(Scene):
    def construct(self):
        self.camera.background_color = BG

        stars = make_starfield(90)
        self.add(stars)

        # Внешний glow прямоугольник
        glow_rect = RoundedRectangle(
            corner_radius=0.2, width=11.8, height=4.5,
            fill_color=ACCENT, fill_opacity=0,
            stroke_color=ACCENT, stroke_width=18, stroke_opacity=0.08,
        )

        rect = RoundedRectangle(
            corner_radius=0.18, width=11.5, height=4.2,
            fill_color="#0b1520", fill_opacity=0.97,
            stroke_color=ACCENT, stroke_width=1.6,
        )

        bar = Rectangle(
            width=0.09, height=4.2,
            fill_color=ACCENT, fill_opacity=1, stroke_width=0,
        ).align_to(rect, LEFT).shift(RIGHT*0.045)

        bar_glow = Rectangle(
            width=0.5, height=4.2,
            fill_color=ACCENT, fill_opacity=0.07, stroke_width=0,
        ).align_to(rect, LEFT).shift(RIGHT*0.25)

        tag = Text("SPACE FACT", font=FONT, font_size=14,
                   color=ACCENT, weight=BOLD)
        tag.align_to(rect, UL).shift(RIGHT*0.4 + DOWN*0.42)

        divider = Line(LEFT*4.9, RIGHT*4.9, color=DIM, stroke_width=0.5)
        divider.align_to(rect, UP).shift(DOWN*0.82)

        headline = Text(
            "The observable universe contains",
            font=FONT, font_size=27, color=WHITE,
        ).shift(UP*0.42)

        big = Text(
            "2,000,000,000,000",
            font=FONT, font_size=50, color=GOLD, weight=BOLD,
        ).shift(DOWN*0.22)
        big.set_color_by_gradient(GOLD, "#ff8f00")

        # Glow на числе
        big_glow = Text(
            "2,000,000,000,000",
            font=FONT, font_size=50, color=GOLD, weight=BOLD,
        ).shift(DOWN*0.22)
        big_glow.set_opacity(0.12)

        sub = Text("GALAXIES", font=FONT, font_size=20,
                   color=WHITE, weight=BOLD)
        sub.next_to(big, DOWN, buff=0.16)

        source = Text("— NASA / ESA Hubble Ultra Deep Field, 2016",
                      font=FONT, font_size=12, color=DIM)
        source.align_to(rect, DR).shift(LEFT*0.35 + UP*0.28)

        # Анимация
        self.play(
            FadeIn(stars),
            DrawBorderThenFill(rect),
            FadeIn(glow_rect),
            run_time=0.55,
        )
        self.play(
            GrowFromEdge(bar, UP),
            FadeIn(bar_glow),
            FadeIn(tag, shift=RIGHT*0.12),
            Create(divider),
            run_time=0.45,
        )
        self.play(FadeIn(headline, shift=DOWN*0.1), run_time=0.5)
        self.play(
            FadeIn(big, scale=1.07),
            FadeIn(big_glow, scale=1.2),
            run_time=0.5,
        )
        self.play(
            FadeIn(sub, shift=UP*0.06),
            FadeIn(source),
            run_time=0.4,
        )
        self.wait(2.8)
        self.play(
            FadeOut(VGroup(rect, glow_rect, bar, bar_glow, tag, divider,
                           headline, big, big_glow, sub, source),
                    shift=DOWN*0.12),
            run_time=0.45,
        )


# ── 4. ГИСТОГРАММА ───────────────────────────────────────────────────────────

class MassChart(Scene):
    def construct(self):
        self.camera.background_color = BG

        stars = make_starfield(70)
        self.add(stars)

        title = Text("PLANETARY MASS  (Earth = 1)",
                     font=FONT, font_size=22, color=DIM, weight=BOLD)
        title.to_edge(UP, buff=0.48)
        self.add(title)

        planets = [
            ("Mercury", 0.055, "#9e9e9e"),
            ("Venus",   0.815, "#ff8f00"),
            ("Earth",   1.000, "#4fc3f7"),
            ("Mars",    0.107, "#ef5350"),
            ("Jupiter", 317.8, "#ff8a65"),
            ("Saturn",  95.2,  "#ffd54f"),
            ("Uranus",  14.5,  "#80deea"),
            ("Neptune", 17.1,  "#5c6bc0"),
        ]

        max_val  = 317.8
        bar_w    = 0.68
        spacing  = 1.05
        max_h    = 3.8
        baseline = -2.65

        # Grid lines
        for frac, label in [(0.25, "79×"), (0.5, "159×"), (0.75, "238×"), (1.0, "317×")]:
            y = baseline + frac * max_h
            gl = DashedLine(LEFT*4.3, RIGHT*4.3,
                            color=DIM, stroke_width=0.5, stroke_opacity=0.5,
                            dash_length=0.09).move_to([0, y, 0])
            gl_lbl = Text(label, font=FONT, font_size=10, color=DIM)
            gl_lbl.move_to([-4.65, y, 0])
            self.add(gl, gl_lbl)

        base_line = Line(LEFT*4.5, RIGHT*4.5, color=WHITE,
                         stroke_width=0.9, stroke_opacity=0.3)
        base_line.move_to([0, baseline, 0])
        self.add(base_line)

        bars, name_lbls, val_lbls, glow_bars = [], [], [], []
        for i, (name, mass, color) in enumerate(planets):
            h = max(mass / max_val * max_h, 0.07)
            x = -3.7 + i * spacing

            # Glow под баром
            gbar = Rectangle(
                width=bar_w + 0.3, height=h,
                fill_color=color, fill_opacity=0.12, stroke_width=0,
            ).move_to([x, baseline + h/2, 0])

            bar = Rectangle(
                width=bar_w, height=h,
                fill_color=color, fill_opacity=0.9,
                stroke_width=0,
            ).move_to([x, baseline + h/2, 0])

            nm = Text(name, font=FONT, font_size=12, color=WHITE)
            nm.move_to([x, baseline - 0.3, 0])

            val_str = f"{mass:.0f}×" if mass >= 10 else f"{mass:.2f}×"
            vl = Text(val_str, font=FONT, font_size=11, color=color, weight=BOLD)
            vl.move_to([x, baseline + h + 0.24, 0])

            bars.append(bar)
            glow_bars.append(gbar)
            name_lbls.append(nm)
            val_lbls.append(vl)

        self.play(FadeIn(VGroup(*name_lbls)), run_time=0.3)
        self.play(
            *[GrowFromEdge(gb, DOWN) for gb in glow_bars],
            *[GrowFromEdge(b, DOWN) for b in bars],
            run_time=1.5, lag_ratio=0.07,
        )
        self.play(FadeIn(VGroup(*val_lbls)), run_time=0.4)

        # Подсветить Юпитер
        self.play(
            bars[4].animate.set_stroke(WHITE, width=2).set_fill(opacity=1.0),
            glow_bars[4].animate.set_fill(opacity=0.25),
            Flash([bars[4].get_center()[0], bars[4].get_top()[1], 0],
                  color="#ff8a65", line_length=0.3, num_lines=12),
            run_time=0.4,
        )
        self.wait(2.2)
