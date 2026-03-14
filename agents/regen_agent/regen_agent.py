"""
Regen Agent
-----------
Перегенерирует плохие фото из blip_report.json:
  1. Читает bad_photos из data/media/{session}/blip_report.json
  2. Регенерирует фото-промпты для плохих ID через Claude CLI
  3. Регенерирует видео-промпты из новых фото-промптов (если задан video-master)
  4. Обновляет photo_prompts.json / video_prompts.json (только плохие ID)
  5. Удаляет старые фото плохих ID
  6. Запускает PixelAgent для генерации новых фото

Запуск:
  py agents/regen_agent/regen_agent.py
  py agents/regen_agent/regen_agent.py --project Video_xxx --photo-master photo_master_prompt.txt
  py agents/regen_agent/regen_agent.py --project Video_xxx --photo-master photo_master_prompt.txt --video-master master_video_grok.txt
"""

import argparse
import asyncio
import io
import json
import os
import re
import subprocess
import sys
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")

# ── Пути ──────────────────────────────────────────────────────────────────────

BASE_DIR        = Path(__file__).parent.parent.parent
TRANSCRIPTS_DIR = BASE_DIR / "data" / "transcripts"
PROMPTS_DIR     = BASE_DIR / "data" / "prompts"
MEDIA_DIR       = BASE_DIR / "data" / "media"
PHOTO_MASTERS   = BASE_DIR / "config" / "master_prompts" / "photo"
VIDEO_MASTERS   = BASE_DIR / "config" / "master_prompts" / "video"
TEMP_DIR        = BASE_DIR / "temp"
REGEN_PROGRESS_FILE = TEMP_DIR / "regen_progress.json"

# Добавляем media_generator в путь (для pixel_agent)
_MEDIA_GEN_DIR = BASE_DIR / "agents" / "media_generator"
if str(_MEDIA_GEN_DIR) not in sys.path:
    sys.path.insert(0, str(_MEDIA_GEN_DIR))

# Claude CLI в PATH
_CLAUDE_DIR = Path(os.environ.get("APPDATA", "")) / "Claude/claude-code"
if _CLAUDE_DIR.exists():
    for v in sorted(_CLAUDE_DIR.iterdir(), reverse=True):
        exe = v / "claude.exe"
        if exe.exists():
            os.environ["PATH"] = str(v) + os.pathsep + os.environ.get("PATH", "")
            break

_SESSION_RE = re.compile(r"^Video_\d{8}_\d{6}$")
BATCH_SIZE   = 10
MAX_PARALLEL = 5


# ── Прогресс ──────────────────────────────────────────────────────────────────

def _write_progress(data: dict) -> None:
    TEMP_DIR.mkdir(parents=True, exist_ok=True)
    try:
        REGEN_PROGRESS_FILE.write_text(
            json.dumps(data, ensure_ascii=False), encoding="utf-8"
        )
    except Exception:
        pass


# ── Сессия ────────────────────────────────────────────────────────────────────

def find_latest_session() -> str | None:
    if not MEDIA_DIR.exists():
        return None
    folders = [d for d in MEDIA_DIR.iterdir()
               if d.is_dir() and _SESSION_RE.match(d.name)]
    if not folders:
        return None
    return max(folders, key=lambda f: f.name).name


# ── Загрузка данных ───────────────────────────────────────────────────────────

def load_blip_bad_ids(session: str) -> list[int]:
    p = MEDIA_DIR / session / "blip_report.json"
    if not p.exists():
        raise FileNotFoundError(f"blip_report.json не найден: {p}\nСначала запусти BLIP анализ.")
    with open(p, encoding="utf-8") as f:
        report = json.load(f)
    return sorted(report.get("summary", {}).get("bad_photos", []))


def load_all_segments(session: str) -> list[dict]:
    p = TRANSCRIPTS_DIR / session / "result.json"
    if not p.exists():
        raise FileNotFoundError(f"result.json не найден: {p}")
    with open(p, encoding="utf-8") as f:
        data = json.load(f)
    return data["segments"] if isinstance(data, dict) else data


def load_photo_prompts_json(session: str) -> list[dict]:
    p = PROMPTS_DIR / session / "photo" / "photo_prompts.json"
    if not p.exists():  # fallback старая структура
        p = PROMPTS_DIR / session / "photo_prompts.json"
    if not p.exists():
        raise FileNotFoundError(f"photo_prompts.json не найден: {p}")
    with open(p, encoding="utf-8") as f:
        return json.load(f)


