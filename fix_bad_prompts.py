"""
Регенерирует плохие видео-промпты (#011-#019) для сессии Video_20260310_195835.
Также исправляет #020 (убирает markdown заголовок).
"""
import json, os, pathlib, re, subprocess, sys

BASE       = pathlib.Path(__file__).parent
SESSION    = "Video_20260310_195835"
PROMPTS    = BASE / "data" / "prompts" / SESSION
VIDEO_DIR  = PROMPTS / "video"
PHOTO_DIR  = PROMPTS / "photo"
MASTER     = BASE / "config" / "master_prompts" / "video" / "master_video_grok.txt"

BAD_IDS    = list(range(11, 20))   # 11..19 включительно
ALSO_FIX_20 = True                 # убрать markdown-заголовок из id=20

# ── загружаем мастер ─────────────────────────────────────────────────────────
master_text = MASTER.read_text(encoding="utf-8").strip()

# ── загружаем photo prompts ───────────────────────────────────────────────────
photo_json = json.loads((PHOTO_DIR / "photo_prompts.json").read_text(encoding="utf-8"))
photo_by_id = {item["id"]: item["photo_prompt"] for item in photo_json}

# ── строим промпт для Claude ──────────────────────────────────────────────────
lines = []
for sid in BAD_IDS:
    pp = photo_by_id.get(sid, "")
    lines.append(f"SEGMENT {sid} (Photo Prompt)")
    lines.append(pp)
    lines.append("")

full_prompt = master_text + "\n\nТеперь создай промпты для этих сегментов:\n" + "\n".join(lines)

print(f"Запрашиваю Claude для сегментов {BAD_IDS[0]}–{BAD_IDS[-1]}...")

# ── вызов claude CLI ──────────────────────────────────────────────────────────
env = os.environ.copy()
env.pop("CLAUDECODE", None)
_CLAUDE_DIR = pathlib.Path(os.environ.get("APPDATA", "")) / "Claude/claude-code"
claude_exe = "claude"
if _CLAUDE_DIR.exists():
    versions = sorted(_CLAUDE_DIR.iterdir(), reverse=True)
    for v in versions:
        exe = v / "claude.exe"
        if exe.exists():
            env["PATH"] = str(v) + os.pathsep + env.get("PATH", "")
            claude_exe = str(exe)
            break

print(f"Используем claude: {claude_exe}")
r = subprocess.run([claude_exe, "-p", full_prompt],
                   capture_output=True, text=True,
                   encoding="utf-8", errors="replace", env=env)
if r.returncode != 0:
    print(f"ОШИБКА claude CLI (код {r.returncode}):\n{r.stderr[:400]}")
    sys.exit(1)

raw = r.stdout.strip()
print(f"Ответ Claude: {len(raw)} символов")
print("--- первые 300 символов ---")
print(raw[:300])
print("---")

# ── парсим ответ ─────────────────────────────────────────────────────────────
def parse_video_output(text: str, n: int) -> list[str]:
    results = []
    parts = re.split(r"\bSEGMENT\s+\d+\s*\(Photo Prompt\)", text, flags=re.IGNORECASE)
    segment_parts = [p.strip() for p in parts[1:] if p.strip()]
    for part in segment_parts:
        m = re.search(r"Final\s+Video\s+Prompt[^\S\n]*[:\-]?\s*\n", part, re.IGNORECASE)
        if not m:
            m = re.search(r"\bVideo\s+Prompt[^\S\n]*[:\-]?\s*\n", part, re.IGNORECASE)
        if m:
            vp = part[m.end():].strip()
        else:
            paras = [p.strip() for p in part.split("\n\n") if p.strip()]
            vp = paras[-1] if paras else part.strip()
        results.append(vp)
    return results

new_prompts = parse_video_output(raw, len(BAD_IDS))
if len(new_prompts) != len(BAD_IDS):
    print(f"ОШИБКА: ожидалось {len(BAD_IDS)}, получено {len(new_prompts)}")
    print("Полный ответ:")
    print(raw)
    sys.exit(1)

print(f"\nПолучено {len(new_prompts)} промптов:")
for sid, p in zip(BAD_IDS, new_prompts):
    print(f"  #{sid:03d} ({len(p)}c): {repr(p[:80])}")

# ── обновляем JSON ────────────────────────────────────────────────────────────
video_json_path = VIDEO_DIR / "video_prompts.json"
video_data = json.loads(video_json_path.read_text(encoding="utf-8"))

new_by_id = dict(zip(BAD_IDS, new_prompts))
updated = 0
for item in video_data:
    if item["id"] in new_by_id:
        item["video_prompt"] = new_by_id[item["id"]]
        updated += 1
    elif ALSO_FIX_20 and item["id"] == 20:
        # Убираем markdown заголовок "**Final Video Prompt**\n"
        p = item["video_prompt"]
        p = re.sub(r"^\*\*Final Video Prompt\*\*\n", "", p, flags=re.IGNORECASE)
        item["video_prompt"] = p.strip()
        print(f"  #020 markdown-заголовок убран, осталось ({len(item['video_prompt'])}c)")

print(f"\nОбновлено в JSON: {updated} записей")
video_json_path.write_text(json.dumps(video_data, ensure_ascii=False, indent=2), encoding="utf-8")

# ── обновляем TXT ─────────────────────────────────────────────────────────────
all_prompts = [item["video_prompt"] for item in video_data]
txt_lines   = [p.strip().replace("\n\n", "\n") for p in all_prompts]
video_txt_path = VIDEO_DIR / "video_prompts.txt"
video_txt_path.write_text("\n\n".join(txt_lines), encoding="utf-8")
print(f"TXT обновлён: {video_txt_path}")

# ── финальная проверка ────────────────────────────────────────────────────────
print("\n=== Проверка ===")
for item in video_data[10:21]:
    print(f"  #{item['id']:03d} ({len(item['video_prompt'])}c): {repr(item['video_prompt'][:70])}")
