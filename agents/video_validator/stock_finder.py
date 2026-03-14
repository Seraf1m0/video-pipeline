import os
import re
import json
import subprocess
import sys
import threading
import urllib.request
from pathlib import Path
from collections import Counter

_stock_lock = threading.Lock()  # Предотвращает race condition при выборе стока

# ── BLIP pre-check ────────────────────────────────────────────────────────────
_BASE_DIR        = Path(__file__).resolve().parent.parent.parent
_TEMP            = _BASE_DIR / "temp"
_TEMP.mkdir(parents=True, exist_ok=True)
_BLIP_SERVER_URL = "http://127.0.0.1:5679"


def _blip_alive() -> bool:
    try:
        with urllib.request.urlopen(f"{_BLIP_SERVER_URL}/ping", timeout=2) as r:
            return r.read() == b"pong"
    except Exception:
        return False


def _blip_is_cosmos(frame_path: Path) -> bool:
    """Быстрая BLIP-проверка: это космос/наука? Не мусор?"""
    if not frame_path or not frame_path.exists():
        return True  # не блокируем если кадр не извлечь
    payload = json.dumps({
        "image_path": str(frame_path),
        "questions": [
            "Does this image show space, stars, planets, galaxies, nebulae, spacecraft, "
            "telescope images, astronomy animations, or any outer space content?",
            "Is this image showing people, buildings, city, ocean, nature scenery, "
            "animals, food, music instruments, or any non-space content?",
        ]
    }).encode()
    req = urllib.request.Request(
        f"{_BLIP_SERVER_URL}/analyze",
        data=payload,
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=30) as r:
            answers = json.loads(r.read())["answers"]
        is_space   = answers[0].strip().lower().startswith("yes")
        is_garbage = answers[1].strip().lower().startswith("yes")
        return is_space and not is_garbage
    except Exception:
        return True  # при ошибке сервера — не блокируем


def _extract_frame_for_check(video_path: Path) -> Path | None:
    """Извлечь один средний кадр для BLIP."""
    out = _TEMP / f"blip_pre_{video_path.stem}.jpg"
    try:
        r = subprocess.run(
            ["ffprobe", "-v", "quiet", "-print_format", "json",
             "-show_format", str(video_path)],
            capture_output=True, text=True, timeout=15
        )
        dur = float(json.loads(r.stdout)["format"]["duration"])
        ts  = max(0.5, dur * 0.5)
        subprocess.run([
            "ffmpeg", "-y", "-ss", str(ts), "-i", str(video_path),
            "-vframes", "1", "-q:v", "2", "-loglevel", "quiet", str(out)
        ], capture_output=True, timeout=20)
        return out if out.exists() else None
    except Exception:
        return None


def _download_candidate(candidate: dict) -> Path | None:
    """Скачать кандидата во временный файл. Возвращает Path или None."""
    import requests
    vid_id = candidate["id"].replace("/", "_").replace(":", "_")
    tmp = _TEMP / f"precheck_{vid_id}.mp4"
    try:
        r = requests.get(candidate["url"], stream=True, timeout=90)
        r.raise_for_status()
        with open(tmp, "wb") as f:
            for chunk in r.iter_content(chunk_size=512 * 1024):
                f.write(chunk)
        return tmp
    except Exception as e:
        print(f"  ⚠️ Скачивание {candidate['id']}: {e}")
        if tmp.exists():
            tmp.unlink(missing_ok=True)
        return None

from dotenv import load_dotenv
load_dotenv(Path(__file__).parent.parent.parent / "config" / ".env")

# ── Channel manager (язык стоков из активного канала) ─────────────────────────
try:
    _BOT_DIR = Path(__file__).resolve().parent.parent.parent / "bot"
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
_used_stock_ids  = set()
_used_stock_urls = set()