def load_video_prompts_json(session: str) -> list[dict]:
    p = PROMPTS_DIR / session / "video" / "video_prompts.json"
    if not p.exists():  # fallback старая структура
        p = PROMPTS_DIR / session / "video_prompts.json"
    if not p.exists():
        return []
    with open(p, encoding="utf-8") as f:
        return json.load(f)


def load_master(folder: Path, filename: str) -> str:
    path = folder / filename
    if not path.exists():
        raise FileNotFoundError(f"Мастер-промпт не найден: {path}")
    return path.read_text(encoding="utf-8").strip()


def list_masters(folder: Path) -> list[str]:
    if not folder.exists():
        return []
    return sorted(f.name for f in folder.iterdir() if f.is_file() and f.suffix == ".txt")


def ask_master(label: str, folder: Path) -> str:
    files = list_masters(folder)
    if not files:
        raise FileNotFoundError(f"Нет .txt файлов в {folder}")
    if len(files) == 1:
        print(f"Мастер-промпт для {label}: {files[0]}")
        return files[0]
    print(f"\nВыбери мастер-промпт для {label}:")
    for i, f in enumerate(files, 1):
        print(f"  {i}. {f}")
    while True:
        choice = input(f"Введи номер (1-{len(files)}): ").strip()
        if choice.isdigit() and 1 <= int(choice) <= len(files):
            return files[int(choice) - 1]
        print("  Неверный ввод.")


# ── Claude CLI ────────────────────────────────────────────────────────────────

def call_claude(prompt: str) -> str:
    env = os.environ.copy()
    env.pop("CLAUDECODE", None)
    r = subprocess.run(
        ["claude", "-p", prompt],
        capture_output=True, text=True,
        encoding="utf-8", errors="replace", env=env,
    )
    if r.returncode != 0:
        raise RuntimeError(
            f"claude CLI вернул код {r.returncode}:\n"
            f"STDERR: {r.stderr[:300]}\nSTDOUT: {r.stdout[:300]}"
        )
    return r.stdout.strip()


# ── Построение промптов ───────────────────────────────────────────────────────

def build_photo_prompt(master: str, segments: list[dict]) -> str:
    lines = []
    for s in segments:
        lines.append(f"SEGMENT {s['id']}")
        lines.append(f'[{s["start"]}s-{s["end"]}s] Script text: "{s["text"]}"')
    return (
        f"{master}\n\n"
        f"Теперь создай промпты для этих сегментов:\n"
        + "\n".join(lines)
    )


def parse_photo_output(text: str, n_segments: int) -> list[tuple[str, str]]:
    results: list[tuple[str, str]] = []
    parts = re.split(r"\bSEGMENT\s+\d+\b(?!\s*\(Photo Prompt\))", text, flags=re.IGNORECASE)
    segment_parts = [p.strip() for p in parts[1:] if p.strip()]
    for part in segment_parts:
        neg_match = re.search(r"Negative\s+prompt\s*:\s*", part, re.IGNORECASE)
        if neg_match:
            positive = part[: neg_match.start()].strip()
            negative = part[neg_match.end():].strip()
        else:
            positive = part.strip()
            negative = ""
        results.append((positive, negative))
    if len(results) != n_segments:
        raise ValueError(
            f"Ожидалось {n_segments} сегментов, получено {len(results)}\n"
            f"Ответ (первые 500 символов):\n{text[:500]}"
        )
    return results


def build_video_prompt(
    master: str,
    segments: list[dict],
    photo_results: list[tuple[str, str]],
) -> str:
    lines = []
    for s, (pos, _neg) in zip(segments, photo_results):
        lines.append(f"SEGMENT {s['id']} (Photo Prompt)")
        lines.append(pos)
        lines.append("")
    return (
        f"{master}\n\n"
        f"Теперь создай промпты для этих сегментов:\n"
        + "\n".join(lines)
    )


def parse_video_output(text: str, n_segments: int) -> list[str]:
    results: list[str] = []
    parts = re.split(r"\bSEGMENT\s+\d+\s*\(Photo Prompt\)", text, flags=re.IGNORECASE)
    segment_parts = [p.strip() for p in parts[1:] if p.strip()]
    for part in segment_parts:
        fvp_match = re.search(r"Final\s+Video\s+Prompt[^\S\n]*[:\-]?\s*\n", part, re.IGNORECASE)
        if not fvp_match:
            fvp_match = re.search(r"\bVideo\s+Prompt[^\S\n]*[:\-]?\s*\n", part, re.IGNORECASE)
        if fvp_match:
            video_prompt = part[fvp_match.end():].strip()
        else:
            paragraphs = [p.strip() for p in part.split("\n\n") if p.strip()]
            video_prompt = paragraphs[-1] if paragraphs else part.strip()
        results.append(video_prompt)
    if len(results) != n_segments:
        raise ValueError(
            f"Ожидалось {n_segments} видео-промптов, получено {len(results)}\n"
            f"Ответ (первые 500 символов):\n{text[:500]}"
        )
    return results


