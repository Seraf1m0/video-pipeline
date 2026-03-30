"""
stock_finder.py — каскад поиска стоковых видео.

Каскад:
  ЭТАП 1 — Pixabay: queries[0] → queries[1] → queries[2] (до 5 страниц каждый)
  ЭТАП 2 — Vecteezy: те же queries (Playwright persistent context)
  ЭТАП 3 — return None → Grok перегенерация (обрабатывается video_validator)

Источники УДАЛЕНЫ: Pexels, NASA APOD, NASA Video.

Vision-сервер на порту 8765 запускается ВРУЧНУЮ (используется video_validator).
При поиске стоков Vision НЕ используется — только тег-фильтр Pixabay.
"""

import json
import os
import subprocess
import sys
import threading
import urllib.request
from pathlib import Path

_stock_lock = threading.Lock()

_BASE_DIR = Path(__file__).resolve().parent.parent.parent
_TEMP     = _BASE_DIR / "temp"
_TEMP.mkdir(parents=True, exist_ok=True)

from dotenv import load_dotenv
load_dotenv(_BASE_DIR / "config" / ".env")

# ── Channel manager ────────────────────────────────────────────────────────────
try:
    _BOT_DIR = _BASE_DIR / "bot"
    if str(_BOT_DIR) not in sys.path:
        sys.path.insert(0, str(_BOT_DIR))
    from channel_manager import get_active_channel as _get_active_channel

    def get_stock_language() -> str:
        channel = _get_active_channel()
        if channel:
            return channel["validator"].get("stock_query_language", "en")
        return "en"
except Exception:
    def get_stock_language() -> str:
        return "en"

# Глобальные трекеры — живут всю сессию
_used_stock_ids  : set[str] = set()
_used_stock_urls : set[str] = set()

# Слова которые подтверждают что запрос космический
_SPACE_WORDS = {
    "space", "universe", "cosmos", "galaxy", "galaxies", "nebula", "nebulae",
    "star", "stars", "planet", "planets", "solar", "cosmic", "astronomy",
    "astronaut", "spacecraft", "telescope", "orbit", "nasa", "supernova",
    "black hole", "milky", "andromeda", "asteroid", "comet", "mars",
    "saturn", "jupiter", "neptune", "venus", "mercury", "moon", "sun",
    "interstellar", "gravitational", "quasar", "pulsar", "neutron",
    "exoplanet", "hubble", "webb", "deep space", "outer space", "aurora",
}

# Вайтлист для cosmos — только эти слова разрешены в поисковом запросе
_COSMOS_WHITELIST = {
    "galaxy", "galaxies", "universe", "cosmos", "cosmic", "space", "nebula",
    "nebulae", "star", "stars", "stellar", "planet", "planets", "planetary",
    "telescope", "astronomy", "astronomer", "astronomical", "astrophysics",
    "supernova", "supernovae", "neutron", "quasar", "pulsar", "comet",
    "asteroid", "meteor", "moon", "solar", "orbital", "orbit", "exoplanet",
    "dark", "matter", "energy", "gravitational", "gravity", "quantum",
    "radiation", "infrared", "redshift", "cosmology", "cosmological",
    "inflation", "milky", "way", "andromeda", "hubble", "webb", "james",
    "nasa", "interstellar", "intergalactic", "galactic", "dwarf", "cluster",
    "void", "formation", "explosion", "expansion", "microwave", "background",
    "cmb", "origin", "ancient", "distant", "deep", "satellite", "spacecraft",
    "rocket", "launch", "exploration", "black", "hole", "big", "bang",
    "aurora", "eclipse", "constellation", "meteorite",
}

# Ротационный пул fallback-запросов
_COSMOS_FALLBACK_POOL = [
    "spiral galaxy deep space",
    "nebula stars cosmos",
    "universe expansion dark matter",
    "black hole accretion disk",
    "Milky Way galaxy stars",
    "supernova explosion space",
    "cosmic microwave background",
    "planet solar system orbit",
    "asteroid space cosmos",
    "star formation nebula",
    "deep space telescope galaxy",
    "Saturn rings planet",
    "Mars red planet surface",
    "comet tail space",
    "galaxy cluster universe",
    "neutron star pulsar",
    "exoplanet transit star",
    "aurora borealis atmosphere",
    "moon surface craters",
    "Jupiter storm planet",
    "stellar nursery nebula",
    "dark energy universe expansion",
    "gravitational lensing galaxy",
    "hubble telescope deep field",
]

