"""
mg_planner.py — анализирует сегменты, планирует MG-зоны,
выбирает Remotion-композицию и формирует props.

Output: session_dir/motion_graphics_plan.json

Usage:
    python mg_planner.py --channel de --session Video_20260415_193110
    python mg_planner.py --channel de --session Video_20260415_193110 --force
"""

import argparse
import json
import os
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
from agents.utils.paths import get_session_dir, TEMP_ROOT

# ── claude.exe ────────────────────────────────────────────────────────────────
_CLAUDE_DIR = Path(os.environ.get("APPDATA", "")) / "Claude/claude-code"
if _CLAUDE_DIR.exists():
    for _v in sorted(_CLAUDE_DIR.iterdir(), reverse=True):
        _exe = _v / "claude.exe"
        if _exe.exists():
            os.environ["PATH"] = str(_v) + os.pathsep + os.environ.get("PATH", "")
            break

CHANNEL_MAP = {
    "fr": "channel_002_cosmos_fr",
    "de": "channel_001_cosmos_de",
    "es": "channel_003_religion_es",
}


# ─── prompts ──────────────────────────────────────────────────────────────────

PLANNER_SYSTEM = """You are a video editor and motion graphics director.
Analyze a video transcript and identify segments where motion graphics would
meaningfully improve viewer comprehension or engagement.

Rules for selecting zones:
- Only where content is DATA-RICH (numbers, dates, comparisons, steps, names)
  or EMOTIONALLY KEY (a central thesis the viewer must not miss)
- Each zone covers 1-6 consecutive segments that share ONE idea
- Minimum zone duration: 4s. Maximum: 14s.
- Leave at least 30s between zones (based on start_time)
- Do NOT place zones in the first 15s or last 10s
- Aim for 2-5 zones total per video
- Prefer fewer, high-impact zones over many mediocre ones

Available Remotion compositions:

  DataCard   — 1-3 data items with animated counters and labels.
               Use when content has specific numbers, measurements, temperatures, percentages.
               Props schema:
               {
                 "title": "short section label (5-8 words max)",
                 "items": [
                   {
                     "label": "what this number means",
                     "value": "display string (e.g. '5.500°C', '×350')",
                     "color": "#hex (optional, pick from: #00D4FF #FFB347 #4DFFB4 #FF4444 #FFD700)",
                     "numeric": 5500,      // optional: if set, value animates from 0 to this integer
                     "suffix": "°C"        // optional: appended after animated numeric
                   }
                 ],
                 "duration_s": 8.0
               }

  Statement  — cinematic bold text reveal for a single key claim or thesis.
               Use for the central thesis, a dramatic fact, or a powerful conclusion.
               Props schema:
               {
                 "text": "the main sentence (keep under 80 chars for readability)",
                 "highlight": "one word within text to accent-color (omit if no obvious word)",
                 "sub": "optional smaller subtitle (source, context, etc.)",
                 "accent_color": "#hex (optional, default #00D4FF)",
                 "duration_s": 6.0
               }

  Timeline   — numbered steps with connector lines and descriptions.
               Use for processes, historical sequences, cause-effect chains (3-5 steps ideal).
               Props schema:
               {
                 "title": "short label for the sequence",
                 "steps": [
                   { "label": "step name", "desc": "optional one-line description" }
                 ],
                 "duration_s": 10.0
               }

  Comparison — VS layout: two values crash in from opposite sides.
               Use when directly contrasting two quantities, eras, or states.
               Props schema:
               {
                 "title": "optional section label",
                 "left":  { "label": "left name", "value": "value string", "sub": "optional note" },
                 "right": { "label": "right name", "value": "value string", "sub": "optional note" },
                 "left_color":  "#hex (warm: #FF6B35 #FFD700 #FF3CAC — default #FF6B35)",
                 "right_color": "#hex (cool: #00C8FF #4DFFB4 #A855F7 — default #00C8FF)",
                 "duration_s": 8.0
               }

  Highlight  — single massive fact / number, most dramatic of all.
               Use for ONE extraordinary number or fact the viewer must remember.
               Props schema:
               {
                 "value": "the number or short value (e.g. '15 Mio.' or '×1.000')",
                 "label": "what this value measures (5-6 words)",
                 "sub": "optional context sentence",
                 "accent_color": "#hex (pick vivid: #FF6B35 #00C8FF #FFD700 #A855F7)",
                 "duration_s": 7.0
               }

Choose the composition that BEST fits each zone's content:
- Multiple stats → DataCard
- Key thesis sentence → Statement
- Step-by-step process → Timeline
- Two things compared → Comparison
- One extraordinary number → Highlight

Return ONLY a JSON array, no markdown, no explanation:
[
  {
    "zone_id": 1,
    "seg_start": 12,
    "seg_end": 15,
    "start_time": 48.0,
    "end_time": 60.0,
    "duration": 12.0,
    "composition": "DataCard",
    "title": "short label for logs",
    "props": { }
  }
]"""

