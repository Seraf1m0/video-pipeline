"""
tag_library.py — присвоить структурированные теги всем клипам в library.json

Добавляет к каждому клипу:
  - entry["category"]  — основная категория (строка)
  - entry["tags"]      — список тегов (именованные объекты, стиль, etc.)

Запуск:
    python tools/tag_library.py --channel channel_001_cosmos_de
    python tools/tag_library.py --channel channel_001_cosmos_de --dry-run
"""
import argparse
import json
import shutil
import sys
from pathlib import Path

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

_utils_dir = Path(__file__).resolve().parent.parent / "agents" / "utils"
if str(_utils_dir) not in sys.path:
    sys.path.insert(0, str(_utils_dir))
from paths import get_library_json

# ─── Taxonomy ────────────────────────────────────────────────────────────────

# Порядок важен: более специфичные правила первые
CATEGORY_RULES: list[tuple[str, list[str]]] = [
    ("black_hole",   ["black hole", "accretion disk", "event horizon", "singularity", "schwarzschild"]),
    ("supernova",    ["supernova", "supernova remnant", "shockwave expanding", "stellar explosion"]),
    ("solar",        ["solar flare", "sun corona", "solar corona", "solar wind", "heliosphere",
                      "sun surface", "solar prominence", "chromosphere", "magnetosphere",
                      "sun's corona", "sun's surface", "sun's radiant", "sun's warmth",
                      "solar storm", "sunspot", "solar disk"]),
    ("telescope",    ["telescope", "observatory", "radio dish", "radio telescope", "hubble space",
                      "james webb", "jwst", "euclid", "chandra", "spitzer", "radio array",
                      "eso vlt", "meerkat", "vlbi"]),
    ("astronaut",    ["astronaut", "spacewalk", "eva ", "space suit", "cosmonaut",
                      "floating in space", "iss interior", "space station interior",
                      "crew module", "mission control"]),
    ("scientist",    ["scientist", "engineer", "clean room", "cleanroom", "lab coat",
                      "computer screen", "data analysis", "control room", "research team",
                      "mission control", "researchers", "technician", "astrophysicist"]),
    ("spacecraft",   ["rocket", "spacecraft", "satellite", "probe", "capsule", "lander",
                      "rover", "launch pad", "launch vehicle", "sls ", "artemis", "orion capsule",
                      "voyager", "cassini", "new horizons", "falcon ", "starship", "soyuz",
                      "dragon capsule", "atlas rocket", "delta rocket", "iss ", "space station",
                      "international space station"]),
    ("earth",        ["earth from space", "blue marble", "earth orbit", "aurora borealis",
                      "aurora australis", "atmosphere edge", "limb of earth", "clouds from space",
                      "continents from space", "earth rising", "earth's curve", "earth's edge",
                      "earth's horizon", "earth silhouette", "earth curvature", "view of earth",
                      "planet earth", "earth's atmosphere", "earth glow", "earth below",
                      "sunlight piercing", "sunlight breaks", "sliver of sunlight",
                      "earth's surface", "earth background", "earth below", "blue gradient",
                      "blue backdrop", "blue tones deep black"]),
    ("solar_system", ["mars ", "mars surface", "red planet", "saturn ", "saturn rings",
                      "jupiter ", "great red spot", "moon surface", "lunar surface",
                      "asteroid", "comet", "mercury ", "venus ", "neptune ", "uranus ",
                      "pluto ", "lunar orbit", "planetary orbit", "solar system"]),
    ("black_hole",   ["black hole"]),  # second pass catch-all
    ("galaxy",       ["galaxy", "spiral galaxy", "elliptical galaxy", "milky way",
                      "galactic plane", "galactic center", "andromeda", "galaxy cluster",
                      "lenticular galaxy", "irregular galaxy"]),
    ("nebula",       ["nebula", "gas cloud", "gas pillar", "stellar nursery",
                      "planetary nebula", "emission nebula", "reflection nebula",
                      "protostellar", "carina", "orion nebula", "pillars of creation"]),
    ("deep_space",   ["cosmic web", "dark matter", "large scale structure", "filament",
                      "void ", "deep field", "deep space", "starfield", "star field",
                      "distant stars", "dark void", "star cluster", "globular cluster",
                      "starry", "night sky", "stars scattered", "stars twinkling",
                      "cosmic ", "celestial", "interstellar", "galactic", "universe",
                      "dark expanse", "space expanse", "infinite space", "vast space",
                      "cold blue", "electric violet", "pale star", "pulsating star",
                      "light beam", "energy beam", "plasma beam", "cosmic cloud",
                      "dark cloud", "gas cloud", "fiery ", "nebulous"]),
    ("earth",        ["earth's curve", "earth's edge", "earth's horizon", "earth silhouette",
                      "earth curvature", "sunlight.*earth", "view of earth", "planet earth"]),
    ("animation",    ["cgi", "animation", "animated", "simulation", "visualization",
                      "render ", "3d model", "illustrated", "infographic", "diagram",
                      "futuristic interface", "holographic", "data overlay", "interface overlay"]),
]