# Перевод DE→EN
_DE_TO_EN = {
    "james-webb-teleskop":       "James Webb telescope",
    "james webb teleskop":       "James Webb telescope",
    "james webb":                "James Webb telescope",
    "james-webb":                "James Webb telescope",
    "hubble-teleskop":           "Hubble telescope",
    "hubble teleskop":           "Hubble telescope",
    "james webb space telescope": "James Webb Space Telescope",
    "marsrover":                 "Mars rover",
    "mars rover":                "Mars rover",
    "perseverance":              "Mars rover Perseverance",
    "curiosity":                 "Mars rover Curiosity",
    "internationale raumstation": "International Space Station",
    "iss":                       "International Space Station",
    "galaxien":                  "galaxies",
    "galaxie":                   "galaxy",
    "universum":                 "universe",
    "kosmologie":                "cosmology",
    "kosmisch":                  "cosmic",
    "teleskop":                  "telescope",
    "sterne":                    "stars",
    "stern":                     "star",
    "planeten":                  "planets",
    "planet":                    "planet",
    "schwarzes loch":            "black hole",
    "schwarze löcher":           "black holes",
    "mikrowellenhintergrund":    "cosmic microwave background",
    "astronomie":                "astronomy",
    "astronomen":                "astronomers",
    "astronaut":                 "astronaut",
    "raumfahrt":                 "spacecraft",
    "raumschiff":                "spacecraft",
    "dunkle materie":            "dark matter",
    "dunkle energie":            "dark energy",
    "nebel":                     "nebula",
    "urknall":                   "big bang",
    "supernovae":                "supernova",
    "supernova":                 "supernova",
    "neutronenstern":            "neutron star",
    "exoplaneten":               "exoplanets",
    "exoplanet":                 "exoplanet",
    "komet":                     "comet",
    "asteroid":                  "asteroid",
    "milchstraße":               "Milky Way galaxy",
    "andromeda":                 "Andromeda galaxy",
    "inflation":                 "cosmic inflation",
    "gravitationswellen":        "gravitational waves",
    "sonnensystem":              "solar system",
    "sonne":                     "sun star",
    "mond":                      "moon",
    "mars":                      "Mars planet",
    "saturn":                    "Saturn planet rings",
    "jupiter":                   "Jupiter planet",
    "neptun":                    "Neptune planet",
    "weltraum":                  "outer space",
    "weltall":                   "outer space",
    "kosmos":                    "cosmos universe",
}

_STOPWORDS = {
    "the", "a", "an", "and", "or", "but", "in", "on", "at", "to", "for",
    "of", "with", "by", "from", "is", "are", "was", "were", "be", "been",
    "has", "have", "had", "do", "does", "did", "will", "would", "could",
    "should", "may", "might", "can", "this", "that", "these", "those",
    "it", "its", "as", "if", "so", "not", "no", "than", "then", "when",
    "where", "which", "who", "what", "how", "why", "all", "each", "every",
    "more", "most", "also", "just", "even", "only", "such", "into", "over",
    "after", "before", "through", "between", "about", "during", "while",
    "our", "their", "they", "we", "he", "she", "you", "i", "my", "your",
    "his", "her", "us", "them", "me", "up", "out", "off", "down", "very",
    "year", "years", "time", "first", "new", "old", "large", "small",
    "much", "many", "few", "some", "any", "other", "same", "different",
    "there", "here", "now", "today", "light", "away", "far", "near",
    "long", "high", "deep", "around", "within", "without",
    "because", "since", "until", "though", "although", "however",
    "let", "make", "made", "use", "used", "take", "given", "come",
    "go", "get", "put", "set", "see", "look", "like", "know", "think",
    "say", "show", "tell", "find", "found", "become", "keep", "hold",
    "turn", "move", "allow", "remain", "appear", "seem",
    "called", "based", "located", "formed", "known", "named", "said",
}


# ── Генерация запроса (fallback если Claude не дал запросы) ──────────────────

