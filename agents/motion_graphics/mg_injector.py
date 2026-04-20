"""
mg_injector.py — патчит clip_selection.json: добавляет mg_overrides
так что assembler использует MG-клипы вместо библиотечных для нужных сегментов.

Usage:
    python mg_injector.py --channel de --session Video_20260415_193110
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
    channel_id  = CHANNEL_MAP.get(channel, channel)
    session_dir = get_session_dir(channel_id, session)
    plan_path   = session_dir / "motion_graphics_plan.json"
    cs_path     = session_dir / "clip_selection.json"

    if not plan_path.exists():
        print(f"ERROR: {plan_path} not found. Run mg_planner + mg_renderer first.")
        sys.exit(1)

    if not cs_path.exists():
        print(f"ERROR: {cs_path} not found.")
        sys.exit(1)

    plan = json.loads(plan_path.read_text(encoding="utf-8"))
    cs   = json.loads(cs_path.read_text(encoding="utf-8"))

    # Загружаем result.json чтобы знать тайминги сегментов
    from pathlib import Path as _Path
    import sys as _sys
    ROOT = _Path(__file__).resolve().parents[2]
    _sys.path.insert(0, str(ROOT / "agents" / "utils"))
    from paths import get_result_json
    result_path = get_result_json(channel_id, session)
    if not result_path.exists():
        print(f"ERROR: {result_path} not found.")
        sys.exit(1)
    result = json.loads(result_path.read_text(encoding="utf-8"))
    # {seg_id: (start, end)} — индекс по seg_id
    seg_times = {
        seg["id"]: (float(seg["start"]), float(seg["end"]))
        for seg in result.get("segments", [])
    }

    # build override map: seg_id (str) → rendered mp4 path
    # Зона покрывает все сегменты, чей start попадает в [zone.start_time, zone.end_time]
    mg_overrides = {}
    for zone in plan["zones"]:
        rendered = zone.get("rendered_path")
        if not rendered:
            print(f"  [skip] zone {zone['zone_id']} has no rendered_path")
            continue
        if not Path(rendered).exists():
            print(f"  [skip] zone {zone['zone_id']} file missing: {rendered}")
            continue
        z_start = float(zone["start_time"])
        z_end   = float(zone["end_time"])
        matched = []
        for seg_id, (t_start, t_end) in seg_times.items():
            if t_start >= z_start and t_start < z_end:
                mg_overrides[str(seg_id)] = rendered
                matched.append(seg_id)
        if not matched:
            # fallback: ближайший сегмент по старту
            closest = min(seg_times.items(), key=lambda x: abs(x[1][0] - z_start))
            mg_overrides[str(closest[0])] = rendered
            matched = [closest[0]]
        print(f"  Zone {zone['zone_id']} ({z_start:.0f}-{z_end:.0f}s): "
              f"segs {matched} -> {Path(rendered).name}")

    if not mg_overrides:
        print("[INJECTOR] No overrides to apply.")
        return

    existing = cs.get("mg_overrides", {})
    existing.update(mg_overrides)
    cs["mg_overrides"] = existing

    cs_path.write_text(json.dumps(cs, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"[INJECTOR] Patched {len(mg_overrides)} segments in clip_selection.json")


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--channel", default="de")
    ap.add_argument("--session", required=True)
    args = ap.parse_args()
    run(args.channel, args.session)
