"""
FactPlate — motion design scene.

A card/panel that reveals a cosmic fact with optional counter.
Tags: факт, карточка, число, статистика, космос, инфографика
Duration: 6-14s (default 9s)

Usage:
    manim tools/scenes/scenes/fact_plate.py FactPlate -qh --media_dir tools/manim_out
    manim tools/scenes/scenes/fact_plate.py FactPlateStatic -qh --media_dir tools/manim_out
"""

import sys
import math
from pathlib import Path

from manim import *

# Add parent so SmartScene is importable
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from smart_scene import SmartScene

# ---- colors ------------------------------------------------------------------
BG     = "#04040e"
ACCENT = "#4fc3f7"
GOLD   = "#ffd54f"
DIM    = "#546e7a"
WHITE  = "#e8eaf6"

# ---- font --------------------------------------------------------------------
try:
    _test = Text("x", font="Montserrat")
    FONT = "Montserrat"
except Exception:
    FONT = "Arial"

# ---- rate func for counting --------------------------------------------------
def rush_into(t: float) -> float:
    """Fast start, slow end — number rushes in."""
    return 1 - (1 - t) ** 3


# ---- helpers -----------------------------------------------------------------

def _format_number(n: int | float) -> str:
    """Format a large number with German-style dots as thousand separators."""
    if isinstance(n, float):
        if n == int(n):
            n = int(n)
        else:
            return f"{n:,.3f}".replace(",", ".")
    s = f"{int(n):,}".replace(",", ".")
    return s


# ═════════════════════════════════════════════════════════════════════════════
# FactPlate
# ═════════════════════════════════════════════════════════════════════════════

