"""
Vision Client — round-robin по 2 серверам Qwen2.5-VL-7B (порты 8765-8766).

Запуск серверов:
  py -X utf8 agents/vision/vision_server.py --port 8765
  py -X utf8 agents/vision/vision_server.py --port 8766
"""

import itertools
import threading
import requests

VISION_SERVERS = [
    "http://localhost:8765",
    "http://localhost:8766",
]

_server_cycle = itertools.cycle(VISION_SERVERS)
_lock         = threading.Lock()


def get_next_server() -> str:
    with _lock:
        return next(_server_cycle)


def get_available_servers() -> list[str]:
    available = []
    for url in VISION_SERVERS:
        try:
            r = requests.get(f"{url}/health", timeout=2)
            if r.status_code == 200 and r.json().get("model_loaded"):
                available.append(url)
        except Exception:
            pass
    return available


def check_health() -> list[str]:
    """Проверить доступные серверы. Raises RuntimeError если ни одного."""
    available = get_available_servers()
    if not available:
        raise RuntimeError(
            "Нет доступных Vision серверов!\n"
            "Запусти:\n"
            "  py -X utf8 agents/vision/vision_server.py --port 8765\n"
            "  py -X utf8 agents/vision/vision_server.py --port 8766"
        )
    print(f"Vision серверов: {len(available)}/2  {available}", flush=True)
    return available


def analyze_video(video_path: str, segment_text: str = "", server_url: str | None = None) -> dict | None:
    """Анализировать 1 клип. Возвращает dict или None при ошибке."""
    if server_url is None:
        server_url = get_next_server()
    try:
        r = requests.post(
            f"{server_url}/analyze",
            json={"video_path": str(video_path), "segment_text": segment_text},
            timeout=60,
        )
        r.raise_for_status()
        return r.json()
    except Exception as e:
        print(f"  [ERR] {server_url}/analyze: {e}", flush=True)
        return None


def analyze_batch(
        video_path1: str,
        video_path2: str,
        video_path3: str = "",
        video_path4: str = "",
        server_url:  "str | None" = None,
        niche:       str = "cosmos",
) -> "dict | None":
    """Анализировать до 4 клипов за 1 inference. Возвращает dict или None при ошибке."""
    if server_url is None:
        server_url = get_next_server()
    try:
        r = requests.post(
            f"{server_url}/analyze_batch",
            json={
                "video_path1": str(video_path1),
                "video_path2": str(video_path2),
                "video_path3": str(video_path3),
                "video_path4": str(video_path4),
                "niche":       niche,
            },
            timeout=120,
        )
        r.raise_for_status()
        return r.json()
    except Exception as e:
        print(f"  ❌ Batch ошибка: {e}", flush=True)
        return None
