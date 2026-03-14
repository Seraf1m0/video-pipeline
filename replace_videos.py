"""
Ручная замена конкретных видео стоками с Pixabay.
"""
import sys
import json
import hashlib
import subprocess
from pathlib import Path

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

BASE_DIR    = Path(__file__).parent
sys.path.insert(0, str(BASE_DIR / "agents" / "video_validator"))

from stock_finder import find_stock_video, download_and_verify, _used_stock_ids, _used_stock_urls

SESSION     = "Video_20260312_170838"
UPSCALED    = BASE_DIR / "data" / "media" / SESSION / "upscaled"
VIDEOS      = BASE_DIR / "data" / "media" / SESSION / "videos"
TRANSCRIPT  = BASE_DIR / "data" / "transcripts" / SESSION / "result.json"

# Видео которые нужно заменить
TO_REPLACE = [82, 86, 90]

# Явные английские запросы
CUSTOM_QUERIES = {
    82:  "nebula stars colorful cosmic",
    86:  "asteroid meteor space rock",
    90:  "galaxy cluster deep field universe",
}

# ── Строим хеш-карту всех существующих upscaled видео (для дедупликации) ──────
def file_hash(p: Path) -> str:
    h = hashlib.md5()
    with open(p, "rb") as f:
        for chunk in iter(lambda: f.read(65536), b""):
            h.update(chunk)
    return h.hexdigest()

print("  Считаю хеши существующих видео...")
_existing_hashes: dict[str, Path] = {}
output_dir = UPSCALED if UPSCALED.exists() else VIDEOS
for vp in output_dir.glob("video_*.mp4"):
    if "_original" in vp.stem:
        continue
    try:
        _existing_hashes[file_hash(vp)] = vp
    except Exception:
        pass
print(f"  Загружено {len(_existing_hashes)} хешей\n")

# Загружаем сегменты
data = json.loads(TRANSCRIPT.read_text(encoding="utf-8"))
segments = data["segments"] if isinstance(data, dict) else data
seg_map = {i+1: seg for i, seg in enumerate(segments)}


def normalize_video(src: Path, dst: Path) -> bool:
    cmd = [
        "ffmpeg", "-y", "-i", str(src),
        "-vf", "scale=1920:1080:force_original_aspect_ratio=decrease,pad=1920:1080:(ow-iw)/2:(oh-ih)/2,setsar=1",
        "-t", "10", "-r", "30",
        "-c:v", "libx264", "-preset", "fast", "-crf", "23",
        "-an", str(dst)
    ]
    r = subprocess.run(cmd, capture_output=True, timeout=120)
    return r.returncode == 0 and dst.exists() and dst.stat().st_size > 1024


def search_unique(query: str, idx: int, tries: int = 8):
    """Ищет уникальный сток — скачивает, нормализует во tmp, хеширует normalized и сравнивает."""
    import os
    import requests as req

    api_key = os.getenv("PIXABAY_API_KEY", "")
    if not api_key:
        return None, None

    for page in range(1, tries + 1):
        params = {
            "key": api_key, "q": query, "video_type": "film",
            "per_page": 15, "page": page, "min_width": 1280,
            "safesearch": "true", "order": "popular",
        }
        try:
            r = req.get("https://pixabay.com/api/videos/", params=params, timeout=10)
            hits = r.json().get("hits", [])
        except Exception:
            break
        if not hits:
            break

        for hit in hits:
            vid_id = f"pixabay_{hit['id']}"
            if vid_id in _used_stock_ids:
                continue
            videos = hit.get("videos", {})
            url = ""
            for q in ("large", "medium", "small"):
                vdata = videos.get(q, {})
                u = vdata.get("url", "")
                if u and vdata.get("width", 0) >= 1280:
                    url = u
                    break
            if not url or url in _used_stock_urls:
                continue

            # Скачиваем raw
            tmp_raw = BASE_DIR / "temp" / f"dedup_{idx:03d}_raw.mp4"
            tmp_raw.parent.mkdir(parents=True, exist_ok=True)
            stock_obj = {
                "id": vid_id, "url": url, "source": "pixabay", "query": query,
                "duration": hit.get("duration", 0),
            }
            if not download_and_verify(stock_obj, tmp_raw):
                continue

            # Нормализуем во временный файл → хешируем normalized
            tmp_norm = BASE_DIR / "temp" / f"dedup_{idx:03d}_norm.mp4"
            if not normalize_video(tmp_raw, tmp_norm):
                tmp_raw.unlink(missing_ok=True)
                continue
            tmp_raw.unlink(missing_ok=True)

            h = file_hash(tmp_norm)
            if h in _existing_hashes:
                dup = _existing_hashes[h]
                print(f"  ⚠️  Повторка (normalized hash) → {dup.name}, пропускаю")
                tmp_norm.unlink(missing_ok=True)
                _used_stock_ids.add(vid_id)
                continue

            # Уникальный!
            _used_stock_ids.add(vid_id)
            _used_stock_urls.add(url)
            _existing_hashes[h] = output_dir / f"video_{idx:03d}.mp4"
            return tmp_norm, stock_obj

    return None, None


print(f"🔄 Замена {len(TO_REPLACE)} видео для {SESSION}\n")

ok = 0
fail = 0
for idx in TO_REPLACE:
    seg = seg_map.get(idx)
    segment_text = seg.get("text", "") if seg else ""
    print(f"\n── #{idx:03d} ──────────────────────────────────")
    print(f"  Текст: '{segment_text[:80]}'")

    query = CUSTOM_QUERIES.get(idx)
    if not query:
        from stock_finder import generate_stock_query
        query = generate_stock_query(segment_text, niche="cosmos")

    print(f"  🔍 Запрос: '{query}'")
    tmp, stock = search_unique(query, idx)

    if not stock:
        print(f"  ❌ Уникальный сток не найден")
        fail += 1
        continue

    dst_up = UPSCALED / f"video_{idx:03d}.mp4"
    dst_vi = VIDEOS   / f"video_{idx:03d}.mp4"
    dst = dst_up if UPSCALED.exists() else dst_vi

    # tmp уже нормализован в search_unique — просто копируем
    try:
        import shutil
        shutil.move(str(tmp), str(dst))
        print(f"  ✅ Сохранено → {dst.name}")
        ok += 1
    except Exception as e:
        print(f"  ❌ Ошибка сохранения: {e}")
        fail += 1
        try:
            tmp.unlink(missing_ok=True)
        except Exception:
            pass

print(f"\n{'='*50}")
print(f"  ✅ Заменено: {ok}/{len(TO_REPLACE)}")
print(f"  ❌ Не удалось: {fail}/{len(TO_REPLACE)}")
