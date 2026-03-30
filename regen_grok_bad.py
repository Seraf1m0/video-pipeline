"""
regen_grok_bad.py — перегенерация невалидных видео через Grok.

Читает video_validation_report.json, переименовывает плохие видео
в videos_upscaled/, запускает generate_grok_video для конкретных индексов.

Запуск:
  py regen_grok_bad.py --session Video_20260317_205803 --channel channel_001_cosmos_de
"""

import argparse
import json
import sys
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent

# ── Пути агентов ───────────────────────────────────────────────────────────────
_MG_DIR = BASE_DIR / "agents" / "media_generator"
if str(_MG_DIR) not in sys.path:
    sys.path.insert(0, str(_MG_DIR))

_BOT_DIR = BASE_DIR / "bot"
if str(_BOT_DIR) not in sys.path:
    sys.path.insert(0, str(_BOT_DIR))

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")


def _get_channel_base(channel_id: str) -> Path:
    config_path = BASE_DIR / "bot" / "channels" / channel_id / "config.json"
    try:
        cfg    = json.loads(config_path.read_text(encoding="utf-8"))
        subdir = cfg.get("data_subdir", "").strip()
        if subdir:
            return BASE_DIR / "data" / subdir
    except Exception:
        pass
    return BASE_DIR / "data"


def run(session: str, channel_id: str) -> None:
    ch_base        = _get_channel_base(channel_id)
    ch_transcripts = ch_base / "transcripts"
    ch_media       = ch_base / "media"
    ch_prompts     = ch_base / "prompts"

    # ── Отчёт ─────────────────────────────────────────────────────────────────
    report_path = ch_transcripts / session / "video_validation_report.json"
    if not report_path.exists():
        print(f"[!] Отчёт не найден: {report_path}")
        sys.exit(1)

    report        = json.loads(report_path.read_text(encoding="utf-8"))
    invalid_ids   = {v["id"] for v in report["videos"] if not v["valid"]}
    print(f"  Невалидных видео: {len(invalid_ids)}: {sorted(invalid_ids)}")

    # ── Загрузка промптов из JSON ──────────────────────────────────────────────
    prompts_json = ch_prompts / session / "video" / "video_prompts.json"
    if not prompts_json.exists():
        print(f"[!] video_prompts.json не найден: {prompts_json}")
        sys.exit(1)

    prompts_data = json.loads(prompts_json.read_text(encoding="utf-8"))
    # id → video_prompt
    prompt_map: dict[int, str] = {
        item["id"]: item.get("video_prompt", item.get("text", ""))
        for item in prompts_data
    }
    total = len(prompts_data)

    # ── Загрузка фото ──────────────────────────────────────────────────────────
    photos_dir = ch_media / session / "photos"
    photos = sorted(
        [p for p in photos_dir.iterdir()
         if p.stem.startswith("photo_") and p.suffix.lower() in (".png", ".jpg", ".jpeg", ".webp")],
        key=lambda p: p.stem,
    )
    print(f"  Фото: {len(photos)}, промпты: {total}")

    if len(photos) < total:
        print(f"[!] Фото ({len(photos)}) меньше промптов ({total}) — могут быть пропуски")

    # Дополняем до total элементов (None если нет фото)
    while len(photos) < total:
        photos.append(photos[-1] if photos else Path(""))

    # Собираем списки по порядку idx 1..total
    prompts_list = [prompt_map.get(i, "") for i in range(1, total + 1)]

    # ── videos_upscaled — папка назначения ────────────────────────────────────
    videos_upscaled = ch_media / session / "videos_upscaled"
    videos_upscaled.mkdir(parents=True, exist_ok=True)

    # ── Переименовать плохие видео ─────────────────────────────────────────────
    renamed = 0
    for idx in invalid_ids:
        bad = videos_upscaled / f"video_{idx:03d}.mp4"
        if bad.exists():
            backup = videos_upscaled / f"video_{idx:03d}_bad.mp4"
            if backup.exists():
                backup.unlink()
            bad.rename(backup)
            print(f"  → renamed video_{idx:03d}.mp4 → video_{idx:03d}_bad.mp4")
            renamed += 1
    print(f"  Переименовано: {renamed} файлов")

    # ── Очистить IDs из прогресс-файла Grok ───────────────────────────────────
    progress_file = BASE_DIR / "temp" / "grok_progress.json"
    if progress_file.exists():
        try:
            prog = json.loads(progress_file.read_text(encoding="utf-8"))
            if prog.get("session") == session:
                completed = set(prog.get("completed", []))
                completed -= invalid_ids
                prog["completed"] = sorted(completed)
                progress_file.write_text(
                    json.dumps(prog, indent=2), encoding="utf-8"
                )
                print(f"  Прогресс обновлён: убрано {len(invalid_ids)} ID")
        except Exception as e:
            print(f"  ⚠️  Прогресс: {e}")

    # ── Импорт Grok + платформа ───────────────────────────────────────────────
    from utils import PLATFORMS
    from grok_agent import generate_grok_video

    platform = PLATFORMS["2"].copy()

    print(f"\n🚀 Запуск Grok для {len(invalid_ids)} видео → {videos_upscaled}\n")

    saved = generate_grok_video(
        platform   = platform,
        photos     = photos,
        prompts    = prompts_list,
        out_dir    = videos_upscaled,
        session    = session,
    )

    print(f"\n✅ Grok завершил: {saved} видео сохранено в {videos_upscaled}")


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--session",  required=True, help="Имя сессии")
    parser.add_argument("--channel",  default="channel_001_cosmos_de")
    args = parser.parse_args()

    run(args.session, args.channel)
