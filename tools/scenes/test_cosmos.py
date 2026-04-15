"""
test_cosmos.py — DE — Kosmische Infografiken auf Basis SmartScene.

Запуск:
  manim tools/scenes/test_cosmos.py OrbitSpeed     -ql --media_dir tools/manim_out
  manim tools/scenes/test_cosmos.py CosmicScale    -ql --media_dir tools/manim_out
  manim tools/scenes/test_cosmos.py UniverseAge    -ql --media_dir tools/manim_out
  manim tools/scenes/test_cosmos.py StarTemp       -ql --media_dir tools/manim_out
"""

import sys
sys.path.insert(0, str(__import__("pathlib").Path(__file__).parent))

from smart_scene import SmartScene
from manim import *
import numpy as np

BG     = "#05050f"
ACCENT = "#4fc3f7"
GOLD   = "#ffd54f"
DIM    = "#37474f"
WHITE  = "#e8eaf6"
RED    = "#ff5252"
GREEN  = "#69f0ae"
ORANGE = "#ffb74d"
PURP   = "#ce93d8"
FONT   = "Arial"
FW     = config.frame_width
FH     = config.frame_height


def make_stars(n=100):
    import random as _rnd
    _rnd.seed(7)
    g = VGroup()
    for _ in range(n):
        x = _rnd.uniform(-FW / 2, FW / 2)
        y = _rnd.uniform(-FH / 2, FH / 2)
        r = _rnd.uniform(0.01, 0.04)
        op = _rnd.uniform(0.15, 0.55)
        g.add(Dot(point=[x, y, 0], radius=r, color=WHITE, fill_opacity=op))
    return g


# ═══════════════════════════════════════════════════════════════════════════════
# 1. OrbitSpeed — Vergleich der Umlaufbahnen
# ═══════════════════════════════════════════════════════════════════════════════

class OrbitSpeed(SmartScene):
    def construct(self):
        self.camera.background_color = BG

        PLANETS = [
            ("Erde",    1.20, ACCENT, "29,8 km/s"),
            ("Mars",    1.90, ORANGE, "24,1 km/s"),
            ("Jupiter", 2.80, GOLD,   "13,1 km/s"),
        ]
        CENTER = ORIGIN + LEFT * 0.8

        stars = make_stars(90)
        self.add(stars)

        # Sonne (Mittelpunkt)
        sun_glow = Circle(radius=0.38, color=GOLD,
                          fill_opacity=0.12, stroke_width=0)
        sun      = Circle(radius=0.22, color=GOLD,
                          fill_color=GOLD, fill_opacity=1, stroke_width=0)
        sun_lbl  = Text("Sonne", font=FONT, font_size=11, color=GOLD)
        sun_lbl.next_to(sun, DOWN, buff=0.12)
        for m in (sun_glow, sun, sun_lbl):
            m.move_to(CENTER)
        sun_lbl.move_to(CENTER + DOWN * 0.38)

        title = Text("UMLAUFGESCHWINDIGKEITEN", font=FONT, font_size=15,
                     color=ACCENT, weight=BOLD)
        title.move_to(UP * (FH / 2 - 0.38))

        self.add(sun_glow, sun, sun_lbl, title)

        orbit_rings   = VGroup()
        planet_dots   = VGroup()
        planet_labels = VGroup()

        for name, radius, color, speed_str in PLANETS:
            ring = Circle(radius=radius, color=DIM,
                          stroke_width=0.9, stroke_opacity=0.4)
            ring.move_to(CENTER)
            orbit_rings.add(ring)

            dot = Dot(radius=0.10, color=color, fill_opacity=0)
            dot.move_to(CENTER + RIGHT * radius)

            name_t  = Text(name,      font=FONT, font_size=13, color=color, weight=BOLD)
            speed_t = Text(speed_str, font=FONT, font_size=11, color=WHITE)
            lbl_grp = VGroup(name_t, speed_t).arrange(DOWN, buff=0.04)
            lbl_grp.move_to(CENTER + RIGHT * (radius + 0.55))
            lbl_grp.set_opacity(0)

            planet_dots.add(dot)
            planet_labels.add(lbl_grp)

        self.add(orbit_rings, planet_dots, planet_labels)

        # Title вход
        title.set_opacity(0)
        self.go(title.animate.set_opacity(1), dur=0.6, rf=smooth,
                sfx="whoosh_fast")

        # Планеты появляются по одной
        for i, (dot, lbl, (name, radius, color, _)) in enumerate(
                zip(planet_dots, planet_labels, PLANETS)):
            # Дуга трассировки
            arc = Arc(radius=radius, start_angle=PI / 2, angle=-TAU * 0.6,
                      stroke_color=color, stroke_width=1.8, stroke_opacity=0.5)
            arc.move_to(CENTER)
            arc.set_stroke(opacity=0)
            self.add(arc)

            self.go(
                dot.animate.set_fill(opacity=1),
                arc.animate.set_stroke(opacity=0.5),
                lbl.animate.set_opacity(1),
                dur=0.8, rf=smooth, sfx="whoosh_fast",
            )

        # Финал
        self.hold(1.8, sfx="done")
        self.go(
            Group(*self.mobjects).animate.set_opacity(0),
            dur=0.5, sfx="whoosh",
        )


