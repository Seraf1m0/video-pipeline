"""Сравнение E5 vs Gemini clip selection. Пишет в compare_out.txt."""
import json, sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "agents" / "utils"))

from paths import CHANNELS_DIR, get_lang, get_library_json

CHANNEL = "channel_001_cosmos_de"
SESSION = "Video_20260415_210049"
lang = get_lang(CHANNEL)
sess_dir = CHANNELS_DIR / lang / SESSION

old_path = sess_dir / "clip_selection_e5_old.json"
new_path = sess_dir / "clip_selection.json"

old = json.loads(old_path.read_text(encoding="utf-8")) if old_path.exists() else None
new = json.loads(new_path.read_text(encoding="utf-8")) if new_path.exists() else None

lib = json.loads(get_library_json(CHANNEL).read_text(encoding="utf-8"))
clips_meta = lib["clips"]

def clip_info(cid):
    if cid is None: return "None"
    entry = clips_meta.get(str(cid), {})
    kw = entry.get("keywords", "")[:80]
    return f"{cid}: {kw}"

result_json = sess_dir / "transcripts" / "result.json"
result = json.loads(result_json.read_text(encoding="utf-8"))
segs_map = {str(s["id"]): s.get("text","") for s in result.get("segments", [])}

out_path = ROOT / "compare_out.txt"
lines = ["=== E5 vs GEMINI ===\n"]

if old and new:
    old_main = {str(x[0]): str(x[1]) for x in old.get("main_clips", [])}
    new_main = {str(x[0]): str(x[1]) for x in new.get("main_clips", [])}

    shown = 0
    for seg_id in sorted(old_main.keys(), key=lambda x: int(x)):
        old_clip = old_main.get(seg_id)
        new_clip = new_main.get(seg_id)
        if old_clip != new_clip:
            seg_text = segs_map.get(seg_id, "")[:60]
            lines.append(f"[seg {seg_id}] {seg_text!r}")
            lines.append(f"  E5:     {clip_info(old_clip)}")
            lines.append(f"  Gemini: {clip_info(new_clip)}")
            lines.append("")
            shown += 1
            if shown >= 30:
                break

    total_changed = sum(1 for s in old_main if old_main.get(s) != new_main.get(s, old_main[s]))
    lines.append(f"Total changed: {total_changed}/{len(old_main)}")

elif new:
    for seg_id, clip_id, dur in new.get("main_clips", [])[:30]:
        seg_text = segs_map.get(str(seg_id), "")[:50]
        lines.append(f"[{seg_id}] {seg_text!r} -> {clip_info(clip_id)}")

out_path.write_text("\n".join(lines), encoding="utf-8")
print(f"Written to {out_path}")
