"""
library_generator.py — автонаполнение библиотеки клипов через Grok.

Пайплайн:
  1. Читает последние N сценариев → извлекает темы
  2. Генерирует M фото-промптов через Claude CLI
  3. Генерирует фото через PixelAgent (v1/v2)
  4. Генерирует видео-промпты из фото-промптов
  5. Генерирует 10с видео через Grok (фото → видео)
  6. Сохраняет в library/incoming/grok/

Запуск:
  python agents/library/library_generator.py --count 10
  python agents/library/library_generator.py --count 200 --topics "Saturn,Enceladus,space"
"""

import argparse
import json
import os
import re
import subprocess
import sys
import time
from pathlib import Path

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

BASE_DIR      = Path(__file__).resolve().parent.parent.parent
CHANNELS_DIR  = BASE_DIR / "data" / "channels"
INCOMING_DIR  = BASE_DIR / "library" / "incoming" / "grok"
PHOTOS_DIR    = INCOMING_DIR / "photos"
MASTER_PHOTO  = BASE_DIR / "config" / "master_prompts" / "photo" / "photo_master_prompt.txt"
MASTER_VIDEO  = BASE_DIR / "config" / "master_prompts" / "video" / "master_video_grok.txt"

sys.path.insert(0, str(BASE_DIR / "agents" / "media_generator"))
sys.path.insert(0, str(BASE_DIR / "agents" / "utils"))


# ─── Темы из последних сценариев ─────────────────────────────────────────────

def extract_themes_from_recent_scripts(n: int = 5) -> str:
    """Берёт тексты последних N сценариев из всех каналов → объединяет для контекста."""
    texts = []
    for channel_dir in CHANNELS_DIR.iterdir():
        if not channel_dir.is_dir():
            continue
        sessions = sorted(
            [d for d in channel_dir.iterdir() if d.is_dir() and d.name.startswith("Video_")],
            reverse=True,
        )
        for session in sessions[:n]:
            result_json = session / "transcripts" / "result.json"
            if result_json.exists():
                try:
                    data = json.loads(result_json.read_text(encoding="utf-8"))
                    segs = data.get("segments", [])
                    text = " ".join(s.get("text", "") for s in segs[:30])
                    texts.append(text[:500])
                except Exception:
                    pass

    return " | ".join(texts[:n])


# ─── Claude CLI ───────────────────────────────────────────────────────────────

def claude(prompt: str, model: str = "claude-haiku-4-5") -> str:
    """Вызов Claude CLI."""
    env = {k: v for k, v in os.environ.items() if k != "CLAUDECODE"}
    result = subprocess.run(
        ["claude.cmd", "--model", model, "-p", "-"],
        input=prompt,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=120,
        env=env,
    )
    if result.returncode != 0:
        raise RuntimeError(f"Claude CLI error: {result.stderr.strip()[:200]}")
    return result.stdout.strip()


# ─── Генерация промптов ───────────────────────────────────────────────────────

_PHOTO_BATCH = 20  # Кол-во промптов за один вызов Claude


def _generate_photo_prompts_batch(batch_size: int, context: str, topics: str, offset: int) -> list[str]:
    """Генерирует batch_size фото-промптов за один вызов Claude."""
    master = MASTER_PHOTO.read_text(encoding="utf-8")

    topic_hint = f"\nFocus topics: {topics}" if topics else ""
    context_hint = f"\nRecent script context: {context[:400]}" if context else ""

    segments_text = "\n".join(
        f"SEGMENT {offset + i + 1}\n[Generate a unique space/science visual for the library]"
        for i in range(batch_size)
    )

    prompt = (
        f"{master}\n\n"
        f"IMPORTANT: You are generating STOCK FOOTAGE PROMPTS for a space science video library, NOT for a specific script.\n"
        f"Each prompt must be a unique, standalone space/science visual.{topic_hint}{context_hint}\n\n"
        f"Generate exactly {batch_size} diverse prompts covering different aspects: "
        f"planets, moons, spacecraft, nebulae, galaxies, scientists, telescopes, space stations, etc.\n\n"
        f"{segments_text}"
    )

    raw = claude(prompt, model="claude-haiku-4-5")

    prompts = []
    current = []
    for line in raw.splitlines():
        line = line.strip()
        if re.match(r"^SEGMENT\s+\d+", line):
            if current:
                prompts.append(" ".join(current).strip())
                current = []
        elif line.startswith("Negative prompt:"):
            if current:
                prompts.append(" ".join(current).strip())
                current = []
        elif line:
            current.append(line)
    if current:
        prompts.append(" ".join(current).strip())

    return [p for p in prompts if len(p) > 20][:batch_size]


