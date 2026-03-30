"""
stock_cache.py — общий кэш стоковых футажей для всех каналов (DE + FR).

Структура:
  data/stock_cache/
  ├── metadata.json   — метаданные каждого стока (бессрочно)
  ├── history.json    — {channel_id: {session: [stock_ids]}}
  └── files/          — MP4 файлы (хранить последние 10 роликов)
"""

import hashlib
import json
from datetime import datetime
from pathlib import Path

_BASE_DIR  = Path(__file__).resolve().parent.parent.parent
_CACHE_DIR = _BASE_DIR / "data" / "stock_cache"
_FILES_DIR = _CACHE_DIR / "files"
_META_PATH = _CACHE_DIR / "metadata.json"
_HIST_PATH = _CACHE_DIR / "history.json"

KEEP_LAST_SESSIONS = 10


def _ensure_dirs() -> None:
    _CACHE_DIR.mkdir(parents=True, exist_ok=True)
    _FILES_DIR.mkdir(parents=True, exist_ok=True)


def get_file_hash(file_path: str | Path) -> str:
    """SHA-256 хэш файла."""
    h = hashlib.sha256()
    with open(file_path, "rb") as f:
        for chunk in iter(lambda: f.read(65536), b""):
            h.update(chunk)
    return h.hexdigest()


def load_metadata() -> dict:
    if _META_PATH.exists():
        try:
            return json.loads(_META_PATH.read_text(encoding="utf-8"))
        except Exception:
            pass
    return {}


def save_metadata(meta: dict) -> None:
    _ensure_dirs()
    _META_PATH.write_text(
        json.dumps(meta, ensure_ascii=False, indent=2), encoding="utf-8"
    )


def load_history() -> dict:
    if _HIST_PATH.exists():
        try:
            return json.loads(_HIST_PATH.read_text(encoding="utf-8"))
        except Exception:
            pass
    return {}


def save_history(history: dict) -> None:
    _ensure_dirs()
    _HIST_PATH.write_text(
        json.dumps(history, ensure_ascii=False, indent=2), encoding="utf-8"
    )


def get_used_ids(channel_id: str = "", last_n: int = KEEP_LAST_SESSIONS) -> set:
    """
    ID стоков за последние last_n роликов.
    Если channel_id не задан — общий список для всех каналов (DE+FR).
    """
    history = load_history()
    all_sessions: list[tuple[str, list]] = []

    if channel_id:
        sessions = history.get(channel_id, {})
        for sess, ids in sessions.items():
            all_sessions.append((sess, ids))
    else:
        # Общий список — все каналы
        for ch, sessions in history.items():
            for sess, ids in sessions.items():
                all_sessions.append((sess, ids))

    # Сортировать по дате (session_id содержит дату)
    all_sessions.sort(key=lambda x: x[0], reverse=True)

    used: set[str] = set()
    for sess, ids in all_sessions[:last_n]:
        used.update(ids)

    print(f"  📋 Кэш: {len(used)} ID заблокировано (последние {last_n} роликов)", flush=True)
    return used


def add_to_cache(
    stock_data: dict,
    channel_id: str,
    session: str,
    local_file: str | Path | None = None,
    local_path: str | Path | None = None,  # алиас
) -> None:
    """
    Добавить сток в кэш.
    Сохраняет МЕТАДАННЫЕ бессрочно.
    local_file / local_path — путь к файлу (только для хэша, не копируется).
    """
    _ensure_dirs()

    stock_id = stock_data.get("id", "")
    if not stock_id:
        return

    fpath = local_file or local_path

    meta    = load_metadata()
    history = load_history()

    # ── Метаданные ────────────────────────────────────────────────────────────
    if stock_id not in meta:
        entry: dict = {
            "id":            stock_id,
            "source":        stock_data.get("source", ""),
            "url":           stock_data.get("url", ""),
            "file_hash":     "",
            "duration":      stock_data.get("duration", 0),
            "width":         stock_data.get("width", 0),
            "height":        stock_data.get("height", 0),
            "ratio":         stock_data.get("ratio", 0),
            "tags":          stock_data.get("tags", ""),
            "search_query":  stock_data.get("query", ""),
            "downloaded_at": datetime.now().isoformat(),
            "used_in":       [],
        }
        if fpath and Path(fpath).exists():
            try:
                entry["file_hash"] = get_file_hash(fpath)
            except Exception:
                pass
        meta[stock_id] = entry

    # Добавляем использование (без дублей)
    used_in = meta[stock_id].setdefault("used_in", [])
    if not any(
        u.get("channel") == channel_id and u.get("session") == session
        for u in used_in
    ):
        used_in.append({
            "channel": channel_id,
            "session": session,
            "date":    datetime.now().isoformat(),
        })

    save_metadata(meta)

    # ── История ───────────────────────────────────────────────────────────────
    if channel_id not in history:
        history[channel_id] = {}
    if session not in history[channel_id]:
        history[channel_id][session] = []
    if stock_id not in history[channel_id][session]:
        history[channel_id][session].append(stock_id)

    save_history(history)
    print(f"  💾 Кэш: {stock_id} сохранён (метаданные)", flush=True)


def check_duplicate_hash(file_path: str | Path) -> bool:
    """Проверить не является ли файл дубликатом по SHA256 хэшу."""
    meta = load_metadata()
    try:
        file_hash = get_file_hash(file_path)
        for stock_id, entry in meta.items():
            if entry.get("file_hash") == file_hash:
                print(f"  ⚠️  Дубликат по хэшу: {stock_id}", flush=True)
                return True
    except Exception:
        pass
    return False


def cleanup_old_files(keep_last: int = KEEP_LAST_SESSIONS) -> None:
    """
    Удалить MP4 файлы из files/ старше keep_last сессий.
    metadata.json хранится бессрочно.
    """
    history = load_history()
    meta    = load_metadata()

    keep_ids: set[str] = set()
    for ch, sessions in history.items():
        for sess in sorted(sessions.keys(), reverse=True)[:keep_last]:
            keep_ids.update(sessions[sess])

    deleted = 0
    for f in _FILES_DIR.glob("*.mp4"):
        matched = False
        for sid in meta:
            safe_id = sid.replace("/", "_").replace(":", "_")
            if f.stem == safe_id:
                if sid not in keep_ids:
                    f.unlink(missing_ok=True)
                    deleted += 1
                matched = True
                break
        if not matched:
            f.unlink(missing_ok=True)
            deleted += 1

    if deleted:
        print(f"  🗑️  Удалено {deleted} старых файлов из кэша", flush=True)
