"""
Prompt Generator — Photo & Video
---------------------------------
Интерактивный запуск:
  py agents/prompt_generator/prompt_generator.py

Запуск с аргументами:
  py agents/prompt_generator/prompt_generator.py --type photo --photo-master photo_master_prompt.txt
  py agents/prompt_generator/prompt_generator.py --type photo --photo-master photo_master_prompt.txt --photo-platform gemini
  py agents/prompt_generator/prompt_generator.py --type video --video-master master_video_grok.txt --video-platform grok
  py agents/prompt_generator/prompt_generator.py --type both  --photo-master photo_master_prompt.txt --video-master master_video_veo3.txt
  py agents/prompt_generator/prompt_generator.py --type both  --photo-master photo_master_prompt.txt --video-master master_video_veo3.txt --project Video_20260227_220628

Платформы: gemini | flow | grok
  --photo-platform  (default: gemini)
  --video-platform  (default: grok)
"""

import argparse
import io
import json
import os
import re
import subprocess
import sys
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime
from pathlib import Path

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")

# Claude CLI в PATH
_CLAUDE_DIR = Path(os.environ.get("APPDATA", "")) / "Claude/claude-code"
if _CLAUDE_DIR.exists():
    versions = sorted(_CLAUDE_DIR.iterdir(), reverse=True)
    for v in versions:
        exe = v / "claude.exe"
        if exe.exists():
            os.environ["PATH"] = str(v) + os.pathsep + os.environ.get("PATH", "")
            break

BASE_DIR        = Path(__file__).parent.parent.parent
TRANSCRIPTS_DIR = BASE_DIR / "data" / "transcripts"
PROMPTS_DIR     = BASE_DIR / "data" / "prompts"
PHOTO_MASTERS   = BASE_DIR / "config" / "master_prompts" / "photo"
VIDEO_MASTERS   = BASE_DIR / "config" / "master_prompts" / "video"
PROGRESS_FILE   = BASE_DIR / "temp" / "prompt_progress.json"

BATCH_SIZE   = 10
MAX_PARALLEL = 5


def _write_progress(data: dict) -> None:
    PROGRESS_FILE.parent.mkdir(parents=True, exist_ok=True)
    try:
        PROGRESS_FILE.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
    except Exception:
        pass


# ---------------------------------------------------------------------------
# Интерактивный выбор
# ---------------------------------------------------------------------------

def ask_type() -> str:
    """Возвращает 'photo', 'video' или 'both'."""
    print("\nЧто генерировать?")
    print("  1. \U0001f4f8 Фото промпты")
    print("  2. \U0001f3ac Видео промпты")
    print("  3. \U0001f4f8\U0001f3ac Оба")
    print()
    while True:
        choice = input("Введи номер (1-3): ").strip()
        if choice == "1":
            return "photo"
        if choice == "2":
            return "video"
        if choice == "3":
            return "both"
        print("  Неверный ввод.")


def list_masters(folder: Path) -> list[str]:
    if not folder.exists():
        return []
    return sorted(f.name for f in folder.iterdir() if f.is_file() and f.suffix == ".txt")


def ask_master(label: str, folder: Path) -> str:
    """Возвращает имя файла мастер-промпта."""
    files = list_masters(folder)
    if not files:
        raise FileNotFoundError(f"Нет .txt файлов в {folder}")
    if len(files) == 1:
        print(f"\nМастер-промпт для {label}: {files[0]}")
        return files[0]
    print(f"\nВыбери мастер-промпт для {label}:")
    for i, f in enumerate(files, 1):
        print(f"  {i}. {f}")
    print()
    while True:
        choice = input(f"Введи номер (1-{len(files)}): ").strip()
        if choice.isdigit() and 1 <= int(choice) <= len(files):
            return files[int(choice) - 1]
        print("  Неверный ввод.")


# ---------------------------------------------------------------------------
# Утилиты
# ---------------------------------------------------------------------------

_SESSION_RE = re.compile(r"^Video_\d{8}_\d{6}$")


def find_latest_session() -> str | None:
    if not TRANSCRIPTS_DIR.exists():
        return None
    folders = [
        d for d in TRANSCRIPTS_DIR.iterdir()
        if d.is_dir() and _SESSION_RE.match(d.name)
    ]
    if not folders:
        return None
    return max(folders, key=lambda f: f.name).name