# Перевод немецких космических терминов → английские поисковые слова
_DE_TO_EN = {
    # Конкретные объекты (высший приоритет)
    "james-webb-teleskop":   "James Webb telescope",
    "james webb teleskop":   "James Webb telescope",
    "james webb":            "James Webb telescope",
    "james-webb":            "James Webb telescope",
    "hubble-teleskop":       "Hubble telescope",
    "hubble teleskop":       "Hubble telescope",
    "james webb space telescope": "James Webb Space Telescope",
    "marsrover":             "Mars rover",
    "mars rover":            "Mars rover",
    "perseverance":          "Mars rover Perseverance",
    "curiosity":             "Mars rover Curiosity",
    "internationale raumstation": "International Space Station",
    "iss":                   "International Space Station",
    # Общие термины
    "galaxien":              "galaxies",
    "galaxie":               "galaxy",
    "universum":             "universe",
    "kosmologie":            "cosmology",
    "kosmisch":              "cosmic",
    "teleskop":              "telescope",
    "sterne":                "stars",
    "stern":                 "star",
    "planeten":              "planets",
    "planet":                "planet",
    "schwarzes loch":        "black hole",
    "schwarze löcher":       "black holes",
    "mikrowellenhintergrund": "cosmic microwave background",
    "astronomie":            "astronomy",
    "astronomen":            "astronomers",
    "astronaut":             "astronaut",
    "raumfahrt":             "spacecraft",
    "raumschiff":            "spacecraft",
    "spektroskopie":         "spectroscopy",
    "dunkle materie":        "dark matter",
    "dunkle energie":        "dark energy",
    "nebel":                 "nebula",
    "raumzeit":              "spacetime",
    "urknall":               "big bang",
    "sternentod":            "stellar death",
    "sternengeburt":         "star formation",
    "supernovae":            "supernova",
    "supernova":             "supernova",
    "neutronenstern":        "neutron star",
    "exoplaneten":           "exoplanets",
    "exoplanet":             "exoplanet",
    "kometenschweif":        "comet",
    "komet":                 "comet",
    "asteroid":              "asteroid",
    "milchstraße":           "Milky Way galaxy",
    "andromeda":             "Andromeda galaxy",
    "inflation":             "cosmic inflation",
    "inflationstheorie":     "cosmic inflation",
    "rote verschiebung":     "redshift",
    "rotverschiebung":       "redshift",
    "gravitationswellen":    "gravitational waves",
    "gravitation":           "gravity space",
    "quantenmechanik":       "quantum physics",
    "relativitätstheorie":   "relativity physics",
    "lichtjahr":             "light year space",
    "lichtjahren":           "light year space",
    "sonnensystem":          "solar system",
    "sonne":                 "sun star",
    "mond":                  "moon",
    "mars":                  "Mars planet",
    "saturn":                "Saturn planet rings",
    "jupiter":               "Jupiter planet",
    "venus":                 "Venus planet",
    "neptun":                "Neptune planet",
    "weltraum":              "outer space",
    "weltall":               "outer space",
    "kosmos":                "cosmos universe",
    "all":                   "universe space",
    "planetensystem":        "solar system planets",
}

# Стоп-слова (не несут смысловой нагрузки)
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
    "known", "called", "known", "two", "three", "one", "billion", "million",
    "year", "years", "time", "first", "new", "old", "large", "small",
    "much", "many", "few", "some", "any", "other", "same", "different",
    "there", "here", "now", "today", "light", "away", "far", "near",
    "long", "high", "deep", "across", "around", "within", "without",
    "because", "since", "until", "though", "although", "however",
    "therefore", "thus", "both", "either", "neither", "whether",
    "despite", "instead", "rather", "quite", "already", "yet", "still",
    "ever", "never", "always", "often", "once", "twice", "again",
    "let", "make", "made", "use", "used", "take", "taken", "give",
    "given", "come", "go", "get", "put", "set", "see", "look", "like",
    "know", "think", "say", "show", "tell", "find", "found", "become",
    "keep", "hold", "turn", "move", "allow", "remain", "appear", "seem",
    "called", "based", "located", "formed", "known", "named", "said",
}

