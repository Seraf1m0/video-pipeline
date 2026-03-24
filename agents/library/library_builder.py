"""
Автопополнение библиотеки клипов.
Флоу:
1. Анализ всех сценариев → топ темы
2. Генерация промптов через Claude CLI
3. PixelAgent фото (7 потоков: 4×v1 + 3×v2)
4. Grok видео (из фото, 4 вкладки Chrome)
5. library_indexer.py → библиотека

Запуск:
  py agents/library/library_builder.py
  py agents/library/library_builder.py --top 30
  py agents/library/library_builder.py --prompts-only
  py agents/library/library_builder.py --skip-videos
"""

import argparse
import json
import os
import subprocess
import sys
import time
import threading
from pathlib import Path
from concurrent.futures import ThreadPoolExecutor

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

BASE_DIR      = Path(__file__).resolve().parent.parent.parent
CHANNELS_DIR  = BASE_DIR / "data" / "channels"
LIBRARY_DIR   = BASE_DIR / "library"

GENERATED_DIR = LIBRARY_DIR / "generated"
PHOTOS_DIR    = GENERATED_DIR / "photos"
VIDEOS_DIR    = GENERATED_DIR / "videos"
PROMPTS_FILE  = GENERATED_DIR / "prompts.json"

GENERATED_DIR.mkdir(parents=True, exist_ok=True)
PHOTOS_DIR.mkdir(parents=True, exist_ok=True)
VIDEOS_DIR.mkdir(parents=True, exist_ok=True)


# ─── Claude CLI helper ────────────────────────

def _find_claude_exe() -> str | None:
    claude_dir = Path(os.environ.get("APPDATA", "")) / "Claude/claude-code"
    if not claude_dir.exists():
        return None
    for v in sorted(claude_dir.iterdir(), reverse=True):
        exe = v / "claude.exe"
        if exe.exists():
            return str(exe)
    return None


def _call_claude(prompt: str, timeout: int = 120) -> str:
    exe = _find_claude_exe()
    if not exe:
        raise RuntimeError("claude.exe не найден")
    env = os.environ.copy()
    env.pop("CLAUDECODE", None)

    # Передаём промпт через stdin чтобы обойти
    # WinError 206 (лимит длины командной строки Windows)
    r = subprocess.run(
        [exe, "-p"],
        input=prompt,
        capture_output=True, text=True,
        encoding="utf-8", errors="replace",
        env=env, timeout=timeout,
    )
    return r.stdout.strip()


# ─── ШАГ 1: Анализ сценариев ──────────────────

def collect_all_segments() -> list[dict]:
    """Собрать все сегменты из всех сессий DE и FR каналов."""
    all_segments = []
    result_jsons = list(CHANNELS_DIR.rglob("result.json"))

    for result_json in result_jsons:
        try:
            with open(result_json, encoding="utf-8") as f:
                data = json.load(f)
            segments = data["segments"] if isinstance(data, dict) else data
            lang     = result_json.parts[-4] if len(result_json.parts) >= 4 else "?"
            session  = result_json.parts[-3] if len(result_json.parts) >= 3 else "?"
            for seg in segments:
                all_segments.append({
                    "text":    seg.get("text", ""),
                    "session": session,
                    "lang":    lang,
                })
        except Exception as e:
            print(f"  ⚠️ {result_json.parent.parent.name}: {e}", flush=True)

    print(
        f"📚 Собрано сегментов: {len(all_segments)} "
        f"из {len(result_jsons)} сессий",
        flush=True,
    )
    return all_segments


