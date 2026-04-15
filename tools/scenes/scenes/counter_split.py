"""
CounterSplit — digits split into groups (MRD/MIO/TSD/units), each counts separately.

Usage:
    manim tools/scenes/scenes/counter_split.py CounterSplit -qh --media_dir tools/manim_out
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


def rush_into(t: float) -> float:
    return 1 - (1 - t) ** 3


def _split_number(n: int):
    """Split integer into groups of 3 from right, max 4 groups."""
    groups = []
    remaining = n
    for _ in range(4):
        groups.append(remaining % 1000)
        remaining //= 1000
        if remaining == 0:
            break
    groups.reverse()
    return groups  # e.g. [13, 800, 000, 000]


GROUP_LABELS = ["MRD", "MIO", "TSD", ""]


class CounterSplit(SmartScene):
    number:    int | float = 13_800_000_000
    unit:      str         = "JAHRE"
    tag:       str         = "ALTER DES UNIVERSUMS"
    total_dur: float       = 9.0

    def construct(self):
        dur = self.total_dur
        FH  = config.frame_height
        FW  = config.frame_width

        ENTRY_TAG  = 0.4
        ENTRY_COLS = 0.3
        HOLD_END   = 1.0
        EXIT_DUR   = 0.5

        num_int = int(self.number)
        groups  = _split_number(num_int)
        n_cols  = len(groups)

        count_dur = max(1.0, dur - ENTRY_TAG - ENTRY_COLS - HOLD_END - EXIT_DUR)
        col_dur   = count_dur / n_cols

        self.camera.background_color = BG

        # ── Tag label ─────────────────────────────────────────────────────────
        tag_lbl = Text(self.tag, font=FONT, font_size=16, color=ACCENT, weight=BOLD)
        tag_lbl.move_to([0, 2.2, 0])
        tag_lbl.set_opacity(0)

        # ── Unit label ────────────────────────────────────────────────────────
        unit_lbl = Text(self.unit, font=FONT, font_size=18, color=DIM)
        unit_lbl.move_to([0, -2.4, 0])
        unit_lbl.set_opacity(0)

        # ── Build columns ─────────────────────────────────────────────────────
        # Determine column spacing
        col_spacing = FW / (n_cols + 1)
        col_xs = [(i + 1) * col_spacing - FW / 2 for i in range(n_cols)]

        # Assign label names based on group index (first group can be MRD/MIO/etc.)
        # groups has n_cols items; map to labels from end
        # e.g. 4 groups -> MRD, MIO, TSD, ""
        # e.g. 3 groups -> MIO, TSD, ""
        # e.g. 2 groups -> TSD, ""
        suffix_labels = GROUP_LABELS[-(n_cols):]  # last n_cols from GROUP_LABELS

        trackers = [ValueTracker(0.0) for _ in range(n_cols)]
        col_targets = [float(g) for g in groups]

        # Dot separators between columns
        dots = []
        for i in range(n_cols - 1):
            dot = Text("·", font=FONT, font_size=56, color=DIM)
            dot.move_to([(col_xs[i] + col_xs[i + 1]) / 2, 0.1, 0])
            dot.set_opacity(0)
            dots.append(dot)

        # Column digit mobs (always_redraw)
        col_mobs = []
        col_sub_mobs = []
        for i, (trk, target, x) in enumerate(zip(trackers, col_targets, col_xs)):
            color = GOLD if i == 0 else WHITE
            # Pad non-first groups to 3 digits
            def _make_col_text(v, tgt=target, is_first=(i == 0), col_i=i, cx=x):
                val = int(round(v * tgt))
                if is_first:
                    s = str(val)
                else:
                    s = f"{val:03d}"
                return (Text(s, font=FONT, font_size=72, color=GOLD if col_i == 0 else WHITE, weight=BOLD)
                        .move_to([cx, 0.1, 0]))

            mob = always_redraw(lambda trk=trk, tgt=target, is_first=(i == 0), ci=i, cx=col_xs[i]:
                (lambda v: (Text(str(int(round(v * tgt))), font=FONT, font_size=72,
                                 color=GOLD if ci == 0 else WHITE, weight=BOLD)
                            .move_to([cx, 0.1, 0])
                            if is_first else
                            Text(f"{int(round(v * tgt)):03d}", font=FONT, font_size=72,
                                 color=GOLD if ci == 0 else WHITE, weight=BOLD)
                            .move_to([cx, 0.1, 0]))(trk.get_value())
            )
            col_mobs.append(mob)

            # Sub-label
            sl = suffix_labels[i]
            sub = Text(sl, font=FONT, font_size=12, color=DIM)
            sub.move_to([col_xs[i], -0.72, 0])
            sub.set_opacity(0)
            col_sub_mobs.append(sub)

        all_mobs = [tag_lbl, unit_lbl] + col_mobs + col_sub_mobs + dots
        self.add(*all_mobs)

        # ── 1. Tag fades in ───────────────────────────────────────────────────
        self.go(tag_lbl.animate.set_opacity(1), dur=ENTRY_TAG, rf=smooth, sfx="whoosh_fast")

        # ── 2. Column sub-labels + dots appear ───────────────────────────────
        fade_in_mobs = col_sub_mobs + dots
        if fade_in_mobs:
            self.go(*[m.animate.set_opacity(1) for m in fade_in_mobs],
                    unit_lbl.animate.set_opacity(1),
                    dur=ENTRY_COLS, rf=smooth)
        else:
            self.go(unit_lbl.animate.set_opacity(1), dur=ENTRY_COLS, rf=smooth)

        # ── 3. Each column counts left to right ───────────────────────────────
        for i, trk in enumerate(trackers):
            tick_n = max(6, int(col_dur * 10))
            self.go(trk.animate.set_value(1.0),
                    dur=col_dur, rf=rush_into,
                    sfx="tick", sfx_n=tick_n)

        # ── 4. Done ───────────────────────────────────────────────────────────
        self.hold(0.15, sfx="done")
        self.hold(HOLD_END - 0.15)

        # ── 5. Exit: FadeOut all + shift DOWN ─────────────────────────────────
        exit_group = VGroup(tag_lbl, unit_lbl, *col_mobs, *col_sub_mobs, *dots)
        self.go(FadeOut(exit_group, shift=DOWN * 1.2),
                dur=EXIT_DUR, rf=rush_into, sfx="whoosh")
