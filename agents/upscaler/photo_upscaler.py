"""
Photo Upscaler — апскейл фотографий сессии.

Запуск:
  py agents/upscaler/photo_upscaler.py
  py agents/upscaler/photo_upscaler.py --project Video_20260305_222350 --method lanczos --resolution 2k
"""

import argparse
import re
import sys
import time
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(BASE_DIR))

MEDIA_DIR = BASE_DIR / "data" / "media"

RESOLUTION_MAP = {
    "1080": (1920, 1080),
    "2k":   (2560, 1440),
    "4k":   (3840, 2160),
}

_SESSION_RE = re.compile(r"^Video_\d{8}_\d{6}$")


def find_latest_session() -> Path | None:
    if not MEDIA_DIR.exists():
        return None
    sessions = sorted(
        [d for d in MEDIA_DIR.iterdir() if d.is_dir() and _SESSION_RE.match(d.name)],
        key=lambda d: d.name,
        reverse=True,
    )
    return sessions[0] if sessions else None


def upscale_photo_pillow(src: Path, dst: Path, target_w: int, target_h: int, method: str) -> None:
    from PIL import Image
    resample = Image.LANCZOS if method == "lanczos" else Image.BICUBIC
    with Image.open(src) as img:
        orig_w, orig_h = img.size
        # Масштабируем пропорционально, вписывая в target
        ratio = min(target_w / orig_w, target_h / orig_h)
        if ratio <= 1.0:
            # Уже достаточно большое — просто копируем
            img.save(dst)
            return
        new_w = int(orig_w * ratio)
        new_h = int(orig_h * ratio)
        upscaled = img.resize((new_w, new_h), resample)
        upscaled.save(dst)


def upscale_photo_realesrgan(src: Path, dst: Path) -> bool:
    """Запускает Real-ESRGAN если установлен. Возвращает True при успехе."""
    import subprocess
    # Ищем realesrgan-ncnn-vulkan в PATH и рядом
    candidates = [
        "realesrgan-ncnn-vulkan",
        str(BASE_DIR / "tools" / "realesrgan-ncnn-vulkan.exe"),
    ]
    for exe in candidates:
        try:
            r = subprocess.run(
                [exe, "-i", str(src), "-o", str(dst), "-n", "realesrgan-x4plus-anime"],
                capture_output=True, timeout=120,
            )
            if r.returncode == 0 and dst.exists():
                return True
        except (FileNotFoundError, subprocess.TimeoutExpired):
            continue
    return False


def run(session_name: str | None, method: str, resolution: str) -> None:
    t_start = time.time()

    # Найти сессию
    if session_name:
        session_dir = MEDIA_DIR / session_name
        if not session_dir.exists():
            print(f"[Upscaler] ⚠️ Сессия не найдена: {session_name}", flush=True)
            sys.exit(1)
    else:
        session_dir = find_latest_session()
        if not session_dir:
            print("[Upscaler] ⚠️ Нет ни одной сессии в data/media/", flush=True)
            sys.exit(1)

    photos_dir = session_dir / "photos"
    if not photos_dir.exists():
        print(f"[Upscaler] ⚠️ Папка photos не найдена: {photos_dir}", flush=True)
        sys.exit(1)

    photos = sorted(photos_dir.glob("*.png")) + sorted(photos_dir.glob("*.jpg")) + \
             sorted(photos_dir.glob("*.webp"))
    if not photos:
        print("[Upscaler] ⚠️ Нет фотографий для апскейла", flush=True)
        sys.exit(1)

    target_w, target_h = RESOLUTION_MAP.get(resolution, (1920, 1080))
    res_label = {"1080": "1080p", "2k": "2K", "4k": "4K"}.get(resolution, resolution)

    print(f"[Upscaler] Сессия: {session_dir.name}", flush=True)
    print(f"[Upscaler] Метод: {method} → {res_label} ({target_w}×{target_h})", flush=True)
    print(f"[Upscaler] Фото: {len(photos)} шт.", flush=True)

    done = 0
    for photo in photos:
        try:
            if method == "realesrgan":
                ok = upscale_photo_realesrgan(photo, photo)
                if not ok:
                    # Fallback to lanczos
                    upscale_photo_pillow(photo, photo, target_w, target_h, "lanczos")
            else:
                upscale_photo_pillow(photo, photo, target_w, target_h, method)
            done += 1
            pct = int(done / len(photos) * 100)
            print(f"[Upscaler] Готово: {done}/{len(photos)} {pct}%  [{photo.name}]", flush=True)
        except Exception as e:
            print(f"[Upscaler] ⚠️ Ошибка {photo.name}: {e}", flush=True)

    elapsed = time.time() - t_start
    mins, secs = divmod(int(elapsed), 60)
    print(f"[Upscaler] Обработано: {done}", flush=True)
    print(f"[Upscaler] Время: {mins}м {secs}с", flush=True)
    print(f"[Upscaler] ✅ Апскейл фото завершён!", flush=True)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Апскейл фото сессии")
    parser.add_argument("--project",    help="Имя сессии (по умолчанию — последняя)")
    parser.add_argument("--method",     choices=["realesrgan", "lanczos", "bicubic"],
                        default="lanczos", help="Метод апскейла")
    parser.add_argument("--resolution", choices=["1080", "2k", "4k"],
                        default="2k", help="Целевое разрешение")
    args = parser.parse_args()
    run(args.project, args.method, args.resolution)
