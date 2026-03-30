"""
stock_history.py — трекер уникальности стоков.
Хранит использованные стоки за последние HISTORY_DEPTH роликов для каждого канала.
Стоки не повторяются в рамках 2-3 роликов.
"""

import json
from datetime import datetime
from pathlib import Path

HISTORY_DEPTH = 3  # сколько роликов помнить

_BASE_DIR = Path(__file__).resolve().parent.parent.parent


def get_history_path(channel_id: str) -> Path:
    lang = "de" if "de" in channel_id else "fr"
    return _BASE_DIR / "data" / "channels" / lang / "stock_history.json"


def load_history(channel_id: str) -> dict:
    """Загрузить историю стоков канала."""
    path = get_history_path(channel_id)
    if path.exists():
        try:
            with open(path, encoding="utf-8") as f:
                return json.load(f)
        except Exception:
            pass
    return {"videos": {}}


def save_history(channel_id: str, history: dict) -> None:
    """Сохранить историю стоков."""
    path = get_history_path(channel_id)
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(history, f, indent=2, ensure_ascii=False)


def get_used_stock_ids(channel_id: str) -> tuple[set, set]:
    """
    Получить все использованные ID стоков за последние HISTORY_DEPTH роликов.
    Возвращает (used_ids: set, used_urls: set).
    """
    history     = load_history(channel_id)
    videos      = history.get("videos", {})
    sorted_vids = sorted(videos.keys(), reverse=True)[:HISTORY_DEPTH]

    used_ids  : set[str] = set()
    used_urls : set[str] = set()

    for vid in sorted_vids:
        for stock in videos[vid].get("stocks", []):
            sid = stock.get("id", "")
            url = stock.get("url", "")
            if sid:
                used_ids.add(sid)
            if url:
                used_urls.add(url)

    print(
        f"  📋 История стоков ({len(sorted_vids)} роликов): "
        f"{len(used_ids)} ID заблокировано",
        flush=True,
    )
    return used_ids, used_urls


def add_stock_to_history(
    channel_id: str,
    session: str,
    stock_data: dict,
) -> None:
    """Добавить использованный сток в историю."""
    history = load_history(channel_id)
    if "videos" not in history:
        history["videos"] = {}

    if session not in history["videos"]:
        history["videos"][session] = {
            "date":   datetime.now().isoformat(),
            "stocks": [],
        }

    history["videos"][session]["stocks"].append({
        "id":     stock_data.get("id", ""),
        "url":    stock_data.get("url", ""),
        "source": stock_data.get("source", ""),
        "query":  stock_data.get("query", ""),
        "seg_id": stock_data.get("seg_id", ""),
    })

    # Оставляем только последние N+2 роликов (небольшой запас)
    sorted_vids = sorted(history["videos"].keys(), reverse=True)
    for old in sorted_vids[HISTORY_DEPTH + 2 :]:
        del history["videos"][old]

    save_history(channel_id, history)


def cleanup_old_history(channel_id: str) -> None:
    """Очистить историю старше HISTORY_DEPTH роликов."""
    history     = load_history(channel_id)
    videos      = history.get("videos", {})
    sorted_vids = sorted(videos.keys(), reverse=True)

    if len(sorted_vids) > HISTORY_DEPTH:
        for old in sorted_vids[HISTORY_DEPTH:]:
            del history["videos"][old]
        save_history(channel_id, history)
        print(
            f"  🗑️ Очищена история: оставлено {HISTORY_DEPTH} роликов",
            flush=True,
        )