def generate_stock_query(segment_text: str, niche: str = "cosmos", segment_idx: int = 0) -> str:
    """Генерировать поисковый запрос без API (keyword-based)."""
    import re
    text_lower = segment_text.lower()

    # DE→EN перевод (многословные сначала)
    en_parts = []
    remaining = text_lower
    for de_phrase in sorted(_DE_TO_EN.keys(), key=len, reverse=True):
        if de_phrase in remaining:
            en_parts.append(_DE_TO_EN[de_phrase])
            remaining = remaining.replace(de_phrase, " ", 1)

    if en_parts:
        query_words = []
        for part in en_parts[:2]:
            query_words.extend(part.split())
        if niche == "cosmos" and "space" not in query_words:
            query_words = ["space"] + query_words
        return " ".join(query_words[:6])

    # Английский текст
    text   = re.sub(r"[^\w\s]", " ", text_lower)
    words  = text.split()
    hits   = [w for w in words if w in _COSMOS_WHITELIST]
    seen: set[str] = set()
    selected: list[str] = []
    for w in hits:
        if w not in seen:
            seen.add(w)
            selected.append(w)
        if len(selected) >= 4:
            break

    if not selected or selected == ["space"]:
        return _COSMOS_FALLBACK_POOL[segment_idx % len(_COSMOS_FALLBACK_POOL)]

    if "space" not in selected:
        selected = ["space"] + selected
    return " ".join(selected[:5])


# ── Основная функция поиска ───────────────────────────────────────────────────

def find_stock_video(
    search_queries: list[str],
    min_duration: int = 10,
    niche: str = "cosmos",
    channel_id: str = "",
    session: str = "",
) -> dict | None:
    """
    Найти стоковое видео.

    Каскад:
      ЭТАП 1 — Pixabay: queries[0] → queries[1] → queries[2]
               (до 5 страниц per_page=20 на запрос)
      ЭТАП 2 — Vecteezy: те же queries
      Возвращает None → caller запускает Grok перегенерацию

    search_queries — список из 3 запросов от Claude (или fallback-сгенерированных).
    """
    global _used_stock_ids, _used_stock_urls

    # Загрузить заблокированные ID из кэша (общий для DE+FR, последние 10 роликов)
    try:
        from stock_cache import get_used_ids
        cached_ids = get_used_ids(last_n=10)
        _used_stock_ids.update(cached_ids)
    except Exception as e:
        print(f"  ⚠️  Кэш недоступен: {e}", flush=True)

    if not search_queries:
        search_queries = [_COSMOS_FALLBACK_POOL[0]]

    with _stock_lock:
        # ── ЭТАП 1: Pixabay ──────────────────────────────────────────────────
        for q in search_queries:
            result = _search_pixabay_query(q, min_duration, niche)
            if result:
                if channel_id or session:
                    try:
                        from stock_cache import add_to_cache
                        add_to_cache(result, channel_id, session,
                                     local_file=result.get("local_path"))
                    except Exception:
                        pass
                return result

        print(f"  📦 Pixabay исчерпан — переходим к Vecteezy", flush=True)

        # ── ЭТАП 2: Vecteezy ─────────────────────────────────────────────────
        for q in search_queries:
            result = _search_vecteezy_query(q, min_duration)
            if result:
                if channel_id or session:
                    try:
                        from stock_cache import add_to_cache
                        add_to_cache(result, channel_id, session,
                                     local_file=result.get("local_path"))
                    except Exception:
                        pass
                return result

        print(f"  📦 Vecteezy исчерпан — нет стоков", flush=True)

    # ЭТАП 3 — return None → Grok (обрабатывается video_validator)
    return None


def _prefilter_pixabay_hit(
    video: dict, quality: str
) -> tuple[bool, str, int, int, float]:
    """
    Проверить размеры/ratio кандидата Pixabay ДО скачивания по метаданным API.
    Returns: (ok, url, width, height, ratio)
    """
    v   = video.get("videos", {}).get(quality, {})
    url = v.get("url", "")
    if not url:
        return False, "", 0, 0, 0.0

    w = v.get("width", 0)
    h = v.get("height", 0)
    if w == 0 or h == 0:
        return False, "", 0, 0, 0.0
    if h > w:
        print(f"  ⚠️  Portrait {w}x{h} → skip", flush=True)
        return False, "", 0, 0, 0.0
    ratio = w / h
    if not (1.67 <= ratio <= 1.87):
        print(f"  ⚠️  Неверный ratio {w}x{h}={ratio:.2f} → skip", flush=True)
        return False, "", 0, 0, 0.0
    if w < 1280 or h < 720:
        print(f"  ⚠️  Низкое разрешение {w}x{h} → skip", flush=True)
        return False, "", 0, 0, 0.0

    return True, url, w, h, ratio


