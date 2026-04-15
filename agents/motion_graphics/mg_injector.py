"""
mg_injector.py — патчит clip_selection.json: добавляет mg_overrides
чтобы assembler использовал MG-клипы вместо библиотечных для нужных сегментов.

Usage:
    python mg_injector.py --channel fr --session Video_20260414_193110
"""

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
from agents.utils.paths import get_session_dir


CHANNEL_MAP = {
    "fr": "channel_002_cosmos_fr",
    "de": "channel_001_cosmos_de",
    "es": "channel_003_religion_es",
}


def run(channel: str, session: str):
    channel_id   = CHANNEL_MAP.get(channel, channel)
    session_dir  = get_session_dir(channel_id, session)
    plan_path    = session_dir / "motion_graphics_plan.json"
    cs_path      = session_dir / "clip_selection.json"

    if not plan_path.exists():
        print(f"ERROR: {plan_path} not found. Run mg_planner + mg_renderer first.")
        sys.exit(1)

    if not cs_path.exists():
        print(f"ERROR: {cs_path} not found.")
        sys.exit(1)

    plan = json.loads(plan_path.read_text(encoding="utf-8"))
    cs   = json.loads(cs_path.read_text(encoding="utf-8"))

    # build override map: seg_id (int) → rendered mp4 path
    mg_overrides = {}
    for zone in plan["zones"]:
        if not zone.get("rendered_path"):
            print(f"  [skip] zone {zone['zone_id']} has no rendered file")
            continue

        rendered = Path(zone["rendered_path"])
        if not rendered.exists():
            print(f"  [skip] zone {zone['zone_id']} file missing: {rendered}")
            continue

        # every seg_id in this zone maps to the same MG clip
        # seg_start and seg_end are inclusive indices into the segments array
        for seg_idx in range(zone["seg_start"], zone["seg_end"] + 1):
            mg_overrides[str(seg_idx)] = str(rendered)

    if not mg_overrides:
        print("[INJECTOR] No overrides to apply.")
        return

    # merge with existing overrides (if any)
    existing = cs.get("mg_overrides", {})
    existing.update(mg_overrides)
    cs["mg_overrides"] = existing

    cs_path.write_text(json.dumps(cs, ensure_ascii=False, indent=2), encoding="utf-8")

    print(f"[INJECTOR] Patched {len(mg_overrides)} segments in clip_selection.json")
    for zone in plan["zones"]:
        if zone.get("rendered_path"):
            print(f"  Zone {zone['zone_id']} segs {zone['seg_start']}-{zone['seg_end']}: "
                  f"{Path(zone['rendered_path']).name}")


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--channel",  default="fr")
    ap.add_argument("--session",  required=True)
    args = ap.parse_args()
    run(args.channel, args.session)