def load_master(folder: Path, filename: str) -> str:
    path = folder / filename
    if not path.exists():
        raise FileNotFoundError(f"Мастер-промпт не найден: {path}")
    return path.read_text(encoding="utf-8").strip()


def load_segments(session: str) -> list[dict]:
    p = TRANSCRIPTS_DIR / session / "result.json"
    if not p.exists():
        raise FileNotFoundError(f"result.json не найден: {p}")
    with open(p, encoding="utf-8") as f:
        data = json.load(f)
    return data["segments"] if isinstance(data, dict) else data


def load_photo_prompts(session: str) -> list[tuple[str, str]]:
    """Загружает фото-промпты из photo_prompts.json для standalone видео-режима."""
    p = PROMPTS_DIR / session / "photo_prompts.json"
    if not p.exists():
        raise FileNotFoundError(
            f"photo_prompts.json не найден: {p}\n"
            f"Сначала сгенерируй фото-промпты (--type photo)."
        )
    with open(p, encoding="utf-8") as f:
        data = json.load(f)
    return [(item["photo_prompt"], item.get("negative_prompt", "")) for item in data]


# ---------------------------------------------------------------------------
# Claude CLI
# ---------------------------------------------------------------------------

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


# ---------------------------------------------------------------------------
# Фото: SEGMENT N формат
# ---------------------------------------------------------------------------

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


def generate_all_photo(
    master: str,
    segments: list[dict],
    progress_ref: dict | None = None,
) -> list[tuple[str, str]]:
    batches = [segments[i:i + BATCH_SIZE] for i in range(0, len(segments), BATCH_SIZE)]
    n_batches = len(batches)
    total = len(segments)

    def _run_batch(idx: int, batch: list[dict]) -> tuple[int, list[tuple[str, str]]]:
        s_id = batch[0]["id"]
        e_id = batch[-1]["id"]
        print(f"    Батч {idx+1}/{n_batches}: #{s_id}–#{e_id} ...", end=" ", flush=True)
        raw = call_claude(build_photo_prompt(master, batch))
        results = parse_photo_output(raw, len(batch))
        print(f"OK ({len(results)})")
        return idx, results

    batch_results: dict[int, list[tuple[str, str]]] = {}
    completed_count = 0

    with ThreadPoolExecutor(max_workers=MAX_PARALLEL) as executor:
        futures = {
            executor.submit(_run_batch, idx, batch): idx
            for idx, batch in enumerate(batches)
        }
        for future in as_completed(futures):
            idx, results = future.result()
            batch_results[idx] = results
            completed_count += len(results)
            if progress_ref is not None:
                progress_ref["current"] = min(completed_count, total)
                _write_progress(progress_ref)

    # Собираем в правильном порядке
    all_results: list[tuple[str, str]] = []
    for i in sorted(batch_results.keys()):
        all_results.extend(batch_results[i])

    if progress_ref is not None:
        progress_ref["current"] = len(all_results)
        progress_ref["current_text"] = (all_results[-1][0][:50] + "...") if all_results else ""
        _write_progress(progress_ref)

    return all_results


# ---------------------------------------------------------------------------
# Видео: Final Video Prompt формат
# ---------------------------------------------------------------------------

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
        fvp_match = re.search(r"Final\s+Video\s+Prompt\s*\n", part, re.IGNORECASE)
        if fvp_match:
            video_prompt = part[fvp_match.end():].strip()
        else:
            video_prompt = part.strip()
        results.append(video_prompt)
    if len(results) != n_segments:
        raise ValueError(
            f"Ожидалось {n_segments} видео-промптов, получено {len(results)}\n"
            f"Ответ (первые 500 символов):\n{text[:500]}"
        )
    return results


