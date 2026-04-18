"""
gemini_indexer.py — Индексатор библиотеки через Gemini 2.0 Flash API.

Архитектура:
  ffmpeg → 3 кадра (0.5s, 2.5s, 4.5s) → base64 JPEG → Gemini 2.0 Flash → keywords
  asyncio + Semaphore(30) — 30 параллельных запросов
  Checkpoint каждые 100 клипов

Режимы:
  --scan          FFprobe-скан → чистый library.json (indexed=False)
  --test N        Scan + тест первых N клипов с подробным выводом (дефолт: 20)
  --full          Scan + полный индекс всех клипов
  --resume        Продолжить без повторного scan

Запуск:
  py agents/library/gemini_indexer.py --scan
  py agents/library/gemini_indexer.py --test 20
  py agents/library/gemini_indexer.py --full
  py agents/library/gemini_indexer.py --resume

После индекса — перестроить CLIP embeddings:
  py agents/library/visual_embedder.py --channel channel_001_cosmos_de
"""

import argparse
import asyncio
import base64
import json
import shutil
import subprocess
import sys
import tempfile
import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone
from pathlib import Path

from dotenv import load_dotenv
import os

# ── Пути ──────────────────────────────────────────────────────────────────────

_root = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(_root / "agents" / "utils"))
load_dotenv(_root / "config" / ".env")

from paths import get_library_dir, get_clips_dir, get_library_json  # noqa: E402

# ── Конфиг ────────────────────────────────────────────────────────────────────

GEMINI_MODEL    = "gemini-2.5-flash-lite"
CONCURRENCY     = 15           # параллельных Gemini запросов
SAVE_EVERY      = 100          # checkpoint каждые N клипов
FFMPEG_WORKERS  = 8            # параллельных ffmpeg при извлечении кадров
FRAME_POSITIONS = [0.5, 2.5, 4.5]  # секунды (3 кадра)
FRAME_SIZE      = 512          # px (ширина), высота пропорционально
BLACK_THRESH    = 5.0
BLACK_MAX       = 30
FROZEN_DIFF     = 0.5

# Паттерны визуальных дескрипторов без имени объекта → клип нужно переиндексировать
_POOR_PATTERNS = [
    "hexagonal mirror", "gold hexagonal", "rectangular sunshield",
    "cylindrical telescope", "silver cylinder", "solar array, space",
    "space telescope, stars", "telescope, space, stars",
]

# Промпт повышенной точности для smart re-index
SMART_PROMPT = """\
You are an expert space archivist. Your task: identify PRECISELY what is in this clip.

TELESCOPE VISUAL IDENTIFICATION — mandatory, never skip:
- JWST: 18 gold hexagonal mirror segments arranged in honeycomb, 5-layer diamond-shaped sunshield → always write "JWST" or "James Webb Space Telescope"
- Euclid: compact white/silver rectangular body, flat rectangular sunshield panels on sides, small cylindrical telescope tube → always write "Euclid Space Telescope"
- Hubble: large silver cylinder, two blue rectangular solar wings, round gold aperture door → always write "Hubble Space Telescope"
- ISS: large truss with many solar panels, multiple cylindrical modules → "International Space Station ISS"
- Soyuz: three-module rocket with four solar panels → "Soyuz spacecraft"

TELESCOPE IMAGE ATTRIBUTION — if the clip shows a space image (nebula, galaxy, star field):
- JWST images: NIR color palette, orange/red/teal tones, extreme resolution, labeled "NIRCam", "MIRI" → add "JWST image" or "photographed by JWST"
- Euclid images: wide-field galaxy survey, many thousands of galaxies, labeled "Euclid" → add "from Euclid survey"
- Hubble images: visible-light palette, labeled "HST", "Hubble" → add "Hubble image"
- If label/watermark visible → read it verbatim

FORMAT (nothing else):
KEYWORDS: <10-15 words, comma-separated, specific — NO generic filler>
VALID: yes
TYPE: <real | CGI | illustration | archival | mixed>
MOTION: <static | pan-left | pan-right | zoom-in | zoom-out | orbit | tracking | timelapse>

VALID is always "yes" unless the clip is: completely black, or shows zero connection to space/science.

EXAMPLES:
KEYWORDS: JWST gold hexagonal mirror, 18 segments, honeycomb pattern, five-layer sunshield, deployed configuration
VALID: yes | TYPE: CGI | MOTION: static

KEYWORDS: Euclid Space Telescope, white rectangular body, flat sunshield panels, solar array, dark space background
VALID: yes | TYPE: CGI | MOTION: orbit

KEYWORDS: Hubble Space Telescope, silver cylinder, two rectangular solar wings, gold aperture door, Earth orbit
VALID: yes | TYPE: CGI | MOTION: orbit

KEYWORDS: Andromeda galaxy M31, spiral arms, dust lanes, from Euclid survey, wide-field image
VALID: yes | TYPE: real | MOTION: zoom-in

KEYWORDS: Carina Nebula pillars, JWST NIRCam image, orange teal color palette, star formation region
VALID: yes | TYPE: real | MOTION: static
"""

