"""
mg_renderer.py — рендерит Manim-сцены из плана.
При ошибке рендера отдаёт код + traceback обратно Claude для фикса (до 3 попыток).

Usage:
    python mg_renderer.py --channel fr --session Video_20260414_193110
"""

import argparse
import json
import shutil
import subprocess
import sys
import textwrap
import traceback
from pathlib import Path

import os

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

MAX_RETRIES = 3


# ─── fix prompt ───────────────────────────────────────────────────────────────

FIX_SYSTEM = """You are a Manim debugging expert.
Fix the Python Manim code so it renders correctly.

Rules:
- Keep the class name MGScene
- Keep the same animation intent and duration
- Fix ALL errors shown in the traceback
- Return ONLY the fixed Python code, no markdown, no explanation"""

FIX_USER = """The following Manim code failed to render.

ERROR:
{error}

ORIGINAL CODE:
{code}

Return the fixed MGScene code."""


def _fix_code(code: str, error: str) -> str:
    prompt = f"{FIX_SYSTEM}\n\n{FIX_USER.format(error=error, code=code)}"
    env = os.environ.copy()
    env.pop("CLAUDECODE", None)
    r = subprocess.run(
        ["claude", "-p", prompt],
        capture_output=True, text=True,
        encoding="utf-8", errors="replace", env=env,
    )
    if r.returncode != 0:
        raise RuntimeError(f"claude CLI error {r.returncode}:\n{r.stderr[:400]}")
    fixed = r.stdout.strip()
    if fixed.startswith("```"):
        fixed = fixed.split("\n", 1)[1]
        fixed = fixed.rsplit("```", 1)[0].strip()
    return fixed


# ─── render one zone ──────────────────────────────────────────────────────────

def render_zone(zone: dict, mg_dir: Path) -> Path | None:
    """Render one zone. Returns path to output mp4, or None on failure."""
    zone_id   = zone["zone_id"]
    code_path = Path(zone["code_path"])
    out_path  = mg_dir / f"zone_{zone_id:02d}.mp4"

    if out_path.exists():
        print(f"  [skip] zone_{zone_id:02d}.mp4 already rendered")
        return out_path

    code = code_path.read_text(encoding="utf-8")

    for attempt in range(1, MAX_RETRIES + 1):
        print(f"  [zone {zone_id}] render attempt {attempt}/{MAX_RETRIES}...")

        # write code to a temp file (Manim needs a file, not stdin)
        tmp_py = mg_dir / f"_zone_{zone_id:02d}_attempt{attempt}.py"
        tmp_py.write_text(code, encoding="utf-8")

        media_dir = mg_dir / f"manim_media_{zone_id}"

        result = subprocess.run(
            [
                sys.executable, "-m", "manim",
                "-qh",
                "--fps", "25",
                "--media_dir", str(media_dir),
                "--output_file", f"zone_{zone_id:02d}",
                str(tmp_py), "MGScene",
            ],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
        )

        # find output file (manim puts it in media_dir/videos/...)
        candidates = list(media_dir.rglob(f"zone_{zone_id:02d}.mp4"))
        if not candidates:
            candidates = [p for p in media_dir.rglob("*.mp4") if "MGScene" in p.name or f"zone_{zone_id}" in p.name]

        if result.returncode == 0 and candidates:
            shutil.copy(candidates[0], out_path)
            print(f"  [zone {zone_id}] OK -> {out_path.name}")
            # cleanup media dir to save space
            shutil.rmtree(media_dir, ignore_errors=True)
            return out_path

        # failed — collect error
        stderr = (result.stderr or "")[-3000:]  # last 3000 chars
        stdout = (result.stdout or "")[-1000:]
        error  = f"STDOUT:\n{stdout}\n\nSTDERR:\n{stderr}"
        print(f"  [zone {zone_id}] FAILED (attempt {attempt}):\n{textwrap.indent(stderr[-500:], '    ')}")

        if attempt < MAX_RETRIES:
            print(f"  [zone {zone_id}] Asking Claude to fix...")
            code = _fix_code(code, error)
            # save fixed code back
            code_path.write_text(code, encoding="utf-8")

    print(f"  [zone {zone_id}] GAVE UP after {MAX_RETRIES} attempts")
    return None


# ─── main ─────────────────────────────────────────────────────────────────────

def run(channel: str, session: str):
    channel_id  = CHANNEL_MAP.get(channel, channel)
    session_dir = get_session_dir(channel_id, session)
    plan_path   = session_dir / "motion_graphics_plan.json"
    mg_dir      = TEMP_ROOT / channel_id / session / "motion_graphics"
    mg_dir.mkdir(parents=True, exist_ok=True)

    if not plan_path.exists():
        print(f"ERROR: {plan_path} not found. Run mg_planner.py first.")
        sys.exit(1)

    plan  = json.loads(plan_path.read_text(encoding="utf-8"))
    zones = plan["zones"]

    print(f"[RENDERER] {len(zones)} zones to render")

    results = []
    for zone in zones:
        mp4 = render_zone(zone, mg_dir)
        zone["rendered_path"] = str(mp4) if mp4 else None
        results.append(zone)

    # save updated plan with rendered paths
    plan["zones"] = results
    plan_path.write_text(json.dumps(plan, ensure_ascii=False, indent=2), encoding="utf-8")

    ok  = sum(1 for z in results if z["rendered_path"])
    bad = len(results) - ok
    print(f"\n[RENDERER] Done: {ok} OK, {bad} failed")
    if bad:
        print("  Failed zones:", [z["zone_id"] for z in results if not z["rendered_path"]])

    return plan


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--channel",  default="fr")
    ap.add_argument("--session",  required=True)
    args = ap.parse_args()
    run(args.channel, args.session)