# ═══════════════════════════════════════════════════════════════════════════════
# 2. CosmicScale — Kosmische Entfernungen
# ═══════════════════════════════════════════════════════════════════════════════

class CosmicScale(SmartScene):
    def construct(self):
        self.camera.background_color = BG

        MILESTONES = [
            ("Mond",      "1,3 Ls",  0.08),
            ("Mars",      "3 min",   0.22),
            ("Pluto",     "5,5 h",   0.40),
            ("Voyager 1", "22 h",    0.62),
            ("Proxima",   "4,2 J",   1.00),
        ]
        COLORS = [ACCENT, GREEN, GOLD, ORANGE, RED]

        stars = make_stars(80)
        self.add(stars)

        title = Text("KOSMISCHE ENTFERNUNGEN", font=FONT, font_size=16,
                     color=ACCENT, weight=BOLD)
        title.move_to(UP * (FH / 2 - 0.42)).set_opacity(0)

        # Горизонтальная линия
        LINE_Y  = -0.3
        LINE_X0 = -FW / 2 + 0.55
        LINE_X1 =  FW / 2 - 0.55
        LINE_W  = LINE_X1 - LINE_X0

        base_line = Line([LINE_X0, LINE_Y, 0], [LINE_X1, LINE_Y, 0],
                         color=DIM, stroke_width=1.5, stroke_opacity=0.5)

        # Земля слева
        earth_dot = Dot(radius=0.10, color=ACCENT, fill_opacity=1)
        earth_dot.move_to([LINE_X0, LINE_Y, 0])
        earth_lbl = Text("Erde", font=FONT, font_size=11, color=ACCENT)
        earth_lbl.next_to(earth_dot, DOWN, buff=0.15)

        self.add(base_line, earth_dot, earth_lbl)
        self.go(title.animate.set_opacity(1), dur=0.5, sfx="whoosh_fast")
        self.add(title)

        for i, (name, dist, frac, color) in enumerate(
                zip([m[0] for m in MILESTONES],
                    [m[1] for m in MILESTONES],
                    [m[2] for m in MILESTONES],
                    COLORS)):
            x = LINE_X0 + frac * LINE_W
            dot = Dot(radius=0.09, color=color, fill_opacity=0)
            dot.move_to([x, LINE_Y, 0])

            tick = Line([x, LINE_Y - 0.12, 0], [x, LINE_Y + 0.12, 0],
                        color=color, stroke_width=1.8, stroke_opacity=0)

            name_t = Text(name, font=FONT, font_size=11, color=color, weight=BOLD)
            dist_t = Text(dist, font=FONT, font_size=10, color=WHITE)

            name_t.move_to([x, LINE_Y + 0.52, 0])
            dist_t.move_to([x, LINE_Y + 0.28, 0])
            name_t.set_opacity(0)
            dist_t.set_opacity(0)

            self.add(dot, tick, name_t, dist_t)

            sfx = "done" if i == len(MILESTONES) - 1 else "tick"
            self.go(
                dot.animate.set_fill(opacity=1),
                tick.animate.set_stroke(opacity=1),
                name_t.animate.set_opacity(1),
                dist_t.animate.set_opacity(1),
                dur=0.45, rf=smooth, sfx=sfx,
            )

        self.hold(1.8)
        self.go(
            Group(*self.mobjects).animate.set_opacity(0),
            dur=0.5, sfx="whoosh",
        )


# ═══════════════════════════════════════════════════════════════════════════════
# 3. UniverseAge — Alter des Universums
# ═══════════════════════════════════════════════════════════════════════════════