GEMINI_PROMPT = """\
ROLE: You are a senior space imagery archivist. Your goal is to transform visual clip content into hyper-dense, technical metadata optimized for high-dimensional vector search (Gemini Embedding 2).

You are given 3 labeled keyframes: START, MIDDLE, END of a ~5s clip. Analyze ALL THREE frames equally — identify what changes between them and what stays constant. If the clip transitions between subjects, describe the PRIMARY subject (dominant across most frames). Never describe only the last frame.

Generate a description using this exact formula:
[ENTITY/SUBJECT]: [TECHNICAL DESCRIPTION]. [ENVIRONMENT & LIGHTING]. [CAMERA & AESTHETIC].

EXPANSION GUIDELINES:
- Light Physics: Use terms like "specular highlights," "collimated light," "subsurface scattering," "Rayleigh scattering," "high dynamic range (HDR)."
- Materials & Textures: Describe surfaces precisely: "anodized aluminum," "multi-layer insulation (MLI) gold foil," "porous regolith," "viscous fluid dynamics."
- Astronomical Accuracy: Identify features like "accretion disks," "oblate spheroids," "plasma filaments," "coronal mass ejections," "terminator lines."
- Cinematography: Describe the lens and sensor: "wide-angle distortion-free," "telephoto compression," "sensor noise/grain for archival look," "60fps fluid motion."

SPACECRAFT IDENTIFICATION (mandatory — use your full visual knowledge):
- JWST: 18 gold hexagonal beryllium mirror segments in honeycomb pattern, 5-layer kapton diamond-shaped sunshield → always name "James Webb Space Telescope JWST"
- Hubble: large silver cylinder, two rectangular blue silicon solar wings, gold aperture door → "Hubble Space Telescope HST"
- Euclid: compact white/silver rectangular body, flat rectangular sunshield panels, small telescope tube → "Euclid Space Telescope ESA"
- ISS: massive truss with 8 photovoltaic solar array wings, multiple cylindrical pressurized modules → "International Space Station ISS"
- If image was TAKEN BY a telescope: identify by color palette/watermark and add "imaged by JWST NIRCam", "Hubble ACS image", etc.
- Read all visible text, logos, watermarks verbatim and include them.

OUTPUT FORMAT (one line per field, nothing else):
DESCRIPTION: <[ENTITY]: [technical]. [environment]. [camera].>
TYPE: <Real | CGI | Photorealistic Simulation | Archival | Illustration | Mixed>
MOTION: <static | pan-left | pan-right | zoom-in | zoom-out | orbit | tracking | timelapse | rotation>
VALID: <yes | no>

VALID is "no" ONLY for: completely black/white frames, human faces in non-space everyday settings, objects with zero connection to space or science.

EXAMPLE OUTPUT:

DESCRIPTION: Saturnian Ring System: Detailed view of the A and B rings showing individual orbital ice particle density gradients across a 270,000 km span. Sharp, high-contrast collimated solar illumination casting distinct shadows across the Cassini Division; specular highlights on icy ring particles create a crystalline texture. Photorealistic CGI with telephoto compression emphasizing the oblate spheroid geometry of Saturn against pitch-black vacuum.
TYPE: CGI
MOTION: static
VALID: yes

DESCRIPTION: James Webb Space Telescope JWST primary mirror: All 18 gold-coated beryllium hexagonal segments visible in fully deployed honeycomb configuration, actuator mechanisms at each segment junction catching specular highlights from collimated studio lighting. Five-layer kapton sunshield unfurled below, with MLI gold foil visible on spacecraft bus; pitch-black vacuum background. Wide-angle distortion-free lens, photorealistic CGI render with high dynamic range emphasizing the 6.5m aperture scale.
TYPE: CGI
MOTION: rotation
VALID: yes
"""

# ── Gemini client ─────────────────────────────────────────────────────────────

_gemini_client = None


def _get_client():
    global _gemini_client
    if _gemini_client is None:
        from google import genai
        api_key = os.environ.get("GEMINI_API_KEY")
        if not api_key:
            raise RuntimeError("GEMINI_API_KEY не найден в config/.env")
        _gemini_client = genai.Client(api_key=api_key)
    return _gemini_client


# ── Library I/O ────────────────────────────────────────────────────────────────

