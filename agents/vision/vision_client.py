"""
Vision Client — round-robin по 2 серверам Qwen2.5-VL-7B (порты 8765-8766).

Запуск серверов:
  py -X utf8 agents/vision/vision_server.py --port 8765
  py -X utf8 agents/vision/vision_server.py --port 8766
"""

import base64
import io
import itertools
import threading
import requests
from PIL import Image

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


def analyze_thumbnail_placement(img: "Image.Image", server_url: str | None = None) -> dict | None:
    """
    Анализировать изображение и получить рекомендацию по размещению текста.

    Возвращает dict вида:
      {
        "layout":       "right_col" | "left_col" | "split" | "standard",
        "subject_side": "left" | "right" | "center",
        "text_zone":    {"x": 0.55, "y": 0.08, "w": 0.40, "h": 0.84},
        "bg_clean":     True,
        "elapsed_s":    1.2,
      }
    или None при ошибке (caller должен использовать fallback).
    """
    if server_url is None:
        server_url = get_next_server()

    # Encode image to base64
    buf = io.BytesIO()
    img.convert("RGB").save(buf, format="JPEG", quality=90)
    b64 = base64.b64encode(buf.getvalue()).decode()

    try:
        r = requests.post(
            f"{server_url}/analyze_thumbnail",
            json={"image_b64": b64},
            timeout=30,
        )
        r.raise_for_status()
        result = r.json()
        # Validate required fields
        if "layout" not in result or "text_zone" not in result:
            print(f"  [THUMB] Qwen response missing fields: {result}", flush=True)
            return None
        tz = result["text_zone"]
        if not all(k in tz for k in ("x", "y", "w", "h")):
            print(f"  [THUMB] Bad text_zone: {tz}", flush=True)
            return None
        return result
    except Exception as e:
        print(f"  [THUMB] analyze_thumbnail_placement error: {e}", flush=True)
        return None