def analyze_segments_for_themes(segments: list[dict], top_n: int = 50) -> list[dict]:
    """
    Claude анализирует все сегменты → Топ N универсальных космических тем.
    Возвращает список {theme_id, frequency, universality, description}.
    """
    print(f"\n🤖 Анализ {len(segments)} сегментов...", flush=True)

    all_texts = "\n".join(
        f"- {seg['text'][:100]}"
        for seg in segments[:500]
    )

    prompt = (
        f"Analyze these space documentary segments and find the top {top_n} "
        f"most UNIVERSAL visual themes.\n\n"
        f"SEGMENTS:\n{all_texts}\n\n"
        f"Rules for UNIVERSAL themes:\n"
        f"- Must work as B-roll for ANY space video\n"
        f"- Visually interesting footage\n"
        f"- Not too specific (avoid exact dates/missions)\n"
        f"- Good examples: galaxies, black holes, nebulae, planets, "
        f"telescopes, spacecraft, observatories, scientists working, star formations\n\n"
        f"For each theme provide:\n"
        f"- theme_id: SHORT_ENGLISH_NAME\n"
        f"- frequency: how often in segments\n"
        f"- universality: 1-10 (10=works in any space video)\n"
        f"- description: what to visualize\n\n"
        f"Respond JSON only:\n"
        f'{{"themes": [{{"theme_id": "BLACK_HOLE", "frequency": 47, '
        f'"universality": 9, "description": "Supermassive black hole with accretion disk"}}]}}'
    )

    raw = _call_claude(prompt, timeout=180)
    raw = raw.replace("```json", "").replace("```", "").strip()

    try:
        data   = json.loads(raw)
        themes = data.get("themes", [])
        themes.sort(
            key=lambda x: x.get("universality", 0) * x.get("frequency", 0),
            reverse=True,
        )
        print(f"✅ Найдено тем: {len(themes)}", flush=True)
        print("\nТоп 10 тем:")
        for t in themes[:10]:
            print(
                f"  {t['theme_id']:25} "
                f"freq={t.get('frequency', 0):3} "
                f"universal={t.get('universality', 0)}/10",
                flush=True,
            )
        return themes[:top_n]
    except Exception as e:
        print(f"❌ Ошибка анализа: {e}\n{raw[:300]}", flush=True)
        return []


# ─── ШАГ 2: Генерация промптов ────────────────

def generate_prompts_for_themes(
        themes: list[dict],
        prompts_per_theme: int = 4) -> list[dict]:
    """
    Claude генерирует промпты для фото/видео по каждой теме.
    Фильтрует по quality >= 7.
    """
    print(
        f"\n📝 Генерация промптов "
        f"({len(themes)} тем × {prompts_per_theme} промптов)...",
        flush=True,
    )

    all_prompts: list[dict] = []

    for i, theme in enumerate(themes):
        print(f"  [{i+1}/{len(themes)}] {theme['theme_id']}...", flush=True)

        prompt = (
            f"Generate {prompts_per_theme} different visual prompts for this space theme:\n\n"
            f"Theme: {theme['theme_id']}\n"
            f"Description: {theme.get('description', '')}\n\n"
            f"Rules:\n"
            f"- Each prompt = different angle/perspective\n"
            f"- Cinematic, photorealistic\n"
            f"- 16:9 landscape format\n"
            f"- No people unless specifically needed\n"
            f"- Scientific accuracy\n"
            f"- Rate each prompt 1-10 (only include if >= 7)\n\n"
            f"Respond JSON only:\n"
            f'{{"prompts": [{{"prompt": "...", "quality": 8, "angle": "wide"}}]}}'
        )

        raw = _call_claude(prompt, timeout=60)
        raw = raw.replace("```json", "").replace("```", "").strip()

        try:
            data        = json.loads(raw)
            prompts     = data.get("prompts", [])
            good        = [p for p in prompts if p.get("quality", 0) >= 7]
            for p in good:
                all_prompts.append({
                    "theme_id":        theme["theme_id"],
                    "prompt":          p["prompt"],
                    "quality":         p["quality"],
                    "angle":           p.get("angle", ""),
                    "photo_generated": False,
                    "video_generated": False,
                })
            print(f"    ✅ {len(good)} промптов", flush=True)
        except Exception as e:
            print(f"    ❌ {e}", flush=True)

    with open(PROMPTS_FILE, "w", encoding="utf-8") as f:
        json.dump({"prompts": all_prompts}, f, indent=2, ensure_ascii=False)

    print(f"""
{'='*50}
📝 ПРОМПТЫ ГОТОВЫ
  Тем обработано: {len(themes)}
  Промптов итого: {len(all_prompts)}
  Файл: {PROMPTS_FILE}
{'='*50}
""", flush=True)
    return all_prompts


# ─── ШАГ 3: Генерация фото ────────────────────