# Ключевые слова ниши — добавляются как контекст если текст слишком общий
_NICHE_KEYWORDS = {
    "cosmos": ["space", "universe", "cosmos", "galaxy", "stars"],
    "science": ["science", "research", "laboratory", "technology"],
    "nature":  ["nature", "earth", "ocean", "atmosphere", "wildlife"],
}

# Слова которые подтверждают что запрос космический
_SPACE_WORDS = {
    "space", "universe", "cosmos", "galaxy", "galaxies", "nebula", "nebulae",
    "star", "stars", "planet", "planets", "solar", "cosmic", "astronomy",
    "astronaut", "spacecraft", "telescope", "orbit", "nasa", "supernova",
    "black hole", "milky", "andromeda", "asteroid", "comet", "mars",
    "saturn", "jupiter", "neptune", "venus", "mercury", "moon", "sun",
    "interstellar", "gravitational", "quasar", "pulsar", "neutron",
    "exoplanet", "hubble", "webb", "deep space", "outer space",
}

# Ротационный пул нейтральных космос-запросов (используется как fallback)
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
    "cosmic rays universe",
    "Saturn rings planet",
    "Mars red planet surface",
    "space shuttle orbit earth",
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
]

# Чёрный список — эти слова не должны попасть в запрос
_BLACKLIST = {
    "food", "restaurant", "cooking", "people", "person",
    "city", "street", "office", "building", "house", "home",
    "fashion", "market", "wood", "factory", "sawmill", "lumber",
    "burger", "pizza", "coffee", "cafe", "shopping", "crowd",
    "car", "truck", "bus", "road", "highway", "traffic",
    "money", "dollar", "bank", "business", "company",
    "music", "song", "dance", "party", "sport", "game",
    "computer", "phone", "laptop", "screen", "keyboard",
    "rhino", "rhinoceros", "snail", "animal", "wildlife",
    "flower", "garden", "tree", "forest", "mountain",
    "ocean", "sea", "river", "lake", "beach",
    "city", "urban", "downtown", "skyline",
}

# Вайтлист для cosmos — только эти английские слова разрешены в запросе
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
    "aurora", "eclipse", "constellation", "comet", "meteorite", "wormhole",
}


def generate_stock_query(segment_text, niche="cosmos", segment_idx=0):
    """
    Генерировать поисковый запрос из текста сегмента без API.
    Для cosmos: всегда содержит космические слова, мусор не проходит.
    """
    text_lower = segment_text.lower()

    # ── Шаг 1: DE→EN перевод (многословные сначала)
    en_parts = []
    remaining = text_lower
    for de_phrase in sorted(_DE_TO_EN.keys(), key=len, reverse=True):
        if de_phrase in remaining:
            en_parts.append(_DE_TO_EN[de_phrase])
            remaining = remaining.replace(de_phrase, " ", 1)

    if en_parts:
        # Строим из переводов, max 2 фразы
        query_words = []
        for part in en_parts[:2]:
            query_words.extend(part.split())
        # Для cosmos — всегда начинаем с "space"
        if niche == "cosmos" and "space" not in query_words:
            query_words = ["space"] + query_words
        query = " ".join(query_words[:6])
        print(f"  🔍 Запрос (DE→EN): '{query}'")
        print(f"  📝 Сегмент: '{segment_text[:60]}'")
        return query

    # ── Шаг 2: Английский текст — только слова из вайтлиста ниши
    text = re.sub(r"[^\w\s]", " ", text_lower)
    words = text.split()

    if niche == "cosmos":
        # Только слова из вайтлиста — гарантия релевантности
        whitelist_hits = [w for w in words if w in _COSMOS_WHITELIST]
        seen = set()
        selected = []
        for w in whitelist_hits:
            if w not in seen:
                seen.add(w)
                selected.append(w)
            if len(selected) >= 4:
                break
        # Если вайтлист ничего не дал — берём из ротационного пула
        if not selected or selected == ["space"]:
            query = _COSMOS_FALLBACK_POOL[segment_idx % len(_COSMOS_FALLBACK_POOL)]
            print(f"  🔍 Запрос (fallback #{segment_idx}): '{query}'")
            print(f"  📝 Сегмент: '{segment_text[:60]}'")
            return query
        # Всегда добавляем "space" в начало
        if "space" not in selected:
            selected = ["space"] + selected
        query = " ".join(selected[:5])
    else:
        # Другие ниши — общая логика
        keywords = [
            w for w in words
            if w not in _STOPWORDS and w not in _BLACKLIST
            and len(w) >= 3 and not w.isdigit()
        ]
        seen = set()
        selected = []
        for w in keywords:
            if w not in seen:
                seen.add(w)
                selected.append(w)
        selected = selected[:5]
        niche_fill = _NICHE_KEYWORDS.get(niche, [])
        for w in niche_fill:
            if w not in selected and len(selected) < 5:
                selected.append(w)
        query = " ".join(selected) if selected else "cosmos space universe"

    print(f"  🔍 Запрос: '{query}'")
    print(f"  📝 Сегмент: '{segment_text[:60]}'")
    return query