# ── Регенерация промптов ──────────────────────────────────────────────────────

def regen_photo_prompts(
    master: str,
    segments: list[dict],
    progress_data: dict,
) -> list[tuple[str, str]]:
    """Регенерирует фото-промпты для переданных сегментов батчами."""
    batches = [segments[i:i + BATCH_SIZE] for i in range(0, len(segments), BATCH_SIZE)]
    n_batches = len(batches)
    total = len(segments)

    def _run_batch(idx: int, batch: list[dict]) -> tuple[int, list[tuple[str, str]]]:
        s_id = batch[0]["id"]
        e_id = batch[-1]["id"]
        print(f"    [фото] Батч {idx+1}/{n_batches}: #{s_id}–#{e_id} ...", end=" ", flush=True)
        raw = call_claude(build_photo_prompt(master, batch))
        results = parse_photo_output(raw, len(batch))
        print(f"OK ({len(results)})")
        return idx, results

    batch_results: dict[int, list[tuple[str, str]]] = {}
    completed = 0

    with ThreadPoolExecutor(max_workers=MAX_PARALLEL) as executor:
        futures = {
            executor.submit(_run_batch, idx, batch): idx
            for idx, batch in enumerate(batches)
        }
        for future in as_completed(futures):
            idx, results = future.result()
            batch_results[idx] = results
            completed += len(results)
            progress_data["current"] = min(completed, total)
            _write_progress(progress_data)

    all_results: list[tuple[str, str]] = []
    for i in sorted(batch_results.keys()):
        all_results.extend(batch_results[i])
    return all_results


def regen_video_prompts(
    master: str,
    segments: list[dict],
    photo_pairs: list[tuple[str, str]],
    progress_data: dict,
) -> list[str]:
    """Регенерирует видео-промпты для переданных сегментов батчами."""
    batches = [segments[i:i + BATCH_SIZE] for i in range(0, len(segments), BATCH_SIZE)]
    photo_batches = [photo_pairs[i:i + BATCH_SIZE] for i in range(0, len(photo_pairs), BATCH_SIZE)]
    n_batches = len(batches)
    total = len(segments)

    def _run_batch(
        idx: int, batch: list[dict], photo_batch: list[tuple[str, str]]
    ) -> tuple[int, list[str]]:
        s_id = batch[0]["id"]
        e_id = batch[-1]["id"]
        print(f"    [видео] Батч {idx+1}/{n_batches}: #{s_id}–#{e_id} ...", end=" ", flush=True)
        raw = call_claude(build_video_prompt(master, batch, photo_batch))
        prompts = parse_video_output(raw, len(batch))
        print(f"OK ({len(prompts)})")
        return idx, prompts

    batch_results: dict[int, list[str]] = {}
    completed = 0

    with ThreadPoolExecutor(max_workers=MAX_PARALLEL) as executor:
        futures = {
            executor.submit(_run_batch, idx, batch, photo_batch): idx
            for idx, (batch, photo_batch) in enumerate(zip(batches, photo_batches))
        }
        for future in as_completed(futures):
            idx, prompts = future.result()
            batch_results[idx] = prompts
            completed += len(prompts)
            progress_data["current"] = min(completed, total)
            _write_progress(progress_data)

    all_prompts: list[str] = []
    for i in sorted(batch_results.keys()):
        all_prompts.extend(batch_results[i])
    return all_prompts


# ── Обновление JSON/TXT файлов промптов ───────────────────────────────────────

def patch_photo_prompts(
    session: str,
    bad_segments: list[dict],
    new_results: list[tuple[str, str]],
) -> None:
    """Обновляет photo_prompts.json/.txt только для плохих ID."""
    data = load_photo_prompts_json(session)
    id_to_new: dict[int, tuple[str, str]] = {
        seg["id"]: pair for seg, pair in zip(bad_segments, new_results)
    }
    for item in data:
        if item["id"] in id_to_new:
            pos, neg = id_to_new[item["id"]]
            item["photo_prompt"]    = pos
            item["negative_prompt"] = neg

    # Новая структура — photo/
    out_dir = PROMPTS_DIR / session / "photo"
    out_dir.mkdir(parents=True, exist_ok=True)
    j = out_dir / "photo_prompts.json"
    j.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")

    t = out_dir / "photo_prompts.txt"
    txt_lines = [item["photo_prompt"].strip().rstrip(",") for item in data]
    t.write_text("\n\n".join(txt_lines), encoding="utf-8")

    print(f"[REGEN] Обновлено photo_prompts.json + .txt ({len(id_to_new)} записей)")