def _load_lib(lib_json: Path) -> dict:
    if lib_json.exists():
        try:
            with open(lib_json, encoding="utf-8") as f:
                return json.load(f)
        except Exception:
            pass
    return {"total": 0, "indexed_at": "", "clips": {}, "photos": {}}


def _save_lib(lib: dict, lib_json: Path) -> None:
    lib["indexed_at"] = datetime.now(timezone.utc).isoformat()
    lib.setdefault("photos", {})
    lib["total"] = len(lib["clips"]) + len(lib["photos"])
    tmp = lib_json.with_suffix(".tmp")
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(lib, f, ensure_ascii=False, indent=2)
    tmp.replace(lib_json)


# ── FFprobe ────────────────────────────────────────────────────────────────────

def _ffprobe(path: Path) -> dict | None:
    cmd = [
        "ffprobe", "-v", "quiet", "-print_format", "json",
        "-show_streams", "-show_format", str(path),
    ]
    try:
        r = subprocess.run(cmd, capture_output=True, timeout=30)
        if r.returncode != 0:
            return None
        data = json.loads(r.stdout.decode("utf-8", errors="replace"))
        vs = next((s for s in data.get("streams", []) if s.get("codec_type") == "video"), None)
        if not vs:
            return None
        dur = float(data.get("format", {}).get("duration", 0) or 0)
        w, h = int(vs.get("width", 0)), int(vs.get("height", 0))
        fps_raw = vs.get("r_frame_rate", "25/1")
        try:
            a, b = fps_raw.split("/")
            fps = round(float(a) / float(b), 3)
        except Exception:
            fps = 25.0
        return {"duration": round(dur, 3), "width": w, "height": h, "fps": fps}
    except Exception:
        return None


# ── Frame extraction ───────────────────────────────────────────────────────────

def _extract_frame_bytes(clip_path: Path, ss: float) -> bytes | None:
    """Извлечь один кадр → JPEG bytes (in-memory, без файла)."""
    r = subprocess.run(
        [
            "ffmpeg", "-y", "-ss", str(ss), "-i", str(clip_path),
            "-vframes", "1",
            "-vf", f"scale={FRAME_SIZE}:-2",
            "-f", "image2pipe", "-vcodec", "mjpeg", "-q:v", "4",
            "-loglevel", "error", "pipe:1",
        ],
        capture_output=True,
        timeout=30,
    )
    if r.returncode == 0 and r.stdout:
        return r.stdout
    return None


def extract_frames_bytes(clip_path: Path) -> list[bytes]:
    """Параллельно извлечь 3 кадра → список JPEG bytes."""
    results: dict[float, bytes | None] = {}

    def _extract(ss: float):
        results[ss] = _extract_frame_bytes(clip_path, ss)

    with ThreadPoolExecutor(max_workers=3) as ex:
        futs = [ex.submit(_extract, ss) for ss in FRAME_POSITIONS]
        for f in futs:
            f.result()

    return [b for ss in FRAME_POSITIONS for b in [results.get(ss)] if b]


def extract_frames_smart(clip_path: Path, duration: float) -> list[bytes]:
    """Извлечь 2 кадра на 33% и 67% длительности клипа."""
    dur = max(duration, 1.0)
    positions = [round(dur * 0.33, 2), round(dur * 0.67, 2)]
    results: dict[float, bytes | None] = {}

    def _extract(ss: float):
        results[ss] = _extract_frame_bytes(clip_path, ss)

    with ThreadPoolExecutor(max_workers=2) as ex:
        futs = [ex.submit(_extract, ss) for ss in positions]
        for f in futs:
            f.result()

    return [b for ss in positions for b in [results.get(ss)] if b]


def _needs_reindex(keywords: str) -> bool:
    """Возвращает True если keywords недостаточно точные → нужно переиндексировать."""
    if not keywords:
        return True
    kw_lower = keywords.lower()
    base = kw_lower.split("|")[0]
    terms = [t.strip() for t in base.split(",") if t.strip()]
    if len(terms) < 4:
        return True
    # Визуальные дескрипторы без имени телескопа
    for pat in _POOR_PATTERNS:
        if pat in kw_lower:
            named = any(name in kw_lower for name in ["jwst", "james webb", "hubble", "euclid", "iss ", "soyuz", "dragon", "artemis"])
            if not named:
                return True
    return False


# ── Quality checks ─────────────────────────────────────────────────────────────

def _is_black_bytes(jpeg: bytes) -> bool:
    """Быстрая проверка на чёрный экран по средней яркости JPEG."""
    try:
        from PIL import Image
        import io
        import numpy as np
        img = Image.open(io.BytesIO(jpeg)).convert("L")
        arr = np.array(img).astype(float)
        return arr.mean() < BLACK_THRESH and arr.max() < BLACK_MAX
    except Exception:
        return False


