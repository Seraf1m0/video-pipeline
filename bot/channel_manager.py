"""
channel_manager.py — управление каналами Video Pipeline
--------------------------------------------------------
Каналы хранятся в bot/channels/<channel_id>/config.json
Активный канал — в bot/active_channel.json
"""

import json
from pathlib import Path

# Пути относительно корня проекта
_BASE_DIR          = Path(__file__).resolve().parent.parent
CHANNELS_DIR       = _BASE_DIR / "bot" / "channels"
ACTIVE_CHANNEL_FILE = _BASE_DIR / "bot" / "active_channel.json"


def get_all_channels() -> list[dict]:
    """Получить все доступные каналы (сортировка по имени папки)."""
    channels = []
    if not CHANNELS_DIR.exists():
        return channels
    for ch_dir in sorted(CHANNELS_DIR.iterdir()):
        config_path = ch_dir / "config.json"
        if config_path.exists():
            with open(config_path, encoding="utf-8") as f:
                channels.append(json.load(f))
    return channels


def get_active_channel() -> dict | None:
    """Получить конфиг активного канала (по умолчанию — первый)."""
    if ACTIVE_CHANNEL_FILE.exists():
        with open(ACTIVE_CHANNEL_FILE, encoding="utf-8") as f:
            data = json.load(f)
        channel_id = data.get("active_channel_id")
        if channel_id:
            config_path = CHANNELS_DIR / channel_id / "config.json"
            if config_path.exists():
                with open(config_path, encoding="utf-8") as f:
                    return json.load(f)

    # По умолчанию первый канал
    channels = get_all_channels()
    if channels:
        set_active_channel(channels[0]["id"])
        return channels[0]
    return None


def set_active_channel(channel_id: str) -> None:
    """Установить активный канал."""
    ACTIVE_CHANNEL_FILE.parent.mkdir(parents=True, exist_ok=True)
    with open(ACTIVE_CHANNEL_FILE, "w", encoding="utf-8") as f:
        json.dump({"active_channel_id": channel_id}, f, indent=2)
    print(f"[channel] Active: {channel_id}")


def get_channel_config(channel_id: str) -> dict | None:
    """Получить конфиг конкретного канала по id."""
    config_path = CHANNELS_DIR / channel_id / "config.json"
    if config_path.exists():
        with open(config_path, encoding="utf-8") as f:
            return json.load(f)
    return None