PLANNER_USER = """Transcript segments (index, start_s, end_s, text):
{segments_block}

Return the MG zone plan as JSON."""


# ─── helpers ──────────────────────────────────────────────────────────────────

def _ask(system: str, user: str) -> str:
    prompt = f"{system}\n\n{user}"
    env = os.environ.copy()
    env.pop("CLAUDECODE", None)
    r = subprocess.run(
        ["claude", "-p", prompt],
        capture_output=True, text=True,
        encoding="utf-8", errors="replace", env=env,
    )
    if r.returncode != 0:
        raise RuntimeError(f"claude CLI error {r.returncode}:\n{r.stderr[:400]}")
    return r.stdout.strip()


def _segments_block(segments: list, start_idx: int = 0, end_idx: int | None = None) -> str:
    end_idx = end_idx if end_idx is not None else len(segments) - 1
    lines = []
    for i, seg in enumerate(segments):
        if i < start_idx or i > end_idx:
            continue
        lines.append(f"  [{i:03d}] {seg['start']:.1f}s-{seg['end']:.1f}s: {seg['text']}")
    return "\n".join(lines)


def _strip_fences(text: str) -> str:
    text = text.strip()
    if text.startswith("```"):
        text = text.split("\n", 1)[1]
        text = text.rsplit("```", 1)[0]
    return text.strip()


# ─── planning ─────────────────────────────────────────────────────────────────

def plan_zones(segments: list) -> list[dict]:
    print("[PLANNER] Analysing transcript for MG zones...")
    block = _segments_block(segments)
    raw   = _ask(PLANNER_SYSTEM, PLANNER_USER.format(segments_block=block))
    zones = json.loads(_strip_fences(raw))

    print(f"[PLANNER] {len(zones)} zones identified:")
    for z in zones:
        print(f"  Zone {z['zone_id']}: segs [{z['seg_start']}-{z['seg_end']}] "
              f"{z['start_time']:.0f}s-{z['end_time']:.0f}s  "
              f"{z['composition']}  — {z['title']}")
    return zones


# ─── main ─────────────────────────────────────────────────────────────────────

def run(channel: str, session: str, force: bool = False):
    channel_id  = CHANNEL_MAP.get(channel, channel)
    session_dir = get_session_dir(channel_id, session)
    result_json = session_dir / "transcripts" / "result.json"
    plan_path   = session_dir / "motion_graphics_plan.json"

    if not result_json.exists():
        print(f"ERROR: {result_json} not found")
        sys.exit(1)

    data     = json.loads(result_json.read_text(encoding="utf-8"))
    segments = data.get("segments", data) if isinstance(data, dict) else data

    if plan_path.exists() and not force:
        print(f"[PLANNER] Loading existing plan: {plan_path}")
        return json.loads(plan_path.read_text(encoding="utf-8"))

    zones = plan_zones(segments)

    # ensure duration is set in props
    for zone in zones:
        props = zone.get("props", {})
        if "duration_s" not in props:
            props["duration_s"] = round(zone.get("duration", 8.0), 2)
        zone["props"] = props

    plan = {
        "channel_id": channel_id,
        "session":    session,
        "zones":      zones,
    }
    plan_path.write_text(json.dumps(plan, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"[PLANNER] Plan saved → {plan_path}")
    return plan


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--channel", default="de")
    ap.add_argument("--session", required=True)
    ap.add_argument("--force",   action="store_true", help="Re-plan even if plan exists")
    args = ap.parse_args()
    run(args.channel, args.session, force=args.force)