def generate_photo_prompts(count: int, context: str, topics: str = "") -> list[str]:
    """Генерирует count фото-промптов через Claude (батчами по _PHOTO_BATCH)."""
    all_prompts: list[str] = []
    n_batches = (count + _PHOTO_BATCH - 1) // _PHOTO_BATCH

    for b in range(n_batches):
        remaining = count - len(all_prompts)
        batch_size = min(_PHOTO_BATCH, remaining)
        print(f"   📝 Батч {b+1}/{n_batches} ({batch_size} промптов)...", flush=True)
        try:
            batch = _generate_photo_prompts_batch(batch_size, context, topics, offset=len(all_prompts))
            all_prompts.extend(batch)
        except Exception as e:
            print(f"   ⚠️  Батч {b+1} ошибка: {e}", flush=True)
        time.sleep(0.2)

    print(f"📝 Сгенерировано фото-промптов: {len(all_prompts)}", flush=True)
    return all_prompts


_VIDEO_BATCH = 20


def _generate_video_prompts_batch(photo_batch: list[str], master: str) -> list[str]:
    """Генерирует видео-промпты для батча фото-промптов."""
    segments_text = "\n\n".join(
        f"SEGMENT {i+1} (Photo Prompt)\n{p}"
        for i, p in enumerate(photo_batch)
    )
    prompt = f"{master}\n\nGenerate video prompts for these {len(photo_batch)} photo prompts:\n\n{segments_text}"
    raw = claude(prompt, model="claude-haiku-4-5")

    video_prompts = []
    for match in re.finditer(r"Final Video Prompt\s*\n(.*?)(?=SEGMENT\s+\d+|Final Video Prompt|\Z)", raw, re.DOTALL):
        vp = match.group(1).strip()
        if len(vp) > 20:
            video_prompts.append(vp)

    if not video_prompts:
        for line in raw.splitlines():
            line = line.strip()
            if len(line) > 40 and not line.startswith("SEGMENT") and not line.startswith("Photo Prompt"):
                video_prompts.append(line)

    return video_prompts[:len(photo_batch)]


def generate_video_prompts(photo_prompts: list[str]) -> list[str]:
    """Генерирует видео-промпты из фото-промптов через Claude (батчами по _VIDEO_BATCH)."""
    master = MASTER_VIDEO.read_text(encoding="utf-8")
    all_video: list[str] = []
    n_batches = (len(photo_prompts) + _VIDEO_BATCH - 1) // _VIDEO_BATCH

    for b in range(n_batches):
        batch = photo_prompts[b * _VIDEO_BATCH : (b + 1) * _VIDEO_BATCH]
        print(f"   🎬 Видео-батч {b+1}/{n_batches} ({len(batch)} промптов)...", flush=True)
        try:
            vps = _generate_video_prompts_batch(batch, master)
            # Если промптов меньше — дублируем фото-промпт как fallback
            while len(vps) < len(batch):
                vps.append(batch[len(vps)])
            all_video.extend(vps)
        except Exception as e:
            print(f"   ⚠️  Видео-батч {b+1} ошибка: {e}", flush=True)
            all_video.extend(batch)  # fallback — используем фото-промпт
        time.sleep(0.2)

    print(f"🎬 Сгенерировано видео-промптов: {len(all_video)}", flush=True)
    return all_video


# ─── Генерация фото ───────────────────────────────────────────────────────────