def _is_frozen_bytes(frames: list[bytes]) -> bool:
    """Проверка на статичный (frozen) клип по разнице первого и последнего кадра."""
    if len(frames) < 2:
        return False
    try:
        from PIL import Image
        import io
        import numpy as np
        img0 = np.array(Image.open(io.BytesIO(frames[0])).convert("L"), dtype=float)
        img1 = np.array(Image.open(io.BytesIO(frames[-1])).convert("L"), dtype=float)
        return float(abs(img0 - img1).mean()) < FROZEN_DIFF
    except Exception:
        return False


# ── Gemini call ────────────────────────────────────────────────────────────────

def _parse_gemini_response(text: str) -> tuple[str, bool]:
    """
    Парсит ответ Gemini нового формата (PRIMARY_SUBJECT / DETAILED_DESCRIPTION / KEYWORDS / ...).
    Возвращает (keywords_str, valid_bool).

    keywords_str формат для library.json:
      "<KEYWORDS> | <PRIMARY_SUBJECT> | <DETAILED_DESCRIPTION> | TYPE:<type> | MOTION:<motion>"
    Это позволяет _clip_embed_text() использовать весь текст для эмбеддинга.
    """
    description = ""
    clip_type   = ""
    motion      = ""
    valid       = True

    for line in text.strip().splitlines():
        line = line.strip()
        if line.startswith("DESCRIPTION:"):
            description = line[len("DESCRIPTION:"):].strip()
        elif line.startswith("TYPE:"):
            clip_type = line[len("TYPE:"):].strip()
        elif line.startswith("MOTION:"):
            motion = line[len("MOTION:"):].strip()
        elif line.startswith("VALID:"):
            valid = line[len("VALID:"):].strip().lower().startswith("y")

    # Богатая строка для embedding: description уже содержит entity + technical + environment + camera
    parts = []
    if description:
        parts.append(description)
    if clip_type:
        parts.append(f"TYPE:{clip_type}")
    if motion:
        parts.append(f"MOTION:{motion}")

    combined = " | ".join(parts)
    return combined, valid and bool(description)


async def _analyze_clip_async(
    clip_id: str,
    frames: list[bytes],
    sem: asyncio.Semaphore,
) -> tuple[str, str, bool]:
    """
    Отправить кадры в Gemini. Возвращает (clip_id, keywords, valid).
    Retry с exponential backoff при 429.
    """
    from google.genai import types as gtypes

    client = _get_client()

    # Отправляем кадры с явными метками позиции — Gemini видит все три и знает порядок
    labels = ["[FRAME 1 — clip START (0.5s)]", "[FRAME 2 — clip MIDDLE (2.5s)]", "[FRAME 3 — clip END (4.5s)]"]
    parts = []
    for label, jpeg in zip(labels, frames):
        parts.append(gtypes.Part.from_text(text=label))
        parts.append(gtypes.Part.from_bytes(data=jpeg, mime_type="image/jpeg"))
    parts.append(gtypes.Part.from_text(text=GEMINI_PROMPT))

    max_retries = 8
    delay       = 5.0   # начальная задержка (429: ждём retryDelay, 503: 5s)

    async with sem:
        for attempt in range(max_retries):
            try:
                resp = await client.aio.models.generate_content(
                    model=GEMINI_MODEL,
                    contents=parts,
                    config=gtypes.GenerateContentConfig(
                        temperature=0.1,
                        max_output_tokens=300,
                    ),
                )
                text = resp.text or ""
                keywords, valid = _parse_gemini_response(text)
                return clip_id, keywords, valid

            except Exception as e:
                err_str = str(e)
                if "429" in err_str or "RESOURCE_EXHAUSTED" in err_str or "503" in err_str or "UNAVAILABLE" in err_str:
                    # Попробовать достать retryDelay из сообщения
                    import re
                    m = re.search(r"retryDelay.*?(\d+)s", err_str)
                    wait = float(m.group(1)) + 5 if m else delay * (2 ** attempt)
                    wait = min(wait, 120)
                    if attempt == 0:
                        code = "429" if "429" in err_str else "503"
                        print(f"  [{code}] throttle, жду {wait:.0f}s...", flush=True)
                    await asyncio.sleep(wait)
                else:
                    print(f"  [GEMINI ERR] {clip_id}: {e}", flush=True)
                    return clip_id, "", False

        print(f"  [GEMINI ERR] {clip_id}: превышено кол-во попыток", flush=True)
        return clip_id, "", False


# ── Phase 1: Scan ─────────────────────────────────────────────────────────────

