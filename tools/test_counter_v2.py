"""
test_counter_v2.py v3 — DE — HUD/sci-fi Zähler.

Запуск:
  manim tools/test_counter_v2.py CounterV2     -qh --media_dir tools/manim_out
  manim tools/test_counter_v2.py CounterMinimal -qh --media_dir tools/manim_out
  manim tools/test_counter_v2.py CounterSplit   -qh --media_dir tools/manim_out
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
CYAN2  = "#00e5ff"
FONT   = "Arial"

FW = config.frame_width
FH = config.frame_height


def _fmt(n: float) -> str:
    return f"{int(n):,}".replace(",", ".")


# ═══════════════════════════════════════════════════════════════════════════════
# CounterV2 — HUD-Zähler mit Scanline
# ═══════════════════════════════════════════════════════════════════════════════

class CounterV2(SmartScene):
    def construct(self):
        self.camera.background_color = BG

        TAG    = "ENTFERNUNG ZUM NÄCHSTEN STERN"
        TARGET = 40_208_000_000_000
        UNIT   = "KILOMETER"

        grid_lines = VGroup()
        for y in np.arange(-FH/2, FH/2, 0.45):
            line = Line(LEFT * FW/2, RIGHT * FW/2,
                        color=ACCENT, stroke_width=0.3, stroke_opacity=0.08)
            line.move_to([0, y, 0])
            grid_lines.add(line)
        self.add(grid_lines)

        blen = 0.5
        corners = VGroup()
        for sx, sy in [(-1,-1),(1,-1),(-1,1),(1,1)]:
            cx, cy = sx * FW/2 * 0.88, sy * FH/2 * 0.82
            h = Line([cx, cy, 0], [cx + sx*blen, cy, 0], color=ACCENT, stroke_width=1.5)
            v = Line([cx, cy, 0], [cx, cy + sy*blen, 0], color=ACCENT, stroke_width=1.5)
            corners.add(h, v)
        corners.set_opacity(0)
        self.add(corners)

        scanline      = Line(LEFT*FW/2, RIGHT*FW/2, color=CYAN2, stroke_width=2.5)
        scanline_glow = Line(LEFT*FW/2, RIGHT*FW/2, color=CYAN2, stroke_width=18, stroke_opacity=0.12)
        scan_group    = VGroup(scanline_glow, scanline)
        scan_group.move_to([0, FH/2, 0])
        self.add(scan_group)

        tag_lbl = Text(TAG, font=FONT, font_size=12, color=ACCENT, weight=BOLD)
        tag_lbl.move_to([0, 2.1, 0]).set_opacity(0)

        tracker     = ValueTracker(0)
        counter_pos = [0, 0.1, 0]
        counter     = always_redraw(lambda: Text(
            _fmt(tracker.get_value()), font=FONT, font_size=86, color=WHITE,
        ).move_to(counter_pos))

        unit_lbl = Text(UNIT, font=FONT, font_size=13, color=ACCENT)
        unit_lbl.move_to([0, -1.35, 0]).set_opacity(0)

        bar_bg = Rectangle(width=8.0, height=0.055,
                           fill_color=DIM, fill_opacity=0.5, stroke_width=0)
        bar_bg.move_to([0, -1.9, 0])
        bar_fill = Rectangle(width=0.001, height=0.055,
                             fill_color=ACCENT, fill_opacity=1, stroke_width=0)
        bar_fill.move_to([-4.0, -1.9, 0]).align_to(bar_bg, LEFT)
        self.add(tag_lbl, counter, unit_lbl, bar_bg, bar_fill)

        # 1. Riser + scanline
        self.sound("riser")
        self.go(scan_group.animate.move_to([0, -FH/2, 0]), dur=0.9, rf=linear)
        self.remove(scan_group)

        # 2. Reveal
        self.go(
            corners.animate.set_opacity(1),
            tag_lbl.animate.set_opacity(1),
            unit_lbl.animate.set_opacity(1),
            dur=0.35, sfx="whoosh_fast",
        )

        # 3. Счёт
        self.go(
            tracker.animate.set_value(TARGET),
            bar_fill.animate.stretch_to_fit_width(8.0).align_to(bar_bg, LEFT),
            dur=2.4, rf=rush_into,
            sfx="tick", sfx_n=22,
        )

        # 4. Финал — done в момент остановки, boom чуть после
        self.sound("done")
        self.sound("boom", at=self._cur + 0.12)

        flash_line = Line(LEFT*FW/2, RIGHT*FW/2,
                          color=WHITE, stroke_width=60, stroke_opacity=0.25)
        flash_line.move_to([0, -1.9, 0])
        self.add(flash_line)
        self.go(flash_line.animate.set_stroke(opacity=0), dur=0.4)
        self.remove(flash_line)
        self.go(bar_fill.animate.set_fill(GOLD), dur=0.2)
        self.hold(2.5)

        # 5. Выход
        self.go(
            VGroup(tag_lbl, counter, unit_lbl, bar_bg, bar_fill,
                   corners, grid_lines).animate.shift(RIGHT * FW * 1.2),
            dur=0.45, rf=rush_into, sfx="whoosh",
        )
        self.hold(0.2)


# ═══════════════════════════════════════════════════════════════════════════════
# CounterSplit — разряды считаются по очереди
# ═══════════════════════════════════════════════════════════════════════════════

class CounterSplit(SmartScene):
    def construct(self):
        self.camera.background_color = BG

        LABEL_TOP    = "ALTER DES UNIVERSUMS"
        LABEL_BOTTOM = "JAHRE"

        bg_rect = Rectangle(width=FW, height=FH,
                            fill_color="#020308", fill_opacity=1, stroke_width=0)
        self.add(bg_rect)

        top_lbl = Text(LABEL_TOP,    font=FONT, font_size=14, color=ACCENT, weight=BOLD)
        top_lbl.move_to([0, 2.5, 0]).set_opacity(0)
        bot_lbl = Text(LABEL_BOTTOM, font=FONT, font_size=14, color=ACCENT, weight=BOLD)
        bot_lbl.move_to([0, -1.85, 0]).set_opacity(0)
        top_div = Line(LEFT*5.5, RIGHT*5.5, color=DIM, stroke_width=0.6)
        top_div.move_to([0, 2.0, 0]).set_opacity(0)
        bot_div = Line(LEFT*5.5, RIGHT*5.5, color=DIM, stroke_width=0.6)
        bot_div.move_to([0, -1.4, 0]).set_opacity(0)
        self.add(top_lbl, bot_lbl, top_div, bot_div)

        parts    = ["13", "800", "000", "000"]
        labels   = ["MRD", "MIO", "TSD", ""]
        colors   = [GOLD, WHITE, WHITE, WHITE]
        n        = len(parts)
        spacing  = 3.0
        xs       = [-(n-1)*spacing/2 + i*spacing for i in range(n)]
        trackers = [ValueTracker(0) for _ in parts]

        self.go(
            top_lbl.animate.set_opacity(1),
            bot_lbl.animate.set_opacity(1),
            top_div.animate.set_opacity(1),
            bot_div.animate.set_opacity(1),
            dur=0.4,
        )
        self.sound("riser", at=self._cur + 0.1)

        for i, (part, label, color, x) in enumerate(zip(parts, labels, colors, xs)):
            target = int(part)
            tr     = trackers[i]

            num_obj = always_redraw(lambda tr=tr, color=color, x=x, part=part: Text(
                f"{int(tr.get_value()):0{len(part) if part != '13' else 2}d}",
                font=FONT, font_size=70, color=color,
            ).move_to([x, 0.3, 0]))

            lbl_obj = Text(label, font=FONT, font_size=11, color=DIM)
            lbl_obj.move_to([x, -1.0, 0]).set_opacity(0)

            sep = None
            if i < n - 1:
                sep = Text("·", font=FONT, font_size=40, color=DIM)
                sep.move_to([x + spacing/2, 0.3, 0]).set_opacity(0)
                self.add(sep)

            self.add(num_obj, lbl_obj)

            dur = 0.6 if target > 0 else 0.2
            if target > 0:
                self.go(
                    tr.animate.set_value(target),
                    lbl_obj.animate.set_opacity(1),
                    dur=dur, rf=rush_into,
                    sfx="tick", sfx_n=10,
                )
            else:
                self.go(
                    tr.animate.set_value(target),
                    lbl_obj.animate.set_opacity(1),
                    dur=dur, rf=rush_into,
                )

            if sep:
                self.go(sep.animate.set_opacity(0.4), dur=0.1)

        # Один финальный акцент — done + boom
        self.sound("done")
        self.sound("boom", at=self._cur + 0.12)
        self.hold(0.15)
        self.hold(2.8)

        self.go(FadeOut(Group(*self.mobjects), shift=UP * 0.3), dur=0.5, sfx="whoosh")
        self.hold(0.2)


# ═══════════════════════════════════════════════════════════════════════════════
# CounterMinimal — одно число на весь экран
# ═══════════════════════════════════════════════════════════════════════════════

class CounterMinimal(SmartScene):
    def construct(self):
        self.camera.background_color = BG

        TARGET = 13_800_000_000
        UNIT   = "JAHRE"
        TAG    = "ALTER DES UNIVERSUMS"

        tracker = ValueTracker(0)
        counter = always_redraw(lambda: Text(
            _fmt(tracker.get_value()), font=FONT, font_size=100, color=WHITE,
        ).move_to([0, 0.4, 0]))

        unit        = Text(UNIT, font=FONT, font_size=16, color=ACCENT).move_to([0, -1.1, 0]).set_opacity(0)
        tag         = Text(TAG,  font=FONT, font_size=13, color=DIM).move_to([0, -1.6, 0]).set_opacity(0)
        accent_line = Line(LEFT*2.5, RIGHT*2.5, color=ACCENT, stroke_width=1.2)
        accent_line.move_to([0, -0.5, 0]).set_opacity(0)
        self.add(counter, unit, tag, accent_line)

        self.sound("riser", at=0.05)
        self.go(
            unit.animate.set_opacity(1),
            tag.animate.set_opacity(1),
            accent_line.animate.set_opacity(1),
            dur=0.4,
        )

        self.go(tracker.animate.set_value(TARGET), dur=2.5, rf=rush_into,
                sfx="tick", sfx_n=20)

        self.sound("done")
        self.sound("boom", at=self._cur + 0.12)

        final = Text(_fmt(TARGET), font=FONT, font_size=100, color=GOLD).move_to([0, 0.4, 0])
        self.go(Transform(counter, final), dur=0.2)
        self.hold(2.8)

        self.go(FadeOut(Group(*self.mobjects), shift=LEFT * 0.5), dur=0.45, sfx="whoosh")
        self.hold(0.15)