def patch_video_prompts(
    session: str,
    bad_segments: list[dict],
    new_prompts: list[str],
) -> None:
    """Обновляет video_prompts.json/.txt только для плохих ID."""
    data = load_video_prompts_json(session)
    if not data:
        print("[REGEN] video_prompts.json не найден — пропускаю.")
        return

    id_to_new: dict[int, str] = {
        seg["id"]: p for seg, p in zip(bad_segments, new_prompts)
    }
    for item in data:
        if item["id"] in id_to_new:
            item["video_prompt"] = id_to_new[item["id"]]

    # Новая структура — video/
    out_dir = PROMPTS_DIR / session / "video"
    out_dir.mkdir(parents=True, exist_ok=True)
    j = out_dir / "video_prompts.json"
    j.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")

    t = out_dir / "video_prompts.txt"
    txt_lines = [item["video_prompt"].strip().replace("\n\n", "\n") for item in data]
    t.write_text("\n\n".join(txt_lines), encoding="utf-8")

    print(f"[REGEN] Обновлено video_prompts.json + .txt ({len(id_to_new)} записей)")


# ── Удаление старых фото + запуск PixelAgent ──────────────────────────────────

def delete_bad_photos(session: str, bad_ids: list[int]) -> None:
    photos_dir = MEDIA_DIR / session / "photos"
    deleted = 0
    for bid in bad_ids:
        p = photos_dir / f"photo_{bid:03d}.png"
        if p.exists():
            p.unlink()
            deleted += 1
        # Удаляем маркер FAILED если есть
        fail_marker = photos_dir / f"photo_{bid:03d}_FAILED.txt"
        fail_marker.unlink(missing_ok=True)
    print(f"[REGEN] Удалено {deleted} старых фото из {len(bad_ids)} плохих")


def run_pixel_regen(session: str, progress_data: dict) -> tuple[int, list[int]]:
    """Запускает PixelAgent — регенерирует только удалённые (плохие) фото."""
    from utils import PIXEL_API_URL, PIXEL_API_KEY
    from pixel_agent import generate_pixel_photos_async

    if not PIXEL_API_URL or not PIXEL_API_KEY:
        raise RuntimeError("PIXEL_API_URL / PIXEL_API_KEY не заданы в config/.env")

    # Читаем все промпты (pixel_agent пропустит существующие фото автоматически)
    prompts_txt = PROMPTS_DIR / session / "photo" / "photo_prompts.txt"
    if not prompts_txt.exists():  # fallback старая структура
        prompts_txt = PROMPTS_DIR / session / "photo_prompts.txt"
    content = prompts_txt.read_text(encoding="utf-8")
    all_prompts = [p.strip() for p in content.split("\n\n") if p.strip()]

    photos_dir = MEDIA_DIR / session / "photos"
    photos_dir.mkdir(parents=True, exist_ok=True)

    total = len(all_prompts)
    progress_data.update({"phase": "photos", "current": 0, "total": total})
    _write_progress(progress_data)

    saved, failed = asyncio.run(
        generate_pixel_photos_async(all_prompts, photos_dir, PIXEL_API_KEY)
    )
    return saved, failed


# ── Точка входа ───────────────────────────────────────────────────────────────