def _search_pixabay_query(
    query: str,
    min_duration: int,
    niche: str,
    max_pages: int = 5,
) -> dict | None:
    """Перебрать до max_pages страниц Pixabay для одного запроса."""
    api_key = os.getenv("PIXABAY_API_KEY", "")
    if not api_key:
        print("  ⚠️  PIXABAY_API_KEY не задан", flush=True)
        return None

    import requests
    print(f"  🔍 Pixabay: '{query}'", flush=True)

    for page in range(1, max_pages + 1):
        params = {
            "key":        api_key,
            "q":          query,
            "video_type": "film",
            "per_page":   20,
            "page":       page,
            "min_width":  1280,
            "min_height": 720,
        }
        try:
            r    = requests.get("https://pixabay.com/api/videos/", params=params, timeout=10)
            hits = r.json().get("hits", [])
            if not hits:
                break

            for video in hits:
                vid_id = f"pixabay_{video['id']}"
                if vid_id in _used_stock_ids:
                    continue

                dur = video.get("duration", 0)
                if dur < min_duration:
                    continue

                # Тег-фильтр для cosmos
                if niche == "cosmos":
                    tags = video.get("tags", "").lower()
                    if not any(kw in tags for kw in _SPACE_WORDS):
                        continue

                for quality in ["large", "medium", "small"]:
                    ok, url, w, h, ratio = _prefilter_pixabay_hit(video, quality)
                    if not ok or url in _used_stock_urls:
                        continue

                    # Всё ок — скачиваем
                    _used_stock_ids.add(vid_id)
                    _used_stock_urls.add(url)
                    local = _download_temp(vid_id, url)
                    if local:
                        # Двойная проверка через ffprobe после скачивания
                        if not _verify_video_ratio(local):
                            print(f"  ⚠️  Post-download ratio fail → skip", flush=True)
                            local.unlink(missing_ok=True)
                            _used_stock_ids.discard(vid_id)
                            _used_stock_urls.discard(url)
                            continue
                        print(f"  ✅ Pixabay [{vid_id}] {w}x{h} стр.{page}: '{query}'", flush=True)
                        return {
                            "id":         vid_id,
                            "url":        url,
                            "source":     "pixabay",
                            "duration":   dur,
                            "width":      w,
                            "height":     h,
                            "ratio":      ratio,
                            "tags":       video.get("tags", ""),
                            "query":      query,
                            "local_path": str(local),
                        }
                    break  # не удалось скачать — пробуем следующее quality

        except Exception as e:
            print(f"  ⚠️  Pixabay стр.{page}: {e}", flush=True)
            break

    return None


def _verify_video_ratio(video_path: Path) -> bool:
    """Двойная проверка через ffprobe после скачивания — реально 16:9 landscape."""
    try:
        result = subprocess.run(
            ["ffprobe", "-v", "quiet", "-print_format", "json",
             "-show_streams", str(video_path)],
            capture_output=True, text=True, timeout=15,
        )
        info = json.loads(result.stdout)
        for stream in info.get("streams", []):
            if stream.get("codec_type") == "video":
                w = stream.get("width", 0)
                h = stream.get("height", 0)
                if w == 0 or h == 0:
                    return False
                if h > w:
                    print(f"  ❌ ffprobe portrait: {w}x{h}", flush=True)
                    return False
                ratio = w / h
                if not (1.67 <= ratio <= 1.87):
                    print(f"  ❌ ffprobe ratio: {w}x{h}={ratio:.2f}", flush=True)
                    return False
                if w < 1280 or h < 720:
                    print(f"  ❌ ffprobe resolution: {w}x{h}", flush=True)
                    return False
                return True
    except Exception as e:
        print(f"  ⚠️  ffprobe verify: {e}", flush=True)
        return False
    return False