def scan_phase(channel_id: str) -> dict:
    """FFprobe-скан всех клипов → чистый library.json с indexed=False."""
    clips_dir = get_clips_dir(channel_id)
    lib_json  = get_library_json(channel_id)

    mp4_files = sorted(clips_dir.glob("*.mp4"))
    total     = len(mp4_files)
    print(f"\n[SCAN] Найдено .mp4: {total}", flush=True)

    # Бэкап
    if lib_json.exists():
        date_str = datetime.now().strftime("%Y%m%d_%H%M%S")
        bak = lib_json.with_name(f"library.json.bak.{date_str}")
        shutil.copy2(lib_json, bak)
        for old in sorted(lib_json.parent.glob("library.json.bak.*"))[:-3]:
            try:
                old.unlink()
            except Exception:
                pass
        print(f"[SCAN] Бэкап → {bak.name}", flush=True)

    lib   = {"total": 0, "indexed_at": "", "clips": {}, "photos": {}}
    _lock = threading.Lock()
    done  = [0]
    errs  = [0]
    t0    = time.time()

    def probe_one(mp4: Path):
        return mp4.stem, _ffprobe(mp4)

    with ThreadPoolExecutor(max_workers=16) as ex:
        futures = {ex.submit(probe_one, f): f for f in mp4_files}
        for fut in as_completed(futures):
            stem, info = fut.result()
            with _lock:
                done[0] += 1
                if info:
                    lib["clips"][stem] = {
                        "file":     f"{stem}.mp4",
                        "duration": info["duration"],
                        "width":    info["width"],
                        "height":   info["height"],
                        "ratio":    round(info["width"] / info["height"], 3) if info["height"] else 0,
                        "fps":      info["fps"],
                        "indexed":  False,
                        "rejected": False,
                        "keywords": "",
                    }
                else:
                    errs[0] += 1
                if done[0] % 500 == 0 or done[0] == total:
                    print(f"  [SCAN] {done[0]}/{total} ffprobe...", flush=True)

    elapsed = time.time() - t0
    print(f"\n[SCAN] ✅ {len(lib['clips'])} клипов  ошибок: {errs[0]}  за {elapsed:.0f}s", flush=True)
    _save_lib(lib, lib_json)
    print(f"[SCAN] Сохранено → {lib_json}", flush=True)
    return lib


# ── Phase 2: Index (async) ─────────────────────────────────────────────────────

async def _index_async(
    channel_id: str,
    lib: dict,
    lib_json: Path,
    clips_dir: Path,
    todo: list[str],
    verbose: bool,
) -> dict:
    """Асинхронный индексатор: параллельные Gemini запросы."""
    sem   = asyncio.Semaphore(CONCURRENCY)
    stats = {"ok": 0, "rejected": 0, "error": 0}
    t0    = time.time()
    done  = [0]
    lock  = asyncio.Lock()

    async def process_one(clip_id: str) -> None:
        clip_path = clips_dir / f"{clip_id}.mp4"
        entry     = lib["clips"][clip_id]

        # Извлечь кадры в thread pool (блокирующий ffmpeg)
        loop   = asyncio.get_event_loop()
        frames = await loop.run_in_executor(
            None, extract_frames_bytes, clip_path
        )

        if not frames:
            entry.update(indexed=True, rejected=True, reject_reason="no_frames")
            async with lock:
                stats["rejected"] += 1
            if verbose:
                print(f"  ❌ {clip_id}: no_frames", flush=True)
            return

        # Quality checks
        if _is_black_bytes(frames[0]):
            entry.update(indexed=True, rejected=True, reject_reason="black_screen")
            async with lock:
                stats["rejected"] += 1
            if verbose:
                print(f"  ❌ {clip_id}: black_screen", flush=True)
            return

        if _is_frozen_bytes(frames):
            entry.update(indexed=True, rejected=True, reject_reason="frozen")
            async with lock:
                stats["rejected"] += 1
            if verbose:
                print(f"  ❌ {clip_id}: frozen", flush=True)
            return

        # Gemini
        old_keywords = entry.get("keywords", "")
        _, keywords, valid = await _analyze_clip_async(clip_id, frames, sem)

        if valid and keywords:
            # Compare quality: count keyword terms (before | TYPE | MOTION suffix)
            def _term_count(kw: str) -> int:
                base = kw.split("|")[0].strip()
                return len([t for t in base.split(",") if t.strip()])

            if old_keywords and _term_count(keywords) < _term_count(old_keywords):
                # New result is worse — keep old
                entry.update(indexed=True, rejected=False)
                async with lock:
                    stats["ok"] += 1
                if verbose:
                    print(f"  ✓ {clip_id}: kept ({_term_count(old_keywords)} terms)", flush=True)
            else:
                entry.update(indexed=True, rejected=False, keywords=keywords)
                async with lock:
                    stats["ok"] += 1
                if verbose:
                    improved = "↑" if old_keywords else "✅"
                    print(f"  {improved} {clip_id}: {keywords}", flush=True)
        else:
            reason = "gemini_invalid" if not valid else "gemini_empty"
            entry.update(indexed=True, rejected=True, reject_reason=reason, keywords=keywords)
            async with lock:
                stats["rejected"] += 1
            if verbose:
                print(f"  ⚠  {clip_id}: {reason} → {keywords!r}", flush=True)

        async with lock:
            done[0] += 1
            n = done[0]

        # Checkpoint
        if n % SAVE_EVERY == 0 or n == len(todo):
            _save_lib(lib, lib_json)
            elapsed = time.time() - t0
            rate    = n / elapsed * 3600 if elapsed > 0 else 0
            eta_h   = (len(todo) - n) / max(n / elapsed, 1) / 3600 if elapsed > 0 else 0
            print(
                f"  [{n}/{len(todo)}] ok={stats['ok']} rej={stats['rejected']} "
                f"rate={rate:.0f}/hr  ETA={eta_h:.1f}h",
                flush=True,
            )

    # Запустить все задачи параллельно (CONCURRENCY ограничен semaphore)
    tasks = [asyncio.create_task(process_one(cid)) for cid in todo]
    await asyncio.gather(*tasks)

    # Финальный checkpoint
    _save_lib(lib, lib_json)
    return stats