def generate_all_video(
    master: str,
    segments: list[dict],
    photo_results: list[tuple[str, str]],
    progress_ref: dict | None = None,
) -> list[str]:
    batches = [segments[i:i + BATCH_SIZE] for i in range(0, len(segments), BATCH_SIZE)]
    photo_batches = [photo_results[i:i + BATCH_SIZE] for i in range(0, len(photo_results), BATCH_SIZE)]
    n_batches = len(batches)
    total = len(segments)

    def _run_batch(
        idx: int,
        batch: list[dict],
        photo_batch: list[tuple[str, str]],
    ) -> tuple[int, list[str]]:
        s_id = batch[0]["id"]
        e_id = batch[-1]["id"]
        print(f"    Батч {idx+1}/{n_batches}: #{s_id}–#{e_id} ...", end=" ", flush=True)
        raw = call_claude(build_video_prompt(master, batch, photo_batch))
        prompts = parse_video_output(raw, len(batch))
        print(f"OK ({len(prompts)})")
        return idx, prompts

    batch_results: dict[int, list[str]] = {}
    completed_count = 0

    with ThreadPoolExecutor(max_workers=MAX_PARALLEL) as executor:
        futures = {
            executor.submit(_run_batch, idx, batch, photo_batch): idx
            for idx, (batch, photo_batch) in enumerate(zip(batches, photo_batches))
        }
        for future in as_completed(futures):
            idx, prompts = future.result()
            batch_results[idx] = prompts
            completed_count += len(prompts)
            if progress_ref is not None:
                progress_ref["current"] = min(completed_count, total)
                _write_progress(progress_ref)

    # Собираем в правильном порядке
    all_prompts: list[str] = []
    for i in sorted(batch_results.keys()):
        all_prompts.extend(batch_results[i])

    if progress_ref is not None:
        progress_ref["current"] = len(all_prompts)
        progress_ref["current_text"] = (all_prompts[-1][:50] + "...") if all_prompts else ""
        _write_progress(progress_ref)

    return all_prompts


# ---------------------------------------------------------------------------
# Сохранение
# ---------------------------------------------------------------------------

def save_photo(
    session: str,
    segments: list[dict],
    results: list[tuple[str, str]],
) -> tuple[Path, Path]:
    out_dir = PROMPTS_DIR / session
    out_dir.mkdir(parents=True, exist_ok=True)

    data = [
        {
            "id":              s["id"],
            "start":           s["start"],
            "end":             s["end"],
            "text":            s["text"],
            "photo_prompt":    pos,
            "negative_prompt": neg,
        }
        for s, (pos, neg) in zip(segments, results)
    ]
    j = out_dir / "photo_prompts.json"
    t = out_dir / "photo_prompts.txt"
    j.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")

    # TXT: один промпт на строку, пустая строка между
    txt_lines = []
    for pos, _neg in results:
        prompt = pos.strip().rstrip(",")
        txt_lines.append(prompt)
    t.write_text("\n\n".join(txt_lines), encoding="utf-8")
    return j, t


def save_video(session: str, segments: list[dict], prompts: list[str]) -> tuple[Path, Path]:
    out_dir = PROMPTS_DIR / session
    out_dir.mkdir(parents=True, exist_ok=True)

    data = [
        {
            "id":           s["id"],
            "start":        s["start"],
            "end":          s["end"],
            "text":         s["text"],
            "video_prompt": p,
        }
        for s, p in zip(segments, prompts)
    ]
    j = out_dir / "video_prompts.json"
    t = out_dir / "video_prompts.txt"
    j.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")

    # TXT: промпты разделены \n\n; внутренние \n\n → \n чтобы не ломать парсинг
    txt_lines = [p.strip().replace("\n\n", "\n") for p in prompts]
    t.write_text("\n\n".join(txt_lines), encoding="utf-8")
    return j, t


# ---------------------------------------------------------------------------
# Запуск
# ---------------------------------------------------------------------------