def _search_vecteezy_query(query: str, min_duration: int) -> dict | None:
    """Поиск одного видео на Vecteezy через Playwright."""
    try:
        from vecteezy_finder import search_vecteezy, download_vecteezy
    except ImportError:
        print("  ⚠️  vecteezy_finder не найден", flush=True)
        return None

    print(f"  🔍 Vecteezy: '{query}'", flush=True)

    candidates = search_vecteezy(query, used_ids=_used_stock_ids)
    for cand in candidates:
        vid_id = cand["id"]
        if vid_id in _used_stock_ids:
            continue
        _used_stock_ids.add(vid_id)

        tmp = _TEMP / f"vecteezy_{vid_id}.mp4"
        if download_vecteezy(cand, tmp):
            print(f"  ✅ Vecteezy [{vid_id}]: '{query}'", flush=True)
            cand["local_path"] = str(tmp)
            return cand

    return None


def _download_temp(vid_id: str, url: str) -> Path | None:
    """Скачать видео во временный файл."""
    import requests
    safe_id = vid_id.replace("/", "_").replace(":", "_")
    tmp     = _TEMP / f"stock_{safe_id}.mp4"
    try:
        r = requests.get(url, stream=True, timeout=90)
        r.raise_for_status()
        with open(tmp, "wb") as f:
            for chunk in r.iter_content(chunk_size=512 * 1024):
                f.write(chunk)
        if tmp.stat().st_size < 10_240:
            tmp.unlink(missing_ok=True)
            return None
        return tmp
    except Exception as e:
        print(f"  ⚠️  Скачивание {vid_id}: {e}", flush=True)
        tmp.unlink(missing_ok=True)
        return None


# ── Утилиты ───────────────────────────────────────────────────────────────────

def download_and_verify(stock: dict, output_path: Path) -> bool:
    """Скачать сток напрямую (если local_path не задан)."""
    import requests
    try:
        r = requests.get(stock["url"], stream=True, timeout=90)
        r.raise_for_status()
        with open(output_path, "wb") as f:
            for chunk in r.iter_content(chunk_size=8192):
                f.write(chunk)

        result = subprocess.run(
            ["ffprobe", "-v", "quiet", "-print_format", "json",
             "-show_format", "-show_streams", str(output_path)],
            capture_output=True, text=True,
        )
        info = json.loads(result.stdout)
        dur  = float(info["format"].get("duration", 0))
        if dur < 8:
            print(f"  ⚠️  Слишком короткое: {dur:.1f}s", flush=True)
            output_path.unlink(missing_ok=True)
            return False
        for stream in info.get("streams", []):
            if stream.get("codec_type") == "video":
                w = stream.get("width", 0)
                h = stream.get("height", 0)
                if w < 1280 or h < 720:
                    print(f"  ⚠️  Низкое разрешение: {w}x{h}", flush=True)
                    output_path.unlink(missing_ok=True)
                    return False
        return True
    except Exception as e:
        print(f"  ❌ Скачивание: {e}", flush=True)
        return False


def load_used_stocks(session: str) -> None:
    global _used_stock_ids, _used_stock_urls
    path = _BASE_DIR / "data" / "transcripts" / session / "used_stocks.json"
    if path.exists():
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
            _used_stock_ids  = set(data.get("ids",  []))
            _used_stock_urls = set(data.get("urls", []))
            print(f"  📋 Загружено {len(_used_stock_ids)} использованных стоков", flush=True)
        except Exception:
            pass


def save_used_stocks(session: str) -> None:
    path = _BASE_DIR / "data" / "transcripts" / session / "used_stocks.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps({"ids": list(_used_stock_ids), "urls": list(_used_stock_urls)}, indent=2),
        encoding="utf-8",
    )
    print(f"  💾 Сохранено {len(_used_stock_ids)} стоков", flush=True)


# Алиас для обратной совместимости
def download_stock_video(stock: dict, output_path: Path) -> bool:
    return download_and_verify(stock, output_path)


def verify_duration(video_path: Path, min_duration: int = 10) -> bool:
    try:
        result = subprocess.run(
            ["ffprobe", "-v", "quiet", "-print_format", "json",
             "-show_format", str(video_path)],
            capture_output=True, text=True, timeout=15,
        )
        duration = float(json.loads(result.stdout)["format"]["duration"])
        return duration >= min_duration
    except Exception:
        return False