def index_clips(
    channel_id: str,
    limit: int | None = None,
    verbose: bool = False,
    all_clips: bool = False,
) -> dict:
    """Точка входа для индексации. Загружает lib, запускает async loop."""
    lib_json  = get_library_json(channel_id)
    clips_dir = get_clips_dir(channel_id)
    lib       = _load_lib(lib_json)

    if not lib["clips"]:
        print("[INDEX] library.json пустой — сначала запусти --scan", flush=True)
        return {}

    if all_clips:
        todo = sorted(lib["clips"].keys())
    else:
        todo = [
            cid for cid, entry in sorted(lib["clips"].items())
            if not entry.get("indexed")
        ]
    if limit:
        todo = todo[:limit]

    print(f"\n[INDEX] Клипов для индексации: {len(todo)}", flush=True)
    if not todo:
        print("[INDEX] Все клипы уже проиндексированы.", flush=True)
        return {"ok": 0, "rejected": 0, "error": 0}

    print(f"[INDEX] Модель: {GEMINI_MODEL}  Параллельность: {CONCURRENCY}", flush=True)

    t_start = time.time()
    stats   = asyncio.run(_index_async(channel_id, lib, lib_json, clips_dir, todo, verbose))
    elapsed = time.time() - t_start

    total_done = stats["ok"] + stats["rejected"]
    pct_ok     = 100 * stats["ok"] // max(total_done, 1)

    print(f"\n{'='*60}", flush=True)
    print(f"[INDEX] ✅ ГОТОВО за {elapsed:.0f}s ({elapsed/3600:.1f}h)", flush=True)
    print(f"  OK:       {stats['ok']}", flush=True)
    print(f"  Rejected: {stats['rejected']}", flush=True)
    print(f"  Годных:   {pct_ok}%", flush=True)
    print(f"{'='*60}\n", flush=True)

    return stats


# ── Smart re-index ────────────────────────────────────────────────────────────