def generate_photos(photo_prompts: list[str], photos_dir: Path, start_id: int = 1) -> list[Path]:
    """Генерирует фото через PixelAgent. start_id — начальный номер для именования файлов."""
    photos_dir.mkdir(parents=True, exist_ok=True)

    from pixel_agent import generate_photo

    photos = []
    for i, prompt in enumerate(photo_prompts):
        idx = start_id + i
        out = photos_dir / f"photo_{idx:03d}.png"
        if out.exists() and out.stat().st_size > 5000:
            print(f"  ⏭️  [{i+1}/{len(photo_prompts)}] Фото уже есть: {out.name}", flush=True)
            photos.append(out)
            continue

        print(f"  🖼️  [{i+1}/{len(photo_prompts)}] Генерирую фото...", flush=True)
        ok = generate_photo(prompt=prompt, output_path=str(out), seg_id=idx)
        if ok and out.exists():
            photos.append(out)
            print(f"      ✅ {out.name}", flush=True)
        else:
            print(f"      ❌ Ошибка фото {idx}", flush=True)
        time.sleep(0.5)

    return photos


# ─── Генерация видео через Grok ───────────────────────────────────────────────

def generate_grok_videos(photos: list[Path], video_prompts: list[str], out_dir: Path,
                         num_tabs: int = 1, start_id: int = 1) -> int:
    """Генерирует 10с видео через Grok, пропускает уже готовые."""
    from grok_agent import generate_grok_video
    from utils import PLATFORMS

    # Фильтруем уже готовые видео
    filtered_photos, filtered_prompts = [], []
    for i, (photo, prompt) in enumerate(zip(photos, video_prompts)):
        vid_out = out_dir / f"video_{start_id + i:03d}.mp4"
        if vid_out.exists() and vid_out.stat().st_size > 50_000:
            print(f"  ⏭️  video_{start_id + i:03d}.mp4 уже есть — пропускаем", flush=True)
        else:
            filtered_photos.append(photo)
            filtered_prompts.append(prompt)

    if not filtered_photos:
        print("  ✅ Все видео уже сгенерированы", flush=True)
        return 0

    print(f"  🎥 Генерирую {len(filtered_photos)} новых видео...", flush=True)

    # Платформа Grok
    platform = PLATFORMS["2"].copy()
    platform["key"] = "2"

    session = f"library_gen_{int(time.time())}"
    out_dir.mkdir(parents=True, exist_ok=True)

    saved = generate_grok_video(
        platform=platform,
        photos=filtered_photos,
        prompts=filtered_prompts,
        out_dir=out_dir,
        session=session,
        num_tabs=num_tabs,
    )

    return saved


# ─── Пост-обработка: 25fps + trim 10s ────────────────────────────────────────

def postprocess_videos(out_dir: Path) -> None:
    """Перекодирует все video_*.mp4 в 25fps и обрезает строго до 10 секунд."""
    videos = sorted(out_dir.glob("video_*.mp4"))
    if not videos:
        print("   Нет видео для пост-обработки", flush=True)
        return

    print(f"\n⚙️  Пост-обработка {len(videos)} видео (25fps, 10s)...", flush=True)
    done = 0
    for vp in videos:
        tmp = vp.with_suffix(".tmp.mp4")
        ret = subprocess.run(
            [
                "ffmpeg", "-y",
                "-i", str(vp),
                "-t", "10",
                "-r", "25",
                "-c:v", "libx264", "-preset", "fast", "-crf", "18",
                "-c:a", "copy",
                str(tmp),
            ],
            capture_output=True,
            timeout=120,
        )
        if ret.returncode == 0 and tmp.exists():
            tmp.replace(vp)
            done += 1
            print(f"   ✅ {vp.name}", flush=True)
        else:
            tmp.unlink(missing_ok=True)
            print(f"   ❌ {vp.name}: {ret.stderr[-200:].decode(errors='replace')}", flush=True)

    print(f"   Готово: {done}/{len(videos)}", flush=True)


