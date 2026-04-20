"""
Запуск clip selection для сессии (standalone, без рендера).
Использование:
  python agents/library/run_clip_selection.py --channel channel_001_cosmos_de --session Video_20260415_210049
"""
import argparse, json, sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "agents" / "utils"))
sys.path.insert(0, str(ROOT / "agents" / "library"))

from paths import CHANNELS_DIR, get_lang
from clip_selector import select_clips_for_video

ap = argparse.ArgumentParser()
ap.add_argument("--channel", default="channel_001_cosmos_de")
ap.add_argument("--session", required=True)
ap.add_argument("--intro_duration", type=float, default=90.0)
args = ap.parse_args()

lang        = get_lang(args.channel)
sess_dir    = CHANNELS_DIR / lang / args.session
result_json = sess_dir / "transcripts" / "result.json"

if not result_json.exists():
    print(f"[ERROR] No result.json at {result_json}")
    sys.exit(1)

result = json.loads(result_json.read_text(encoding="utf-8"))
segments = result.get("segments", [])
print(f"Loaded {len(segments)} segments from {result_json}")

selection = select_clips_for_video(
    session=args.session,
    channel_id=args.channel,
    segments=segments,
    max_repeats_in_video=3,
    max_from_prev=20,
    intro_duration=args.intro_duration,
    text_only=False,
)

main_clips  = selection.get("main_clips", [])
intro_clips = selection.get("intro_clips", [])

print(f"\n=== RESULTS ===")
print(f"Main clips:  {len(main_clips)}")
print(f"Intro clips: {len(intro_clips)}")
print(f"\nFirst 10 main clips:")
for seg_id, clip_id, dur in main_clips[:10]:
    print(f"  seg {seg_id:3d} -> clip {clip_id}  ({dur:.1f}s)")