def generate_photos(prompts: list[dict]) -> int:
    """
    PixelAgent 7 потоков: чётные → v1 (4), нечётные → v2 (3).
    Пропускает уже готовые файлы.
    """
    print(f"\n🖼️  Генерация {len(prompts)} фото (7 потоков)...", flush=True)

    # Добавить media_generator в path
    _mg_dir = str(BASE_DIR / "agents" / "media_generator")
    if _mg_dir not in sys.path:
        sys.path.insert(0, _mg_dir)

    from pixel_agent import generate_photo  # noqa

    done_lock = threading.Lock()
    done_count = [0]
    total = len(prompts)

    def generate_one(item: tuple[int, dict]) -> bool:
        idx, p = item
        output = PHOTOS_DIR / f"photo_{idx:04d}.png"
        if output.exists() and output.stat().st_size > 500:
            with done_lock:
                done_count[0] += 1
            return True

        success = generate_photo(p["prompt"], str(output), idx)

        with done_lock:
            done_count[0] += 1
            status = "✅" if success else "❌"
            print(
                f"  {status} [{done_count[0]}/{total}] "
                f"{p['theme_id']}: {p['prompt'][:50]}...",
                flush=True,
            )

        p["photo_generated"] = success
        return success

    items = list(enumerate(prompts, start=1))
    with ThreadPoolExecutor(max_workers=7) as ex:
        list(ex.map(generate_one, items))

    # Сохранить статус
    with open(PROMPTS_FILE, "w", encoding="utf-8") as f:
        json.dump({"prompts": prompts}, f, indent=2, ensure_ascii=False)

    generated = sum(1 for p in prompts if p.get("photo_generated"))
    print(f"✅ Фото сгенерировано: {generated}/{total}", flush=True)
    return generated


# ─── ШАГ 4: Генерация видео ───────────────────

def generate_videos(prompts: list[dict]) -> int:
    """
    Grok image-to-video через generate_grok_video.
    Использует фото из PHOTOS_DIR как входные изображения.
    4 параллельных вкладки Chrome.
    """
    print(f"\n🎬 Генерация {len(prompts)} видео (Grok, 4 вкладки)...", flush=True)

    _mg_dir = str(BASE_DIR / "agents" / "media_generator")
    if _mg_dir not in sys.path:
        sys.path.insert(0, _mg_dir)

    from grok_agent import generate_grok_video  # noqa
    from utils import PLATFORMS                  # noqa

    grok_platform = PLATFORMS["2"].copy()

    # Собрать пары (фото, промпт) только для тех у кого есть фото
    photo_paths: list[Path] = []
    prompt_texts: list[str] = []

    for idx, p in enumerate(prompts, start=1):
        photo = PHOTOS_DIR / f"photo_{idx:04d}.png"
        video = VIDEOS_DIR / f"video_{idx:04d}.mp4"
        if photo.exists() and not (video.exists() and video.stat().st_size > 1024):
            photo_paths.append(photo)
            prompt_texts.append(p["prompt"])

    if not photo_paths:
        print("⚠️  Нет фото для генерации видео", flush=True)
        return 0

    print(f"  Пар фото+промпт: {len(photo_paths)}", flush=True)

    generated = generate_grok_video(
        platform=grok_platform,
        photos=photo_paths,
        prompts=prompt_texts,
        out_dir=VIDEOS_DIR,
        session="library_builder",
        num_tabs=4,
    )

    # Пометить сгенерированные
    for idx, p in enumerate(prompts, start=1):
        video = VIDEOS_DIR / f"video_{idx:04d}.mp4"
        if video.exists() and video.stat().st_size > 1024:
            p["video_generated"] = True

    with open(PROMPTS_FILE, "w", encoding="utf-8") as f:
        json.dump({"prompts": prompts}, f, indent=2, ensure_ascii=False)

    print(f"✅ Видео сгенерировано: {generated}/{len(photo_paths)}", flush=True)
    return generated


# ─── ШАГ 5: Добавить в библиотеку ────────────

def add_to_library() -> None:
    """Запустить library_indexer.py для сгенерированных видео."""
    print("\n📥 Добавление в библиотеку...", flush=True)

    result = subprocess.run(
        ["py", "agents/library/library_indexer.py", "--source", str(VIDEOS_DIR)],
        cwd=str(BASE_DIR),
    )
    if result.returncode == 0:
        print("✅ Клипы добавлены в библиотеку!", flush=True)
    else:
        print("❌ Ошибка добавления в библиотеку", flush=True)