# ─── Главная функция ──────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(description="Library Generator — автонаполнение клипами через Grok")
    parser.add_argument("--count",        type=int, default=10,  help="Кол-во видео для генерации (default: 10)")
    parser.add_argument("--topics",       type=str, default="",  help="Темы через запятую (напр. 'Saturn,black hole,nebula')")
    parser.add_argument("--prompts-only", action="store_true",   help="Только сгенерировать промпты, не запускать генерацию")
    parser.add_argument("--tabs",         type=int, default=1,   help="Кол-во вкладок Grok (default: 1)")
    parser.add_argument("--resume",       action="store_true",   help="Продолжить с последнего места (загрузить prompts.json, пропустить готовые фото/видео)")
    args = parser.parse_args()

    INCOMING_DIR.mkdir(parents=True, exist_ok=True)
    PHOTOS_DIR.mkdir(parents=True, exist_ok=True)

    prompts_file = INCOMING_DIR / "prompts.json"

    # ── RESUME: загрузить готовые промпты ──────────────────────────────────────
    if args.resume and prompts_file.exists():
        print(f"\n{'='*55}", flush=True)
        print(f"LIBRARY GENERATOR — RESUME", flush=True)
        print(f"{'='*55}\n", flush=True)

        data = json.loads(prompts_file.read_text(encoding="utf-8"))
        photo_prompts = [d["photo"] for d in data]
        video_prompts = [d["video"] for d in data]
        start_id = data[0]["id"]
        print(f"📂 Загружено из prompts.json: {len(photo_prompts)} промптов (ID с {start_id})", flush=True)
    else:
        # ── ОБЫЧНЫЙ ЗАПУСК ──────────────────────────────────────────────────────
        print(f"\n{'='*55}", flush=True)
        print(f"LIBRARY GENERATOR — {args.count} клипов", flush=True)
        print(f"{'='*55}\n", flush=True)

        # 1. Контекст из последних сценариев
        print("📖 Читаю последние сценарии...", flush=True)
        context = extract_themes_from_recent_scripts(n=5)
        print(f"   Контекст: {context[:120]}...", flush=True)

        # 2. Фото-промпты
        print(f"\n📝 Генерирую {args.count} фото-промптов...", flush=True)
        photo_prompts = generate_photo_prompts(args.count, context, topics=args.topics)

        if not photo_prompts:
            print("❌ Не удалось сгенерировать промпты", flush=True)
            return

        # 3. Видео-промпты
        print(f"\n🎬 Генерирую видео-промпты...", flush=True)
        video_prompts = generate_video_prompts(photo_prompts)

        # Определяем start_id — следующий после максимального существующего номера
        existing_videos = sorted(INCOMING_DIR.glob("video_*.mp4"))
        existing_photos = sorted(PHOTOS_DIR.glob("photo_*.png"))
        max_existing = max(
            [int(p.stem.split("_")[1]) for p in existing_videos + existing_photos] or [0]
        )
        start_id = max_existing + 1

        # Сохранить промпты
        with open(prompts_file, "w", encoding="utf-8") as f:
            json.dump(
                [{"id": start_id + i, "photo": pp, "video": vp}
                 for i, (pp, vp) in enumerate(zip(photo_prompts, video_prompts))],
                f, ensure_ascii=False, indent=2,
            )
        print(f"💾 Промпты сохранены: {prompts_file} (start_id={start_id})", flush=True)

    if args.prompts_only:
        print("\n✅ --prompts-only: генерация остановлена после промптов.", flush=True)
        return

    # Если resume — start_id берём из первой записи prompts.json
    if args.resume:
        data = json.loads(prompts_file.read_text(encoding="utf-8"))
        start_id = data[0]["id"]

    # 4. Фото
    print(f"\n🖼️  Генерирую {len(photo_prompts)} фото (start_id={start_id})...", flush=True)
    photos = generate_photos(photo_prompts, PHOTOS_DIR, start_id=start_id)
    print(f"   Готово фото: {len(photos)}/{len(photo_prompts)}", flush=True)

    if not photos:
        print("❌ Нет фото — прерываем", flush=True)
        return

    # Подравниваем видео-промпты по кол-ву фото
    vp_aligned = video_prompts[:len(photos)]

    # 5. Grok видео
    print(f"\n🎥 Генерирую Grok видео (10с, {args.tabs} вкладок)...", flush=True)
    saved = generate_grok_videos(photos, vp_aligned, INCOMING_DIR, num_tabs=args.tabs, start_id=start_id)

    # 6. Пост-обработка: 25fps + trim 10s
    postprocess_videos(INCOMING_DIR)

    print(f"\n{'='*55}", flush=True)
    print(f"✅ ГОТОВО: {saved} видео → {INCOMING_DIR}", flush=True)
    print(f"{'='*55}\n", flush=True)


if __name__ == "__main__":
    main()