def run() -> None:
    parser = argparse.ArgumentParser(description="Генератор фото + видео промптов")
    parser.add_argument("--type",           choices=["photo", "video", "both"], help="Тип генерации")
    parser.add_argument("--photo-master",   help="Имя файла мастер-промпта для фото")
    parser.add_argument("--video-master",   help="Имя файла мастер-промпта для видео")
    parser.add_argument("--project",        help="Имя сессии (по умолчанию — последняя)")
    parser.add_argument("--photo-platform", choices=["gemini", "flow", "grok"],
                        default="gemini", help="Платформа для фото (default: gemini)")
    parser.add_argument("--video-platform", choices=["gemini", "flow", "grok"],
                        default="grok",   help="Платформа для видео (default: grok)")
    args = parser.parse_args()

    # ── claude CLI ────────────────────────────────────────────────────────
    if subprocess.run(["claude", "--version"], capture_output=True).returncode != 0:
        print("ОШИБКА: claude CLI не найден.")
        sys.exit(1)

    # ── Тип генерации ─────────────────────────────────────────────────────
    gen_type = args.type or ask_type()

    # ── Мастер-промпты ────────────────────────────────────────────────────
    photo_master_name = args.photo_master
    video_master_name = args.video_master

    if gen_type in ("photo", "both") and not photo_master_name:
        photo_master_name = ask_master("фото", PHOTO_MASTERS)
    if gen_type in ("video", "both") and not video_master_name:
        video_master_name = ask_master("видео", VIDEO_MASTERS)

    # ── Сессия ────────────────────────────────────────────────────────────
    session = args.project or find_latest_session()
    if not session:
        print("Нет сессий в data/transcripts/")
        sys.exit(1)

    print(f"\nСессия   : {session}")
    if gen_type in ("photo", "both"):
        print(f"Фото платформа : {args.photo_platform}")
    if gen_type in ("video", "both"):
        print(f"Видео платформа: {args.video_platform}")
    segments = load_segments(session)
    print(f"Сегментов: {len(segments)}\n")

    # ── Прогресс ──────────────────────────────────────────────────────────
    progress_data: dict = {
        "type":         "photo" if gen_type in ("photo", "both") else "video",
        "current":      0,
        "total":        len(segments),
        "status":       "running",
        "current_text": "",
        "master_photo": photo_master_name or "",
        "master_video": video_master_name or "",
        "started_at":   datetime.now().strftime("%H:%M:%S"),
    }
    _write_progress(progress_data)

    # ══ ФОТО ПРОМПТЫ ══════════════════════════════════════════════════════
    photo_results: list[tuple[str, str]] = []
    if gen_type in ("photo", "both"):
        print("─" * 50)
        print(f"ФОТО ПРОМПТЫ  [{photo_master_name}]")
        print("─" * 50)

        photo_master = load_master(PHOTO_MASTERS, photo_master_name)
        print(f"📋 Мастер-промпт фото: {photo_master_name} ({len(photo_master)} символов)")
        print(f"\nГенерирую батчами по {BATCH_SIZE} сегментов...")

        photo_results = generate_all_photo(photo_master, segments, progress_data)
        pj, pt = save_photo(session, segments, photo_results)

        print(f"\nФото промпты готовы: {len(photo_results)} шт.")
        print(f"   JSON : {pj}")
        print(f"   TXT  : {pt}\n")

    # ══ ВИДЕО ПРОМПТЫ ═════════════════════════════════════════════════════
    video_prompts: list[str] = []
    if gen_type in ("video", "both"):
        print("─" * 50)
        print(f"ВИДЕО ПРОМПТЫ  [{video_master_name}]")
        print("─" * 50)

        video_master = load_master(VIDEO_MASTERS, video_master_name)
        print(f"📋 Мастер-промпт видео: {video_master_name} ({len(video_master)} символов)")

        # Для standalone video — грузим из файла
        if gen_type == "video":
            photo_results = load_photo_prompts(session)
            if len(photo_results) != len(segments):
                print(f"ОШИБКА: фото промптов {len(photo_results)}, сегментов {len(segments)}")
                sys.exit(1)
            print(f"Фото промпты загружены: {len(photo_results)} шт.")
        elif gen_type == "both":
            # Переключаемся на видео-фазу
            progress_data["type"]    = "video"
            progress_data["current"] = 0
            progress_data["current_text"] = ""
            _write_progress(progress_data)

        print(f"\nГенерирую батчами по {BATCH_SIZE} сегментов...")
        video_prompts = generate_all_video(video_master, segments, photo_results, progress_data)
        vj, vt = save_video(session, segments, video_prompts)

        print(f"\nВидео промпты готовы: {len(video_prompts)} шт.")
        print(f"   JSON : {vj}")
        print(f"   TXT  : {vt}\n")

    # ══ ИТОГ ══════════════════════════════════════════════════════════════
    print("═" * 50)
    print("Всё готово!")
    if gen_type in ("photo", "both"):
        print(f"   Фото  : {len(photo_results)} промптов  [{photo_master_name}]")
    if gen_type in ("video", "both"):
        print(f"   Видео : {len(video_prompts)} промптов  [{video_master_name}]")
    print(f"   Сегментов: {len(segments)}")
    if gen_type == "photo":
        match = len(photo_results) == len(segments)
    elif gen_type == "video":
        match = len(video_prompts) == len(segments)
    else:
        match = len(photo_results) == len(segments) == len(video_prompts)
    print(f"   Совпадают: {'Да' if match else 'НЕТ'}")
    print("═" * 50)

    progress_data["status"] = "completed"
    _write_progress(progress_data)


if __name__ == "__main__":
    run()