class UniverseAge(SmartScene):
    def construct(self):
        self.camera.background_color = BG

        EVENTS = [
            ("Urknall",         "0",          0.00, RED),
            ("Erste Sterne",    "200 Mio J",  0.14, ORANGE),
            ("Milchstraße",     "8,8 Mrd J",  0.64, ACCENT),
            ("Sonne",           "9,2 Mrd J",  0.67, GOLD),
            ("Leben",           "10 Mrd J",   0.72, GREEN),
            ("Heute",           "13,8 Mrd J", 1.00, WHITE),
        ]

        stars = make_stars(80)
        self.add(stars)

        title = Text("ALTER DES UNIVERSUMS", font=FONT, font_size=16,
                     color=ACCENT, weight=BOLD)
        title.move_to(UP * (FH / 2 - 0.42)).set_opacity(0)

        LINE_Y  = 0.15
        LINE_X0 = -FW / 2 + 0.6
        LINE_X1 =  FW / 2 - 0.6
        LINE_W  = LINE_X1 - LINE_X0

        # Линия рисуется через ValueTracker для прогрессивного рендера
        base_line_bg = Line([LINE_X0, LINE_Y, 0], [LINE_X1, LINE_Y, 0],
                            color=DIM, stroke_width=1.2, stroke_opacity=0.3)
        self.add(base_line_bg)

        self.go(title.animate.set_opacity(1), dur=0.5,
                sfx="whoosh_fast")
        self.add(title)

        # Используем tracker для прогресса линии
        prog_tracker = ValueTracker(0.001)  # начинаем с малого ненулевого значения
        drawn_line = always_redraw(lambda: Line(
            [LINE_X0, LINE_Y, 0],
            [LINE_X0 + prog_tracker.get_value() * LINE_W, LINE_Y, 0],
            color=ACCENT, stroke_width=2.2,
        ))
        self.add(drawn_line)

        for i, (name, time_str, frac, color) in enumerate(EVENTS):
            x_target = LINE_X0 + frac * LINE_W

            tick = Line([x_target, LINE_Y - 0.14, 0],
                        [x_target, LINE_Y + 0.14, 0],
                        color=color, stroke_width=2.0, stroke_opacity=0)

            # Чередуем метки вверх/вниз
            y_dir = 1 if i % 2 == 0 else -1
            y_name = LINE_Y + y_dir * 0.60
            y_time = LINE_Y + y_dir * 0.35

            name_t = Text(name,     font=FONT, font_size=11, color=color, weight=BOLD)
            time_t = Text(time_str, font=FONT, font_size=10, color=WHITE)
            name_t.move_to([x_target, y_name, 0]).set_opacity(0)
            time_t.move_to([x_target, y_time, 0]).set_opacity(0)

            self.add(tick, name_t, time_t)

            sfx = "done" if name == "Heute" else "tick"
            self.go(
                prog_tracker.animate.set_value(frac if frac > 0 else 0.001),
                tick.animate.set_stroke(opacity=1),
                name_t.animate.set_opacity(1),
                time_t.animate.set_opacity(1),
                dur=0.5, rf=smooth, sfx=sfx,
            )

        self.hold(1.8)
        self.go(
            Group(*self.mobjects).animate.set_opacity(0),
            dur=0.5, sfx="whoosh",
        )


# ═══════════════════════════════════════════════════════════════════════════════
# 4. StarTemp — Sterntemperaturen
# ═══════════════════════════════════════════════════════════════════════════════

class StarTemp(SmartScene):
    def construct(self):
        self.camera.background_color = BG

        STARS = [
            ("Roter Zwerg",   3_200,  "#ff3d00", 0.30),
            ("Oranger Stern", 5_000,  "#ff8f00", 0.40),
            ("Sonne",         5_778,  GOLD,      0.52),
            ("Weißer Stern",  9_500,  "#e8eaf6", 0.42),
            ("Blauer Riese", 30_000,  "#82b1ff", 0.70),
        ]

        n = len(STARS)
        xs = np.linspace(-FW / 2 + 1.0, FW / 2 - 1.0, n)

        stars_bg = make_stars(70)
        self.add(stars_bg)

        title = Text("STERNTEMPERATUREN", font=FONT, font_size=16,
                     color=ACCENT, weight=BOLD)
        title.move_to(UP * (FH / 2 - 0.42)).set_opacity(0)
        self.add(title)
        self.go(title.animate.set_opacity(1), dur=0.5, sfx="whoosh_fast")

        trackers = [ValueTracker(0) for _ in STARS]

        star_circles = VGroup()
        star_labels  = VGroup()
        temp_numbers = VGroup()

        STAR_Y  = 0.3
        LABEL_Y = -1.0
        TEMP_Y  = -1.55

        for i, ((name, temp, color, radius_norm), x) in enumerate(
                zip(STARS, xs)):
            radius = radius_norm * 0.9
            circle = Circle(radius=radius, color=color,
                            fill_color=color, fill_opacity=0,
                            stroke_width=1.5, stroke_opacity=0)
            circle.move_to([x, STAR_Y, 0])
            star_circles.add(circle)

            name_t = Text(name, font=FONT, font_size=10, color=color)
            name_t.move_to([x, LABEL_Y, 0]).set_opacity(0)
            star_labels.add(name_t)

            tr = trackers[i]
            num_obj = always_redraw(
                lambda tr=tr, color=color, x=x: Text(
                    f"{int(tr.get_value()):,}".replace(",", ".") + " K",
                    font=FONT, font_size=11, color=color,
                ).move_to([x, TEMP_Y, 0])
            )
            temp_numbers.add(num_obj)

        self.add(star_circles, star_labels, temp_numbers)

        # Каждая звезда появляется с тиками каунтера, done только на последней
        for i, ((name, temp, color, _), circle, label) in enumerate(
                zip(STARS, star_circles, star_labels)):
            tr = trackers[i]
            is_last = (i == n - 1)
            self.go(
                circle.animate.set_fill(opacity=0.85).set_stroke(opacity=0.6),
                label.animate.set_opacity(1),
                tr.animate.set_value(temp),
                dur=0.8, rf=rush_into, sfx="tick", sfx_n=4,
            )
            if is_last:
                self.sound("done")

        self.hold(1.8)
        self.go(
            Group(*self.mobjects).animate.set_opacity(0),
            dur=0.5, sfx="whoosh",
        )