class FactPlate(SmartScene):
    # ---- parameters ---------------------------------------------------------
    tag:          str   = "KOSMISCHER FAKT"
    headline:     str   = "Eine Lichtsekunde entspricht"
    main_value:   str   = "299.792"
    main_number:  int   = 299792
    sub_label:    str   = "Kilometer"
    source:       str   = "— Lichtgeschwindigkeit"
    with_counter: bool  = True
    total_dur:    float = 9.0

    def construct(self):
        dur  = self.total_dur

        # Timing constants
        ENTRY  = 1.4   # panel slide + bar + tag + headline
        SETTLE = 0.4   # brief pause after entry
        HOLD   = 1.2
        EXIT   = 0.6

        if self.with_counter:
            count_dur = max(0.5, dur - ENTRY - SETTLE - HOLD - EXIT)
        else:
            count_dur = 0.0

        self.camera.background_color = BG

        # ------------------------------------------------------------------ #
        # Panel geometry                                                       #
        # ------------------------------------------------------------------ #
        PW = 11.0   # panel width
        PH = 5.2    # panel height
        CR = 0.15   # corner radius

        panel = RoundedRectangle(
            width=PW, height=PH,
            corner_radius=CR,
            fill_color="#070712",
            fill_opacity=1.0,
            stroke_color=ACCENT,
            stroke_width=1.2,
        )
        panel.set_stroke(color=ACCENT, width=1.2, opacity=0.6)
        panel.move_to(ORIGIN)

        # Left accent bar (glow + solid)
        BAR_X   = -PW / 2 + 0.035   # center x of bar (left edge of panel)
        BAR_W   = 0.07
        BAR_H   = PH

        glow_bar = Rectangle(
            width=0.22, height=BAR_H,
            fill_color=ACCENT, fill_opacity=0.06,
            stroke_width=0,
        ).move_to([BAR_X - 0.04, 0, 0])

        accent_bar = Rectangle(
            width=BAR_W, height=BAR_H,
            fill_color=ACCENT, fill_opacity=1.0,
            stroke_width=0,
        ).move_to([BAR_X, 0, 0])

        # ------------------------------------------------------------------ #
        # Content layout (relative to panel center)                           #
        # ------------------------------------------------------------------ #
        INNER_L = -PW / 2 + 0.4    # left margin (after bar)
        TOP_Y   = PH / 2 - 0.38    # top of content area

        # Tag text
        tag_mob = Text(
            self.tag, font=FONT, font_size=12, color=ACCENT, weight=BOLD
        )
        tag_mob.move_to([INNER_L + tag_mob.width / 2 + 0.05, TOP_Y, 0])

        # Divider under tag
        divider = Line(
            start=[INNER_L, TOP_Y - 0.28, 0],
            end=[PW / 2 - 0.25, TOP_Y - 0.28, 0],
            stroke_color=ACCENT,
            stroke_width=0.8,
        )
        divider.set_stroke(opacity=0.4)

        # Headline (may wrap — use width limit)
        headline_mob = Text(
            self.headline,
            font=FONT, font_size=22, color=WHITE,
        )
        # center horizontally in panel, positioned below divider
        headline_mob.move_to([0, TOP_Y - 0.80, 0])

        # Main value text
        main_color = GOLD if self.with_counter else WHITE
        is_number  = self.with_counter  # counter mode implies numeric

        main_val_mob = Text(
            self.main_value,
            font=FONT, font_size=72, color=main_color, weight=BOLD,
        )
        main_val_mob.move_to([0, 0.05, 0])

        # Sub label
        sub_mob = Text(
            self.sub_label,
            font=FONT, font_size=16, color=DIM,
        )
        sub_mob.move_to([0, -1.45, 0])

        # Source (optional)
        source_mob = None
        if self.source:
            source_mob = Text(
                self.source,
                font=FONT, font_size=10, color="#2a3a45",
            )
            source_mob.move_to([PW / 2 - source_mob.width / 2 - 0.3, -PH / 2 + 0.28, 0])

        # ------------------------------------------------------------------ #
        # ANIMATION                                                            #
        # ------------------------------------------------------------------ #

        # Panel + bar start below screen, content stays at target pos (hidden)
        FH = config.frame_height
        panel.shift(DOWN * FH * 0.7)
        accent_bar.shift(DOWN * FH * 0.7)
        glow_bar.shift(DOWN * FH * 0.7)

        # Content hidden at target positions
        for mob in [tag_mob, divider, headline_mob, main_val_mob, sub_mob]:
            mob.set_opacity(0)
        if source_mob:
            source_mob.set_opacity(0)

        self.add(panel, accent_bar, glow_bar,
                 tag_mob, divider, headline_mob, main_val_mob, sub_mob)
        if source_mob:
            self.add(source_mob)

        # ── t=0.0  Panel slides in (0.5s) + whoosh_fast ──────────────────
        self.go(
            panel.animate.shift(UP * FH * 0.7),
            accent_bar.animate.shift(UP * FH * 0.7),
            glow_bar.animate.shift(UP * FH * 0.7),
            dur=0.5, rf=smooth, sfx="whoosh_fast",
        )

        self.hold(0.3)

        # ── t=0.8  Tag + divider appear (0.25s) ──────────────────────────
        self.go(
            tag_mob.animate.set_opacity(1),
            divider.animate.set_stroke(opacity=0.4),
            dur=0.25, rf=smooth,
        )

        # ── t=1.05  Headline fades in (0.35s) ────────────────────────────
        self.go(
            headline_mob.animate.set_opacity(1),
            dur=0.35, rf=smooth,
        )
        # cursor = 1.4 = ENTRY

        # ── ENTRY complete — branch on with_counter ───────────────────────

        if self.with_counter:
            # t=1.4  Sub label appears (0.2s)
            self.go(sub_mob.animate.set_opacity(1), dur=0.2, rf=smooth)

            # t=1.6  Counter starts
            tick_n  = max(6, int(count_dur * 4))
            target  = self.main_number
            tracker = ValueTracker(0.0)

            counter_pos = main_val_mob.get_center()

            def _make_counter(val: float):
                n_cur  = val * target
                n_disp = int(round(n_cur))
                s = _format_number(n_disp)
                t = Text(s, font=FONT, font_size=72, color=WHITE, weight=BOLD)
                t.move_to(counter_pos)
                return t

            counter_mob = always_redraw(lambda: _make_counter(tracker.get_value()))
            self.add(counter_mob)

            self.go(
                tracker.animate.set_value(1.0),
                dur=count_dur, rf=rush_into,
                sfx="tick", sfx_n=tick_n,
            )
            # cursor = 1.6 + count_dur

            # Number snaps gold (0.2s) + done sfx
            final_str = _format_number(target)
            num_final = Text(
                final_str, font=FONT, font_size=72, color=GOLD, weight=BOLD
            ).move_to(counter_pos)

            self.remove(counter_mob)
            self.add(num_final)

            self.go(
                Transform(num_final, num_final),   # dummy — snap is instant
                dur=0.2, rf=linear, sfx="done",
            )
            # cursor = 1.8 + count_dur

            # Source appears (0.15s)
            if source_mob:
                self.go(source_mob.animate.set_opacity(1), dur=0.15, rf=smooth)
            else:
                self.hold(0.15)

            self.hold(HOLD)

            # EXIT
            exit_mobs = [panel, accent_bar, glow_bar, tag_mob, divider, headline_mob, num_final, sub_mob]
            if source_mob:
                exit_mobs.append(source_mob)
            self.go(
                *[FadeOut(m, shift=RIGHT * 2) for m in exit_mobs],
                dur=EXIT, rf=smooth, sfx="whoosh",
            )

        else:
            # with_counter=False
            # t=1.4  Main value appears (0.3s) + done sfx
            self.go(
                main_val_mob.animate.set_opacity(1),
                dur=0.3, rf=smooth, sfx="done",
            )

            # t=1.7  Sub label (0.2s)
            self.go(sub_mob.animate.set_opacity(1), dur=0.2, rf=smooth)

            # t=1.9  Source (0.15s)
            if source_mob:
                self.go(source_mob.animate.set_opacity(1), dur=0.15, rf=smooth)
                t_after_content = 1.9 + 0.15  # = 2.05
            else:
                self.hold(0.15)
                t_after_content = 2.05

            # Hold for remaining time
            hold_t = max(0.1, dur - t_after_content - EXIT)
            self.hold(hold_t)

            # EXIT
            exit_mobs = [panel, accent_bar, glow_bar, tag_mob, divider, headline_mob, main_val_mob, sub_mob]
            if source_mob:
                exit_mobs.append(source_mob)
            self.go(
                *[FadeOut(m, shift=RIGHT * 2) for m in exit_mobs],
                dur=EXIT, rf=smooth, sfx="whoosh",
            )


# ── Static variant ────────────────────────────────────────────────────────────

class FactPlateStatic(FactPlate):
    with_counter: bool  = False
    total_dur:    float = 8.0
    main_value:   str   = "8 Minuten 20 Sekunden"
    sub_label:    str   = "Licht von der Sonne zur Erde"
    source:       str   = "— NASA Solar Facts"
    headline:     str   = "Reisezeit des Sonnenlichts"