def build_stock_query(segment_text, prompt_text="", niche="cosmos", segment_idx=0):
    return generate_stock_query(segment_text, niche, segment_idx)


def find_stock_video(segment_text, prompt_text="",
                     niche="cosmos", min_duration=10, segment_idx=0):
    """
    Найти сток с BLIP-валидацией каждого кандидата.

    Логика:
      1. Генерируем запрос из текста сегмента
      2. Перебираем кандидатов Pixabay → Pexels поштучно
      3. Каждого скачиваем во временный файл
      4. Извлекаем кадр → BLIP проверяет: космос? не мусор?
      5. Если BLIP ОК → принимаем (возвращаем с local_path)
      6. Если BLIP отклонил → пропускаем этот ID, пробуем следующий
      7. При ≥ 3 отказах на один запрос → переходим к следующему
         запросу из _COSMOS_FALLBACK_POOL
    """
    global _used_stock_ids, _used_stock_urls

    query = build_stock_query(segment_text, niche=niche, segment_idx=segment_idx)

    # Список запросов: основной + fallback-ротация
    fallbacks = [q for q in _COSMOS_FALLBACK_POOL if q != query]
    queries_to_try = [query] + fallbacks

    blip_running = _blip_alive()
    if not blip_running:
        print("  ⚠️  BLIP-сервер не отвечает — валидация кандидатов отключена")

    with _stock_lock:
        for attempt_query in queries_to_try:
            rejections = 0

            # Итерируем кандидатов из обоих источников
            for candidate in _iter_pixabay(attempt_query, min_duration,
                                           _used_stock_ids, _used_stock_urls):
                # Помечаем ID как занятый сразу (защита от дублей в параллельных потоках)
                _used_stock_ids.add(candidate["id"])
                _used_stock_urls.add(candidate["url"])

                result = _check_candidate(candidate, blip_running)
                if result:
                    print(f"  ✅ Pixabay [{result['id']}]: '{attempt_query}'")
                    return result

                rejections += 1
                print(f"  ✗ BLIP отклонил [{candidate['id']}] "
                      f"(отказов: {rejections}, запрос: '{attempt_query}')")
                if rejections >= 3:
                    print(f"  → Смена запроса после {rejections} отказов")
                    break

            else:
                # Pixabay иссяк — пробуем Pexels с тем же запросом
                for candidate in _iter_pexels(attempt_query, min_duration,
                                              _used_stock_ids, _used_stock_urls):
                    _used_stock_ids.add(candidate["id"])
                    _used_stock_urls.add(candidate["url"])

                    result = _check_candidate(candidate, blip_running)
                    if result:
                        print(f"  ✅ Pexels [{result['id']}]: '{attempt_query}'")
                        return result

                    rejections += 1
                    print(f"  ✗ BLIP отклонил [{candidate['id']}] "
                          f"(отказов: {rejections})")
                    if rejections >= 3:
                        break
                continue  # следующий запрос

            # Если не break из внутреннего цикла — пробуем Pexels
            for candidate in _iter_pexels(attempt_query, min_duration,
                                          _used_stock_ids, _used_stock_urls):
                _used_stock_ids.add(candidate["id"])
                _used_stock_urls.add(candidate["url"])

                result = _check_candidate(candidate, blip_running)
                if result:
                    print(f"  ✅ Pexels [{result['id']}]: '{attempt_query}'")
                    return result

                rejections += 1
                print(f"  ✗ BLIP отклонил [{candidate['id']}] "
                      f"(отказов: {rejections})")
                if rejections >= 5:
                    break

        # Последний резерв: NASA (там точно космос, без BLIP-проверки)
        result = _search_nasa(query, _used_stock_ids, _used_stock_urls)
        if result:
            _used_stock_ids.add(result["id"])
            _used_stock_urls.add(result["url"])
            local = _download_candidate(result)
            if local:
                result["local_path"] = str(local)
            print(f"  ✅ NASA [{result['id']}]: '{query}'")
            return result

    print(f"  ❌ Все стоки исчерпаны для: '{query}'")
    return None