async def _smart_reindex(channel_id: str) -> None:
    """
    Проходит все клипы:
    - хороший keywords → пропустить (бесплатно)
    - плохой / неточный → 2 кадра + усиленный промпт → обновить
    """
    lib_json  = get_library_json(channel_id)
    clips_dir = get_clips_dir(channel_id)
    lib       = _load_lib(lib_json)

    all_ids = sorted(lib["clips"].keys())
    todo    = [cid for cid in all_ids if _needs_reindex(lib["clips"][cid].get("keywords", ""))]
    skip    = len(all_ids) - len(todo)

    print(f"\n[SMART] Всего клипов: {len(all_ids)}", flush=True)
    print(f"[SMART] Пропускаем (хорошие): {skip}", flush=True)
    print(f"[SMART] Переиндексируем (плохие): {len(todo)}", flush=True)

    if not todo:
        print("[SMART] Все клипы уже описаны хорошо.", flush=True)
        return

    sem   = asyncio.Semaphore(CONCURRENCY)
    stats = {"ok": 0, "kept": 0, "rejected": 0}
    t0    = time.time()
    done  = [0]
    lock  = asyncio.Lock()

    async def process_one(clip_id: str) -> None:
        clip_path = clips_dir / f"{clip_id}.mp4"
        entry     = lib["clips"][clip_id]
        duration  = entry.get("duration", 5.0)

        loop   = asyncio.get_event_loop()
        frames = await loop.run_in_executor(
            None, extract_frames_smart, clip_path, duration
        )

        if not frames:
            print(f"  ❌ {clip_id}: no_frames", flush=True)
            async with lock:
                stats["rejected"] += 1
            async with lock:
                done[0] += 1
            return

        # Вызов Gemini с усиленным промптом
        from google.genai import types as gtypes
        parts = [gtypes.Part.from_bytes(data=f, mime_type="image/jpeg") for f in frames]
        parts.append(gtypes.Part.from_text(text=SMART_PROMPT))

        delay = 5.0
        keywords, valid = "", True
        for attempt in range(8):
            try:
                async with sem:
                    resp = await client.aio.models.generate_content(
                        model=GEMINI_MODEL,
                        contents=parts,
                        config=gtypes.GenerateContentConfig(
                            max_output_tokens=350,
                            temperature=0.1,
                        ),
                    )
                text = (resp.text or "").strip()
                if text:
                    keywords, valid = _parse_gemini_response(text)
                    break
            except Exception as e:
                err_str = str(e)
                if "429" in err_str or "503" in err_str:
                    await asyncio.sleep(delay)
                    delay = min(delay * 2, 120)
                else:
                    break

        old_kw = entry.get("keywords", "")

        def _term_count(kw: str) -> int:
            base = kw.split("|")[0].strip()
            return len([t for t in base.split(",") if t.strip()])

        if keywords and _term_count(keywords) >= _term_count(old_kw):
            entry.update(indexed=True, rejected=False, keywords=keywords)
            async with lock:
                stats["ok"] += 1
            print(f"  ↑ {clip_id}: {keywords}", flush=True)
        elif keywords:
            # New is worse — keep old but mark as indexed
            entry.update(indexed=True, rejected=False)
            async with lock:
                stats["kept"] += 1
            print(f"  ✓ {clip_id}: kept old ({_term_count(old_kw)} terms)", flush=True)
        else:
            async with lock:
                stats["rejected"] += 1
            print(f"  ⚠ {clip_id}: empty response", flush=True)

        async with lock:
            done[0] += 1
            n = done[0]

        if n % SAVE_EVERY == 0 or n == len(todo):
            _save_lib(lib, lib_json)
            elapsed = time.time() - t0
            rate    = n / elapsed * 3600 if elapsed > 0 else 0
            eta_h   = (len(todo) - n) / max(n / elapsed, 1) / 3600 if elapsed > 0 else 0
            print(
                f"  [{n}/{len(todo)}] updated={stats['ok']} kept={stats['kept']} "
                f"rate={rate:.0f}/hr  ETA={eta_h:.1f}h",
                flush=True,
            )

    tasks = [asyncio.create_task(process_one(cid)) for cid in todo]
    await asyncio.gather(*tasks)
    _save_lib(lib, lib_json)

    elapsed = time.time() - t0
    print(f"\n{'='*60}", flush=True)
    print(f"[SMART] ✅ Готово за {elapsed:.0f}s ({elapsed/3600:.1f}h)", flush=True)
    print(f"  Пропущено (хорошие): {skip}", flush=True)
    print(f"  Обновлено:           {stats['ok']}", flush=True)
    print(f"  Оставлено (лучше):   {stats['kept']}", flush=True)
    print(f"  Ошибки:              {stats['rejected']}", flush=True)
    print(f"{'='*60}\n", flush=True)


# ── Main ──────────────────────────────────────────────────────────────────────

