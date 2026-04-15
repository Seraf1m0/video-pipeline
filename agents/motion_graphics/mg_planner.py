"""
mg_planner.py — анализирует сегменты через Claude, планирует MG-зоны,
генерирует Manim-код для каждой зоны.

Usage:
    python mg_planner.py --channel fr --session Video_20260414_193110
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
- Minimum zone duration: 3s. Maximum: 22s
- Leave at least 30s between zones
- Aim for 3-7 zones total per video
- Do NOT place zones in the first 15s or last 10s

For each zone, choose one animation type:
  counter   — number(s) animate up from zero; use when there are specific figures
  timeline  — horizontal phases/steps with labels; use for multi-phase plans
  statement — single powerful phrase revealed dramatically; use for key thesis
  push      — bold fast reveal of 1-2 key terms; use for short punchy moments
  split     — two-column comparison; use when two sides are explicitly contrasted
  infograph — custom layout combining labels, values, arrows; use for complex data

Return ONLY a JSON array, no markdown, no explanation:
[
  {
    "zone_id": 1,
    "seg_start": 12,
    "seg_end": 15,
    "start_time": 48.0,
    "end_time": 60.0,
    "duration": 12.0,
    "anim_type": "timeline",
    "title": "short label",
    "description": "what this animation should show and why",
    "data": { "any relevant structured data extracted from the segments" }
  }
]"""

PLANNER_USER = """Transcript segments (index, start_s, end_s, text):
{segments_block}

Return the MG zone plan as JSON."""


CODEGEN_SYSTEM = """You are a professional Manim animation developer.
Generate a complete, runnable Python Manim scene.

TECHNICAL CONSTRAINTS:
- Frame: 1920×1080 (16:9). Coordinate system: x∈[-7.1,7.1], y∈[-4.0,4.0]
- FPS: 25. Duration: EXACTLY {duration:.2f}s
- Dark opaque background — no transparency needed
- Only allowed import: `from manim import *`
- NO ImageMobject, NO SVGMobject, NO external files
- Class name must be: MGScene

TEXT SAFETY — broken text ruins the clip:
- Safe zone: x∈[-6.2,6.2], y∈[-3.4,3.4]
- After creating any Text: if obj.width > 12.0: obj.scale(12.0 / obj.width)
- Split long phrases with \\n (max ~40 chars per line for body, ~25 for headlines)
- font="Montserrat" for all text (always available)
- font_size caps: main headline≤84, sub-headline≤52, body≤38, caption≤28

TIMING — must be exact:
- Sum of all run_time values + all self.wait() values = {duration:.2f}s
- Keep a running total as you write. Adjust final self.wait() to hit the target.
- Min hold between beats: 1.2s

ANIMATION QUALITY:
- Always use rate_func=rate_functions.ease_in_out_sine on every animation
- FadeIn / FadeOut run_time: 0.5s for primary elements, 0.3s for secondary
- For counters: use ValueTracker + always_redraw(lambda: Integer(...))
- Exits: FadeOut everything at end (run_time=0.4)

VISUAL STYLE:
- Background rect first: Rectangle(width=16, height=9).set_fill("#050A14", opacity=1).set_stroke(width=0)
- Palette: CYAN="#00D4FF", WARM="#FFB347", GREEN="#4DFFB4", WHITE="#FFFFFF", GREY="#556677"
- Cards: RoundedRectangle(corner_radius=0.22) with fill "#0D1526", opacity=0.92, stroke CYAN width=1.5
- Add subtle depth: faint horizontal lines or dot grid in background at opacity=0.07
- For push/statement: make it feel cinematic — large text, breathing room, no clutter

RETURN: Only the Python code. No markdown fences. No explanation."""

CODEGEN_USER = """Animation zone:
  Type: {anim_type}
  Title: {title}
  Description: {description}
  Data: {data}
  Duration: {duration:.2f}s

Segments covered:
{segments_block}

Write the MGScene class."""