# ─── ГЛАВНАЯ ФУНКЦИЯ ──────────────────────────

def run(
        top_n: int = 50,
        prompts_per_theme: int = 4,
        prompts_only: bool = False,
        skip_photos: bool = False,
        skip_videos: bool = False) -> None:

    start_time = time.time()

    print(f"""
{'='*60}
🚀 АВТОПОПОЛНЕНИЕ БИБЛИОТЕКИ
  Топ тем:          {top_n}
  Промптов на тему: {prompts_per_theme}
  Всего промптов:   ~{top_n * prompts_per_theme}
{'='*60}
""", flush=True)

    # ШАГ 1 — Анализ сценариев
    print("─"*40, flush=True)
    print("ШАГ 1/5 — Анализ сценариев", flush=True)
    print("─"*40, flush=True)

    segments = collect_all_segments()
    if not segments:
        print("❌ Сегменты не найдены!")
        return

    themes = analyze_segments_for_themes(segments, top_n=top_n)
    if not themes:
        print("❌ Темы не найдены!")
        return

    t1 = time.time()
    print(f"⏱️  ШАГ 1: {t1-start_time:.0f}s", flush=True)

    # ШАГ 2 — Генерация промптов
    print("\n" + "─"*40, flush=True)
    print("ШАГ 2/5 — Генерация промптов", flush=True)
    print("─"*40, flush=True)

    if PROMPTS_FILE.exists() and not prompts_only:
        with open(PROMPTS_FILE, encoding="utf-8") as f:
            existing = json.load(f)
        prompts = existing.get("prompts", [])
        print(f"📋 Загружено {len(prompts)} существующих промптов", flush=True)
    else:
        prompts = generate_prompts_for_themes(themes, prompts_per_theme=prompts_per_theme)

    if prompts_only:
        print("✅ --prompts-only: останавливаемся")
        return

    t2 = time.time()
    print(f"⏱️  ШАГ 2: {t2-t1:.0f}s", flush=True)

    # ШАГ 3 — Генерация фото
    if not skip_photos:
        print("\n" + "─"*40, flush=True)
        print("ШАГ 3/5 — Генерация фото", flush=True)
        print("─"*40, flush=True)
        generate_photos(prompts)
        t3 = time.time()
        print(f"⏱️  ШАГ 3: {t3-t2:.0f}s", flush=True)
    else:
        t3 = time.time()

    # ШАГ 4 — Генерация видео
    if not skip_videos:
        print("\n" + "─"*40, flush=True)
        print("ШАГ 4/5 — Генерация видео", flush=True)
        print("─"*40, flush=True)
        generate_videos(prompts)
        t4 = time.time()
        print(f"⏱️  ШАГ 4: {t4-t3:.0f}s", flush=True)
    else:
        t4 = time.time()

    # ШАГ 5 — Добавить в библиотеку
    print("\n" + "─"*40, flush=True)
    print("ШАГ 5/5 — Добавление в библиотеку", flush=True)
    print("─"*40, flush=True)
    add_to_library()

    total_time = time.time() - start_time
    print(f"""
{'='*60}
🎉 АВТОПОПОЛНЕНИЕ ЗАВЕРШЕНО!
  Тем найдено:    {len(themes)}
  Промптов:       {len(prompts)}
  ⏱️ Общее время: {total_time/60:.1f} мин
{'='*60}
""", flush=True)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Автопополнение библиотеки клипов")
    parser.add_argument(
        "--top", type=int, default=50,
        help="Топ N тем (default: 50)",
    )
    parser.add_argument(
        "--prompts-per-theme", type=int, default=4,
        help="Промптов на тему (default: 4)",
    )
    parser.add_argument(
        "--prompts-only", action="store_true",
        help="Только анализ + промпты",
    )
    parser.add_argument(
        "--skip-photos", action="store_true",
        help="Пропустить генерацию фото",
    )
    parser.add_argument(
        "--skip-videos", action="store_true",
        help="Пропустить генерацию видео",
    )
    args = parser.parse_args()

    run(
        top_n=args.top,
        prompts_per_theme=args.prompts_per_theme,
        prompts_only=args.prompts_only,
        skip_photos=args.skip_photos,
        skip_videos=args.skip_videos,
    )
