"""
test_fr_word_hl.py — изолированный тест word_hl (karaoke) субтитров для FR канала.

Рендерит несколько кадров для каждой группы слов и сохраняет в PNG.
Запуск:  python test_fr_word_hl.py
"""
import sys, os
sys.path.insert(0, "agents/assembler")
sys.path.insert(0, "agents/utils")
sys.stdout.reconfigure(encoding="utf-8", errors="replace")

from pathlib import Path
import numpy as np
from PIL import Image
import torch

from gpu_compositor import (
    parse_ass, prerender_subtitles, composite_subtitles, SubtitleIndex,
    ANIM_WORD_HL, W, H, DEVICE,
)

# ─── Пути ────────────────────────────────────────────────────────────────────
ASS_FILE = Path(r"D:\Video-pipeline-data\channels\fr\Video_MINI_FR_TEST\transcripts\subtitles_animated.ass")
OUT_DIR  = Path(r"D:\Video-pipeline-temp\test_word_hl_fr")
OUT_DIR.mkdir(parents=True, exist_ok=True)

FPS      = 25.0
FONT_SZ  = 52

# ─── Загружаем ASS ──────────────────────────────────────────────────────────
events = parse_ass(str(ASS_FILE))
print(f"ASS events: {len(events)}")

# Берём только word_hl события (у них есть 'words' поле с >1 словом)
word_hl_events = [e for e in events if e.get("words") and len(e["words"]) > 1]
print(f"word_hl groups: {len(word_hl_events)}")
for i, ev in enumerate(word_hl_events[:5]):
    words = [w["text"] for w in ev["words"]]
    print(f"  [{i}] t={ev['start']:.2f}s  '{' | '.join(words)}'")

# ─── Prerендер субтитров ─────────────────────────────────────────────────────
print(f"\nPrerender subtitles ({DEVICE})...")
rendered = prerender_subtitles(
    events        = events,
    font_size     = FONT_SZ,
    fps           = FPS,
    fade_ms       = 100,
    subtitle_anim = ANIM_WORD_HL,
)
print(f"Prerendered: {len(rendered)} events")

# Проверяем, что все word_hl canvases одинаковой высоты
hl_rendered = [r for r in rendered if r.get("anim") == ANIM_WORD_HL]
print(f"word_hl rendered: {len(hl_rendered)}")
heights = set()
for ev_r in hl_rendered:
    for bt in ev_r["bright_tensors"]:
        heights.add(bt.shape[0])
print(f"Canvas heights in word_hl tensors: {heights}  (должна быть 1 высота!)")

# ─── Рендер кадров ──────────────────────────────────────────────────────────
idx = SubtitleIndex(rendered)

saved = 0
for i, ev in enumerate(word_hl_events[:8]):  # первые 8 групп
    words = [w["text"] for w in ev["words"]]
    t_mid = (ev["start"] + ev["end"]) / 2.0  # середина события
    frame_num = int(t_mid * FPS)

    # Пустой чёрный кадр
    bg = torch.zeros((H, W, 3), dtype=torch.float32, device=DEVICE)

    # Composite
    frame = composite_subtitles(bg, frame_num, idx)
    frame_np = (frame.cpu().numpy() * 255).clip(0, 255).astype(np.uint8)

    # Сохраняем PNG
    img = Image.fromarray(frame_np, "RGB")
    fname = OUT_DIR / f"group_{i:02d}_t{t_mid:.1f}s.png"
    img.save(fname)
    print(f"  Saved: {fname.name}  words={words}  frame={frame_num}")
    saved += 1

print(f"\n{'='*60}")
print(f"Готово! {saved} PNG в {OUT_DIR}")
print(f"{'='*60}")
print("\nЧТО ПРОВЕРЯТЬ:")
print("  - Все слова группы на одной высоте (нет прыжков)")
print("  - Baseline выровнен между словами с капсом и строчными")
print("  - Жёлтое (активное) слово примерно посередине по времени")