# ─── helpers ──────────────────────────────────────────────────────────────────

def _ask(system: str, user: str, max_tokens: int = 4096) -> str:
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


# ─── main pipeline ────────────────────────────────────────────────────────────

def plan_zones(segments: list) -> list[dict]:
    """Ask Claude to identify MG zones in the transcript."""
    print("[PLANNER] Analysing transcript for MG zones...")
    block = _segments_block(segments)
    raw = _ask(PLANNER_SYSTEM, PLANNER_USER.format(segments_block=block))

    # strip any accidental markdown fences
    raw = raw.strip()
    if raw.startswith("```"):
        raw = raw.split("\n", 1)[1]
        raw = raw.rsplit("```", 1)[0]

    zones = json.loads(raw)
    print(f"[PLANNER] {len(zones)} zones identified")
    for z in zones:
        print(f"  Zone {z['zone_id']}: [{z['seg_start']}-{z['seg_end']}] "
              f"{z['start_time']:.0f}s-{z['end_time']:.0f}s  {z['anim_type']}  — {z['title']}")
    return zones


def generate_code(zone: dict, segments: list) -> str:
    """Ask Claude to write Manim code for one zone."""
    print(f"[CODEGEN] Zone {zone['zone_id']}: {zone['title']} ({zone['duration']:.1f}s)...")
    block = _segments_block(segments, zone["seg_start"], zone["seg_end"])

    system = CODEGEN_SYSTEM.format(duration=zone["duration"])
    user   = CODEGEN_USER.format(
        anim_type      = zone["anim_type"],
        title          = zone["title"],
        description    = zone["description"],
        data           = json.dumps(zone.get("data", {}), ensure_ascii=False),
        duration       = zone["duration"],
        segments_block = block,
    )
    code = _ask(system, user, max_tokens=6000)

    # strip markdown fences if present
    if code.startswith("```"):
        code = code.split("\n", 1)[1]
        code = code.rsplit("```", 1)[0].strip()

    return code


def run(channel: str, session: str, force: bool = False):
    channel_id  = CHANNEL_MAP.get(channel, channel)
    session_dir = get_session_dir(channel_id, session)
    result_json = session_dir / "transcripts" / "result.json"
    plan_path   = session_dir / "motion_graphics_plan.json"
    mg_temp_dir = TEMP_ROOT / channel_id / session / "motion_graphics"
    mg_temp_dir.mkdir(parents=True, exist_ok=True)

    if not result_json.exists():
        print(f"ERROR: {result_json} not found")
        sys.exit(1)

    data     = json.loads(result_json.read_text(encoding="utf-8"))
    segments = data.get("segments", data) if isinstance(data, dict) else data

    if plan_path.exists() and not force:
        print(f"[PLANNER] Loading existing plan: {plan_path}")
        plan = json.loads(plan_path.read_text(encoding="utf-8"))
    else:
        zones = plan_zones(segments)

        # generate Manim code for each zone
        for zone in zones:
            code_path = mg_temp_dir / f"zone_{zone['zone_id']:02d}.py"
            if code_path.exists() and not force:
                print(f"[CODEGEN] Zone {zone['zone_id']} code exists, skipping")
                zone["code_path"] = str(code_path)
                continue
            code = generate_code(zone, segments)
            code_path.write_text(code, encoding="utf-8")
            zone["code_path"] = str(code_path)
            print(f"  -> {code_path}")

        plan = {
            "channel_id": channel_id,
            "session":    session,
            "zones":      zones,
        }
        plan_path.write_text(json.dumps(plan, ensure_ascii=False, indent=2), encoding="utf-8")
        print(f"[PLANNER] Plan saved: {plan_path}")

    return plan


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--channel",  default="fr")
    ap.add_argument("--session",  required=True)
    ap.add_argument("--force",    action="store_true", help="Re-plan even if plan exists")
    args = ap.parse_args()
    run(args.channel, args.session, force=args.force)