def _check_candidate(candidate: dict, blip_running: bool) -> dict | None:
    """Скачать кандидата, проверить BLIP. Вернуть dict с local_path или None."""
    local = _download_candidate(candidate)
    if not local:
        return None

    if not blip_running:
        candidate["local_path"] = str(local)
        return candidate

    frame = _extract_frame_for_check(local)
    ok    = _blip_is_cosmos(frame)

    if frame and frame.exists():
        frame.unlink(missing_ok=True)

    if ok:
        candidate["local_path"] = str(local)
        return candidate
    else:
        local.unlink(missing_ok=True)
        return None


# ── Генераторы кандидатов (без скачивания) ────────────────────────────────────

def _iter_pexels(query, min_duration, used_ids, used_urls):
    """Генератор кандидатов из Pexels (без скачивания)."""
    api_key = os.getenv("PEXELS_API_KEY", "")
    if not api_key:
        return
    import requests
    headers = {"Authorization": api_key}
    for page in range(1, 8):
        params = {
            "query": query,
            "per_page": 20,
            "page": page,
            "min_duration": min_duration,
            "orientation": "landscape",
        }
        try:
            r = requests.get(
                "https://api.pexels.com/videos/search",
                headers=headers, params=params, timeout=10
            )
            videos = r.json().get("videos", [])
            if not videos:
                return
            for video in videos:
                vid_id = f"pexels_{video['id']}"
                if vid_id in used_ids:
                    continue
                hd_files = [
                    f for f in video.get("video_files", [])
                    if f.get("width", 0) >= 1280 and f.get("height", 0) >= 720
                ]
                if not hd_files:
                    continue
                best = max(hd_files, key=lambda x: x.get("width", 0))
                if best["link"] in used_urls:
                    continue
                yield {
                    "id":       vid_id,
                    "url":      best["link"],
                    "source":   "pexels",
                    "duration": video.get("duration", 0),
                    "width":    best.get("width", 0),
                    "query":    query,
                    "page":     page,
                }
        except Exception as e:
            print(f"  ⚠️ Pexels стр.{page}: {e}")
            return


def _iter_pixabay(query, min_duration, used_ids, used_urls):
    """Генератор кандидатов из Pixabay (без скачивания)."""
    api_key = os.getenv("PIXABAY_API_KEY", "")
    if not api_key:
        return
    import requests
    for page in range(1, 8):
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
            r = requests.get(
                "https://pixabay.com/api/videos/",
                params=params, timeout=10
            )
            hits = r.json().get("hits", [])
            if not hits:
                return
            for video in hits:
                vid_id = f"pixabay_{video['id']}"
                if vid_id in used_ids:
                    continue
                dur = video.get("duration", 0)
                if dur < min_duration:
                    continue
                for quality in ["large", "medium", "small"]:
                    v   = video.get("videos", {}).get(quality, {})
                    url = v.get("url", "")
                    if url and url not in used_urls:
                        yield {
                            "id":       vid_id,
                            "url":      url,
                            "source":   "pixabay",
                            "duration": dur,
                            "width":    v.get("width", 0),
                            "query":    query,
                            "page":     page,
                        }
                        break
        except Exception as e:
            print(f"  ⚠️ Pixabay стр.{page}: {e}")
            return