# Именованные теги — могут быть у клипа вместе с любой категорией
NAMED_TAG_RULES: list[tuple[str, list[str]]] = [
    ("jwst",     ["james webb", "jwst", "webb telescope", "webb space"]),
    ("hubble",   ["hubble", "hst "]),
    ("euclid",   ["euclid"]),
    ("chandra",  ["chandra"]),
    ("spitzer",  ["spitzer"]),
    ("iss",      ["international space station", "iss ", " iss", "space station module"]),
    ("artemis",  ["artemis"]),
    ("sls",      ["sls rocket", "sls launch", " sls "]),
    ("orion",    ["orion capsule", "orion spacecraft", "orion crew"]),
    ("voyager",  ["voyager"]),
    ("cassini",  ["cassini"]),
    ("perseverance", ["perseverance"]),
    ("curiosity",    ["curiosity rover"]),
    ("mars",     ["mars ", "martian surface", "red planet", "mars orbit"]),
    ("moon",     ["moon", "lunar", "luna "]),
    ("saturn",   ["saturn"]),
    ("jupiter",  ["jupiter", "great red spot"]),
    ("aurora",   ["aurora borealis", "aurora australis", "northern lights", "southern lights"]),
    ("real_photo", ["real image", "real photo", "actual photo", "true color", "real telescope",
                    "hubble image", "jwst image", "esa image", "nasa image", "actual footage"]),
    ("cgi",      ["cgi", " cgi", "animation", "animated", "3d render", "simulation",
                  "visualization", "illustrated", "rendered"]),
]


GARBAGE_PATTERNS = [
    "you've hit your limit", "you're out of extra usage",
    "not logged in", "error:", "quota",
]

HUMAN_PATTERNS = [
    "woman's face", "man's face", "woman's eye", "man's eye",
    "person's face", "human face", "portrait of", "close-up face",
    "man typing", "woman typing", "hands typing",
]


def assign_tags(keywords: str) -> tuple[str, list[str]]:
    """
    Присвоить категорию и теги на основе ключевых слов.
    Возвращает (category, tags).
    """
    kw = keywords.lower()

    # Мусорные/нерелевантные клипы
    if any(p in kw for p in GARBAGE_PATTERNS):
        return "garbage", ["garbage"]
    if any(p in kw for p in HUMAN_PATTERNS):
        return "human", ["human"]

    # Категория — первое совпавшее правило
    category = "other"
    for cat, patterns in CATEGORY_RULES:
        if any(p in kw for p in patterns):
            category = cat
            break

    # Именованные теги — все совпавшие
    tags: list[str] = []
    for tag, patterns in NAMED_TAG_RULES:
        if any(p in kw for p in patterns):
            tags.append(tag)

    return category, tags


def extract_tags_from_query(visual_query: str) -> list[str]:
    """
    Из visual_query (от Haiku) извлечь теги для boost в clip_selector.
    Публичная функция — используется в clip_selector.py.
    """
    _, tags = assign_tags(visual_query)
    # Также проверяем категорию — если query явно указывает на категорию, добавляем её
    kw = visual_query.lower()
    cat_tags = []
    for cat, patterns in CATEGORY_RULES:
        if any(p in kw for p in patterns):
            cat_tags.append(cat)
            break
    return list(set(tags + cat_tags))


def _get_combined_keywords(entry: dict) -> str:
    """Combine all keyword fields (EN/DE/FR/ES) into one lowercase string."""
    parts = [
        entry.get("keywords", ""),
        entry.get("keywords_de", ""),
        entry.get("keywords_fr", ""),
        entry.get("keywords_es", ""),
    ]
    combined = " ".join(p for p in parts if p)
    # Wrap with spaces so fragment matching works at word boundaries
    return " " + combined.lower() + " "


def run(channel_id: str, dry_run: bool = False):
    lib_path = get_library_json(channel_id)
    print(f"📖 Loading library: {lib_path}")
    with open(lib_path, encoding="utf-8") as f:
        lib = json.load(f)
    clips: dict = lib["clips"]
    total = len(clips)
    print(f"   Total clips: {total}")

    if not dry_run:
        bak_path = Path(str(lib_path) + ".bak_pretags")
        if not bak_path.exists():
            shutil.copy2(lib_path, bak_path)
            print(f"💾 Backup saved: {bak_path}")
        else:
            print(f"   Backup already exists: {bak_path}")

    from collections import Counter
    cat_counter: Counter = Counter()
    tag_counter: Counter = Counter()
    clips_with_tags = 0
    processed = 0

    for clip_id, entry in clips.items():
        # Process ALL clips (including unindexed), using all keyword fields
        text = _get_combined_keywords(entry)

        if not text.strip():
            category = "other"
            tags: list[str] = []
        else:
            category, tags = assign_tags(text)

        cat_counter[category] += 1
        for t in tags:
            tag_counter[t] += 1
        if tags:
            clips_with_tags += 1

        if not dry_run:
            entry["category"] = category
            entry["tags"] = tags
        processed += 1

    if not dry_run:
        with open(lib_path, "w", encoding="utf-8") as f:
            json.dump(lib, f, ensure_ascii=False, indent=2)
        print(f"\n✅ Tags written for all {processed} clips → {lib_path}")
    else:
        print(f"\n[DRY RUN] Would update {processed} clips")

    print("\n📊 Category distribution:")
    for cat, cnt in cat_counter.most_common():
        bar = "█" * max(1, cnt // 30)
        print(f"  {cat:<20} {cnt:>5}  {bar}")

    print(f"\n🏷  Named tags ({clips_with_tags} clips have at least one named tag):")
    for tag, cnt in tag_counter.most_common():
        print(f"  {tag:<20} {cnt:>5}")

    print(f"\n  Total clips:         {total}")
    print(f"  With named tags:     {clips_with_tags}")
    print(f"  Without named tags:  {total - clips_with_tags}")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Tag all clips in library.json")
    parser.add_argument("--channel", default="channel_001_cosmos_de",
                        help="Channel ID (default: channel_001_cosmos_de)")
    parser.add_argument("--dry-run", action="store_true",
                        help="Preview stats without modifying library.json")
    args = parser.parse_args()
    run(args.channel, dry_run=args.dry_run)