def main() -> None:
    parser = argparse.ArgumentParser(
        description="Индексатор космической библиотеки через Gemini 2.0 Flash"
    )
    parser.add_argument("--channel", default="channel_001_cosmos_de")

    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--scan",   action="store_true",
                      help="Только FFprobe-скан → library.json с indexed=False")
    mode.add_argument("--test",   type=int, metavar="N", nargs="?", const=20,
                      help="Scan + тест первых N клипов (дефолт: 20)")
    mode.add_argument("--full",   action="store_true",
                      help="Scan + полный Gemini-индекс")
    mode.add_argument("--resume", action="store_true",
                      help="Продолжить с места остановки (без scan)")
    mode.add_argument("--reindex-rejected", action="store_true",
                      help="Сбросить indexed у всех rejected клипов и переиндексировать")
    mode.add_argument("--reindex-filter", metavar="PATTERN",
                      help="Переиндексировать клипы, у которых keywords содержит PATTERN (case-insensitive)")
    mode.add_argument("--reindex-poor", action="store_true",
                      help="Автоматически переиндексировать клипы с короткими/слабыми keywords (< 4 terms)")
    mode.add_argument("--reindex-all", action="store_true",
                      help="Прогнать все клипы заново: обновить если новый результат лучше, иначе оставить")
    mode.add_argument("--reindex-smart", action="store_true",
                      help="2 кадра на клип: хорошие — пропустить, плохие — переиндексировать усиленным промптом")

    args = parser.parse_args()

    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")

    print(f"\n{'='*60}")
    print(f"  COSMOS LIBRARY — Gemini {GEMINI_MODEL}")
    print(f"  Channel : {args.channel}")
    mode_str = ("scan" if args.scan else
                f"test-{args.test}" if args.test else
                "full" if args.full else "resume")
    print(f"  Mode    : {mode_str}")
    print(f"  Time    : {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    print(f"{'='*60}\n", flush=True)

    if args.scan:
        scan_phase(args.channel)
        print("\n[NEXT] py agents/library/gemini_indexer.py --full", flush=True)

    elif args.test is not None:
        scan_phase(args.channel)
        stats = index_clips(args.channel, limit=args.test, verbose=True)
        ok  = stats.get("ok", 0)
        rej = stats.get("rejected", 0)
        tot = ok + rej
        print(f"\n[TEST] ИТОГ: {ok}/{tot} годных ({100*ok//max(tot,1)}%)", flush=True)
        if ok / max(tot, 1) >= 0.80:
            print("[TEST] ✅ Качество OK — можно запускать --full", flush=True)
        else:
            print("[TEST] ⚠️  Качество < 80%", flush=True)

    elif args.full:
        scan_phase(args.channel)
        index_clips(args.channel, verbose=False)
        print("[NEXT] py agents/library/visual_embedder.py --channel", args.channel, flush=True)

    elif args.resume:
        index_clips(args.channel, verbose=False)
        print("[NEXT] py agents/library/visual_embedder.py --channel", args.channel, flush=True)

    elif args.reindex_smart:
        asyncio.run(_smart_reindex(args.channel))

    elif args.reindex_all:
        print(f"[REINDEX-ALL] Прогоняем все клипы, обновляем только если новый результат лучше", flush=True)
        index_clips(args.channel, verbose=True, all_clips=True)

    elif args.reindex_poor:
        lib_json = get_library_json(args.channel)
        lib = _load_lib(lib_json)
        count = 0
        for entry in lib["clips"].values():
            if not entry.get("indexed"):
                continue
            if entry.get("rejected"):
                continue
            kw = entry.get("keywords", "")
            # Strip TYPE/MOTION suffix to count only actual keyword terms
            kw_clean = kw.split("|")[0].strip()
            terms = [t.strip() for t in kw_clean.split(",") if t.strip()]
            if len(terms) < 4:
                entry["indexed"] = False
                entry["keywords"] = ""
                count += 1
        _save_lib(lib, lib_json)
        print(f"[REINDEX] Клипов с < 4 keywords terms: {count} → переиндексация", flush=True)
        if count == 0:
            print("[REINDEX] Все клипы описаны достаточно хорошо.", flush=True)
        else:
            index_clips(args.channel, verbose=False)

    elif args.reindex_filter:
        lib_json = get_library_json(args.channel)
        lib = _load_lib(lib_json)
        pattern = args.reindex_filter.lower()
        count = 0
        for entry in lib["clips"].values():
            if pattern in entry.get("keywords", "").lower():
                entry["indexed"] = False
                entry["rejected"] = False
                entry.pop("reject_reason", None)
                entry["keywords"] = ""
                count += 1
        _save_lib(lib, lib_json)
        print(f"[REINDEX] Найдено по '{args.reindex_filter}': {count} клипов → переиндексация", flush=True)
        if count == 0:
            print("[REINDEX] Ничего не найдено. Проверь паттерн.", flush=True)
        else:
            index_clips(args.channel, verbose=True)

    elif args.reindex_rejected:
        lib_json = get_library_json(args.channel)
        lib = _load_lib(lib_json)
        count = 0
        for entry in lib["clips"].values():
            if entry.get("rejected"):
                entry["indexed"] = False
                entry["rejected"] = False
                entry.pop("reject_reason", None)
                entry["keywords"] = ""
                count += 1
        _save_lib(lib, lib_json)
        print(f"[REINDEX] Сброшено {count} rejected клипов → будут переиндексированы", flush=True)
        index_clips(args.channel, verbose=False)
        print("[NEXT] py agents/library/visual_embedder.py --channel", args.channel, flush=True)


if __name__ == "__main__":
    main()