def run() -> None:
    parser = argparse.ArgumentParser(description="Regen Agent — перегенерация плохих фото")
    parser.add_argument("--project",      default=None, help="Сессия (по умолчанию — последняя)")
    parser.add_argument("--photo-master", default=None, help="Мастер-промпт для фото")
    parser.add_argument("--video-master", default=None, help="Мастер-промпт для видео (опционально)")
    args = parser.parse_args()

    # ── Сессия ────────────────────────────────────────────────────────────
    session = args.project or find_latest_session()
    if not session:
        print("[REGEN] Нет сессий в data/media/")
        sys.exit(1)

    # ── Мастер-промпты ────────────────────────────────────────────────────
    photo_master_name = args.photo_master or ask_master("фото", PHOTO_MASTERS)
    video_master_name = args.video_master  # None = не регенерировать видео-промпты

    # ── Плохие ID из blip_report.json ─────────────────────────────────────
    try:
        bad_ids = load_blip_bad_ids(session)
    except FileNotFoundError as e:
        print(f"[REGEN] Ошибка: {e}")
        sys.exit(1)

    if not bad_ids:
        print("[REGEN] Нет плохих фото по данным BLIP — регенерация не нужна!")
        print("REGEN_SUMMARY: {\"regenerated\": 0, \"failed\": []}")
        sys.exit(0)

    print(f"[REGEN] Сессия     : {session}")
    print(f"[REGEN] Фото-мастер: {photo_master_name}")
    if video_master_name:
        print(f"[REGEN] Видео-мастер: {video_master_name}")
    print(f"[REGEN] Плохих фото: {len(bad_ids)} → {bad_ids[:20]}" +
          (f"... ещё {len(bad_ids)-20}" if len(bad_ids) > 20 else ""))
    print()

    # Загружаем все сегменты, фильтруем по плохим ID
    all_segments = load_all_segments(session)
    bad_ids_set  = set(bad_ids)
    bad_segments = [s for s in all_segments if s["id"] in bad_ids_set]

    if len(bad_segments) != len(bad_ids):
        found = {s["id"] for s in bad_segments}
        missing = bad_ids_set - found
        print(f"[REGEN] ⚠️  Не найдено сегментов для ID: {sorted(missing)}")

    # ── Прогресс ──────────────────────────────────────────────────────────
    progress_data: dict = {
        "session":  session,
        "phase":    "prompts",
        "status":   "running",
        "bad_ids":  bad_ids,
        "current":  0,
        "total":    len(bad_segments),
    }
    _write_progress(progress_data)

    # ══ ФАЗА 1: Фото-промпты ══════════════════════════════════════════════
    print("─" * 52)
    print(f"ФАЗА 1: Регенерация фото-промптов [{photo_master_name}]")
    print("─" * 52)

    photo_master = load_master(PHOTO_MASTERS, photo_master_name)
    progress_data.update({"phase": "prompts", "current": 0, "total": len(bad_segments)})
    _write_progress(progress_data)

    t0 = time.time()
    new_photo_pairs = regen_photo_prompts(photo_master, bad_segments, progress_data)
    print(f"  → {len(new_photo_pairs)} фото-промпта за {time.time()-t0:.1f}с")

    patch_photo_prompts(session, bad_segments, new_photo_pairs)

    # ══ ФАЗА 2: Видео-промпты (если задан мастер) ═════════════════════════
    if video_master_name:
        print()
        print("─" * 52)
        print(f"ФАЗА 2: Регенерация видео-промптов [{video_master_name}]")
        print("─" * 52)

        video_master = load_master(VIDEO_MASTERS, video_master_name)
        progress_data.update({"phase": "video_prompts", "current": 0, "total": len(bad_segments)})
        _write_progress(progress_data)

        t0 = time.time()
        new_video_prompts = regen_video_prompts(
            video_master, bad_segments, new_photo_pairs, progress_data
        )
        print(f"  → {len(new_video_prompts)} видео-промптов за {time.time()-t0:.1f}с")

        patch_video_prompts(session, bad_segments, new_video_prompts)
    else:
        print("\n[REGEN] Видео-мастер не задан — видео-промпты не обновляются")

    # ══ ФАЗА 3: Удаление старых фото + PixelAgent ═════════════════════════
    print()
    print("─" * 52)
    print("ФАЗА 3: Удаление старых фото + PixelAgent")
    print("─" * 52)

    delete_bad_photos(session, bad_ids)

    t0 = time.time()
    try:
        saved, failed = run_pixel_regen(session, progress_data)
    except Exception as e:
        print(f"[REGEN] ❌ PixelAgent ошибка: {e}")
        failed = bad_ids
        saved  = 0

    elapsed = int(time.time() - t0)

    # ── Сводка ────────────────────────────────────────────────────────────
    print()
    print("=" * 52)
    print("  РЕГЕНЕРАЦИЯ ЗАВЕРШЕНА")
    print("=" * 52)
    print(f"  🔄 Было плохих: {len(bad_ids)}")
    print(f"  ✅ Сгенерировано: {saved}")
    print(f"  ❌ Провалилось:  {len(failed)}" + (f"  {failed}" if failed else ""))
    print(f"  ⏱  Время: {elapsed}с")
    print("=" * 52)

    summary = {
        "session":      session,
        "bad_ids":      bad_ids,
        "regenerated":  len(bad_ids) - len(failed),
        "failed":       failed,
        "with_video":   bool(video_master_name),
    }
    print(f"REGEN_SUMMARY: {json.dumps(summary)}")

    progress_data["status"] = "completed"
    _write_progress(progress_data)


if __name__ == "__main__":
    run()