# Обёртки для обратной совместимости (возвращают первый результат)
def _search_pexels(query, min_duration, used_ids, used_urls):
    return next(_iter_pexels(query, min_duration, used_ids, used_urls), None)


def _search_pixabay(query, min_duration, used_ids, used_urls):
    return next(_iter_pixabay(query, min_duration, used_ids, used_urls), None)


def _search_nasa(query, used_ids, used_urls):
    import requests
    try:
        r = requests.get(
            "https://images-api.nasa.gov/search",
            params={"q": query, "media_type": "video"},
            timeout=10
        )
        items = r.json().get("collection", {}).get("items", [])
        for item in items[:20]:
            nasa_id = item.get("data", [{}])[0].get("nasa_id", "")
            vid_id = f"nasa_{nasa_id}"
            if vid_id in used_ids:
                continue
            for link in item.get("links", []):
                if link.get("render") == "video":
                    url = link["href"]
                    if url not in used_urls:
                        return {
                            "id": vid_id,
                            "url": url,
                            "source": "nasa",
                            "duration": 15,
                            "width": 1920,
                            "query": query
                        }
    except Exception as e:
        print(f"  ⚠️ NASA: {e}")
    return None


def download_and_verify(stock, output_path):
    import requests
    try:
        r = requests.get(stock["url"], stream=True, timeout=30)
        with open(output_path, "wb") as f:
            for chunk in r.iter_content(chunk_size=8192):
                f.write(chunk)
        cmd = [
            "ffprobe", "-v", "quiet",
            "-print_format", "json",
            "-show_format", "-show_streams",
            str(output_path)
        ]
        result = subprocess.run(cmd, capture_output=True, text=True)
        info = json.loads(result.stdout)
        dur = float(info["format"].get("duration", 0))
        if dur < 8:
            print(f"  ⚠️ Слишком короткое: {dur:.1f}s")
            os.remove(output_path)
            return False
        for stream in info.get("streams", []):
            if stream.get("codec_type") == "video":
                w = stream.get("width", 0)
                h = stream.get("height", 0)
                if w < 1280 or h < 720:
                    print(f"  ⚠️ Низкое разрешение: {w}x{h}")
                    os.remove(output_path)
                    return False
        return True
    except Exception as e:
        print(f"  ❌ Скачивание: {e}")
        return False


def load_used_stocks(session):
    global _used_stock_ids, _used_stock_urls
    path = Path(f"data/transcripts/{session}/used_stocks.json")
    if path.exists():
        with open(path) as f:
            data = json.load(f)
        _used_stock_ids  = set(data.get("ids",  []))
        _used_stock_urls = set(data.get("urls", []))
        print(f"  📋 Загружено {len(_used_stock_ids)} использованных стоков")


def save_used_stocks(session):
    path = Path(f"data/transcripts/{session}/used_stocks.json")
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w") as f:
        json.dump({
            "ids":  list(_used_stock_ids),
            "urls": list(_used_stock_urls)
        }, f, indent=2)
    print(f"  💾 Сохранено {len(_used_stock_ids)} стоков")


# Алиасы для обратной совместимости
def download_stock_video(stock, output_path):
    return download_and_verify(stock, output_path)


def verify_duration(video_path, min_duration=10):
    try:
        cmd = [
            "ffprobe", "-v", "quiet",
            "-print_format", "json",
            "-show_format", str(video_path),
        ]
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=15)
        if result.returncode == 0:
            duration = float(json.loads(result.stdout)["format"]["duration"])
            return duration >= min_duration
    except Exception:
        pass
    return False
