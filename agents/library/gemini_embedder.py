"""
gemini_embedder.py — Gemini Embedding 2 мультимодальный индекс библиотеки.

Модель: gemini-embedding-2-preview (текст + изображения, 3072-dim)

Build: для каждого клипа — 3 кадра (0.1s/2.5s/4.9s) + текст keywords → один вектор
Query: текст сегмента → вектор (батчевый, дешёвый)

Usage:
    py agents/library/gemini_embedder.py --channel channel_001_cosmos_de
    py agents/library/gemini_embedder.py --channel channel_001_cosmos_de --rebuild
"""

import argparse
import asyncio
import os
import subprocess
import sys
import time
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "agents" / "utils"))
from paths import get_library_json, get_library_dir, get_clips_dir

GEMINI_EMBED_MODEL  = "gemini-embedding-2-preview"
GEMINI_EMBED_FILE   = "gemini_embeddings.npz"
FRAME_POSITIONS     = [0.1, 2.5, 4.9]   # секунды (0s / 2.5s / 5s)
FRAME_SIZE          = 512                 # px ширина
CONCURRENCY         = 15                  # параллельных embed API вызовов
CHECKPOINT_EVERY    = 100
RETRY_DELAY         = 5.0
MAX_RETRIES         = 8

_client = None


def _get_client():
    global _client
    if _client is None:
        import google.genai
        from dotenv import load_dotenv
        load_dotenv(ROOT / "config" / ".env")
        api_key = os.environ.get("GEMINI_API_KEY", "")
        if not api_key:
            raise RuntimeError("GEMINI_API_KEY не найден в config/.env")
        _client = google.genai.Client(api_key=api_key)
    return _client


# ── Frame extraction ───────────────────────────────────────────────────────────

def _extract_frame(clip_path: Path, ss: float) -> bytes | None:
    r = subprocess.run(
        ["ffmpeg", "-y", "-ss", str(ss), "-i", str(clip_path),
         "-vframes", "1", "-vf", f"scale={FRAME_SIZE}:-2",
         "-f", "image2pipe", "-vcodec", "mjpeg", "-q:v", "4",
         "-loglevel", "error", "pipe:1"],
        capture_output=True, timeout=30,
    )
    return r.stdout if r.returncode == 0 and r.stdout else None


def extract_frames(clip_path: Path) -> list[bytes]:
    from concurrent.futures import ThreadPoolExecutor
    results = {}
    def _ex(ss):
        results[ss] = _extract_frame(clip_path, ss)
    with ThreadPoolExecutor(max_workers=3) as ex:
        futs = [ex.submit(_ex, ss) for ss in FRAME_POSITIONS]
        for f in futs:
            f.result()
    return [results[ss] for ss in FRAME_POSITIONS if results.get(ss)]


# ── Normalize vector ───────────────────────────────────────────────────────────

def _norm(v: np.ndarray) -> np.ndarray:
    n = np.linalg.norm(v) + 1e-9
    return v / n


# ── Build: мультимодальный embed одного клипа ──────────────────────────────────

async def _embed_clip_async(
    clip_id: str,
    keywords: str,
    clip_path: Path,
    sem: asyncio.Semaphore,
    loop: asyncio.AbstractEventLoop,
) -> tuple[str, np.ndarray | None]:
    from google.genai import types as gtypes

    client = _get_client()

    frames = await loop.run_in_executor(None, extract_frames, clip_path)

    contents = [gtypes.Part.from_text(text=f"search result: {keywords}")]
    for frame_bytes in frames:
        contents.append(gtypes.Part.from_bytes(data=frame_bytes, mime_type="image/jpeg"))

    delay = RETRY_DELAY
    async with sem:
        for attempt in range(MAX_RETRIES):
            try:
                resp = await client.aio.models.embed_content(
                    model=GEMINI_EMBED_MODEL,
                    contents=contents,
                )
                vec = np.array(resp.embeddings[0].values, dtype=np.float32)
                return clip_id, _norm(vec)
            except Exception as e:
                err = str(e)
                if "429" in err or "RESOURCE_EXHAUSTED" in err or "503" in err or "UNAVAILABLE" in err:
                    wait = min(delay * (2 ** attempt), 120)
                    await asyncio.sleep(wait)
                else:
                    print(f"  [ERR] {clip_id}: {err[:200]}", flush=True)
                    return clip_id, None

    print(f"  [ERR] {clip_id}: превышено кол-во попыток", flush=True)
    return clip_id, None


# ── Build index ────────────────────────────────────────────────────────────────

async def _build_async(
    channel_id: str,
    clip_ids: list[str],
    keywords_map: dict[str, str],
    clips_dir: Path,
    out_path: Path,
    resume: bool,
) -> tuple[list[str], np.ndarray]:

    # Resume: загружаем уже посчитанные
    done_ids: list[str] = []
    done_vecs: list[np.ndarray] = []
    if resume and out_path.exists():
        try:
            d = np.load(str(out_path), allow_pickle=True)
            done_ids = d["clip_ids"].tolist()
            done_vecs = [d["embeddings"][i] for i in range(len(done_ids))]
            print(f"[Embed] Resume: уже готово {len(done_ids)} клипов", flush=True)
        except Exception:
            done_ids, done_vecs = [], []

    done_set = set(done_ids)
    remaining = [(cid, keywords_map[cid]) for cid in clip_ids if cid not in done_set]

    if not remaining:
        print(f"[Embed] Все клипы уже обработаны ({len(done_ids)})", flush=True)
        all_embs = np.stack(done_vecs) if done_vecs else np.zeros((0, 3072), dtype=np.float32)
        return done_ids, all_embs

    print(f"[Embed] Обрабатываем {len(remaining)} клипов (CONCURRENCY={CONCURRENCY})...", flush=True)
    t0 = time.time()

    sem      = asyncio.Semaphore(CONCURRENCY)
    task_sem = asyncio.Semaphore(CONCURRENCY * 3)  # макс задач в полёте (ffmpeg + API)
    loop = asyncio.get_event_loop()

    acc_ids = list(done_ids)
    acc_vecs = list(done_vecs)
    done_count = [0]
    lock = asyncio.Lock()

    async def process_one(cid: str, kw: str):
        clip_path = clips_dir / f"{cid}.mp4"
        if not clip_path.exists():
            print(f"  [SKIP] {cid}: файл не найден", flush=True)
            return

        clip_id, vec = await _embed_clip_async(cid, kw, clip_path, sem, loop)
        if vec is None:
            return

        async with lock:
            acc_ids.append(clip_id)
            acc_vecs.append(vec)
            done_count[0] += 1
            n = done_count[0]

        if n % CHECKPOINT_EVERY == 0 or n == len(remaining):
            async with lock:
                if acc_vecs:
                    embs = np.stack(acc_vecs)
                    np.savez_compressed(str(out_path), clip_ids=acc_ids, embeddings=embs)
            elapsed = time.time() - t0
            rate = n / elapsed * 3600 if elapsed > 0 else 0
            eta = (len(remaining) - n) / max(n / elapsed, 1) / 60 if elapsed > 0 else 0
            print(f"  [{n}/{len(remaining)}] rate={rate:.0f}/hr  ETA={eta:.1f}min", flush=True)

    async def process_one_throttled(cid: str, kw: str):
        async with task_sem:
            await process_one(cid, kw)

    tasks = [asyncio.create_task(process_one_throttled(cid, kw)) for cid, kw in remaining]
    await asyncio.gather(*tasks)

    all_embs = np.stack(acc_vecs) if acc_vecs else np.zeros((0, 3072), dtype=np.float32)
    np.savez_compressed(str(out_path), clip_ids=acc_ids, embeddings=all_embs)
    print(f"[Embed] Сохранено: {out_path} ({len(acc_ids)} клипов)", flush=True)
    return acc_ids, all_embs


def build_library_embeddings(channel_id: str, resume: bool = False) -> tuple[list[str], np.ndarray]:
    import json
    lib = json.loads(get_library_json(channel_id).read_text(encoding="utf-8"))
    clips = lib["clips"]

    valid = [
        (cid, entry)
        for cid, entry in clips.items()
        if entry.get("indexed") and not entry.get("rejected") and entry.get("keywords")
    ]
    clip_ids     = [cid for cid, _ in valid]
    keywords_map = {cid: entry["keywords"] for cid, entry in valid}

    clips_dir = get_clips_dir(channel_id)
    out_path  = get_library_dir(channel_id) / GEMINI_EMBED_FILE

    print(f"[Embed] Клипов для индекса: {len(clip_ids)}", flush=True)
    return asyncio.run(_build_async(channel_id, clip_ids, keywords_map, clips_dir, out_path, resume))


def load_library_embeddings(channel_id: str) -> tuple[list[str], np.ndarray] | None:
    out_path = get_library_dir(channel_id) / GEMINI_EMBED_FILE
    if not out_path.exists():
        print(f"[Embed] {out_path.name} не найден — строим...", flush=True)
        return build_library_embeddings(channel_id)
    data = np.load(str(out_path), allow_pickle=True)
    clip_ids   = data["clip_ids"].tolist()
    embeddings = data["embeddings"]
    print(f"[Embed] Загружено: {len(clip_ids)} клипов ({embeddings.shape[1]}-dim)", flush=True)
    return clip_ids, embeddings


# ── Query: батчевый текстовый embed сегментов ─────────────────────────────────

def embed_batch(texts: list[str], prev_descs: list[str] | None = None) -> np.ndarray:
    """
    Батчевый embed текстовых запросов (сегменты сценария).
    prev_descs: описание предыдущего клипа для каждого сегмента (опционально).
    Формат: "search query: previous: {prev} | {text}"
    """
    client = _get_client()

    formatted = []
    for i, text in enumerate(texts):
        prev = (prev_descs[i] if prev_descs and i < len(prev_descs) and prev_descs[i] else "")
        if prev:
            formatted.append(f"search query: previous: {prev[:100]} | {text}")
        else:
            formatted.append(f"search query: {text}")

    BATCH_SIZE = 50
    all_vecs = []

    for i in range(0, len(formatted), BATCH_SIZE):
        batch = formatted[i:i + BATCH_SIZE]
        for attempt in range(MAX_RETRIES):
            try:
                resp = client.models.embed_content(
                    model=GEMINI_EMBED_MODEL,
                    contents=batch,
                )
                vecs = np.array([e.values for e in resp.embeddings], dtype=np.float32)
                norms = np.linalg.norm(vecs, axis=1, keepdims=True) + 1e-9
                all_vecs.append(vecs / norms)
                break
            except Exception as e:
                err = str(e)
                is_rate = "429" in err or "RESOURCE_EXHAUSTED" in err
                if attempt < MAX_RETRIES - 1:
                    wait = min(RETRY_DELAY * (3 ** attempt), 300) if is_rate else RETRY_DELAY * (attempt + 1)
                    time.sleep(wait)
                else:
                    raise RuntimeError(f"embed_batch failed at {i}: {e}")
        time.sleep(0.5)  # QPM

    return np.vstack(all_vecs) if all_vecs else np.zeros((len(texts), 3072), dtype=np.float32)


# ── CLI ────────────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--channel", default="channel_001_cosmos_de")
    ap.add_argument("--rebuild", action="store_true")
    ap.add_argument("--resume",  action="store_true")
    args = ap.parse_args()

    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")

    # ── Kill any existing embedder instance before starting ────────────────────
    import psutil
    _self_pid = os.getpid()
    _script   = Path(__file__).name
    for _proc in psutil.process_iter(["pid", "cmdline"]):
        try:
            _cmd = " ".join(_proc.info["cmdline"] or [])
            if _script in _cmd and _proc.info["pid"] != _self_pid:
                print(f"[Embed] Убиваем старый процесс PID={_proc.info['pid']}", flush=True)
                _proc.kill()
        except (psutil.NoSuchProcess, psutil.AccessDenied):
            pass

    out = get_library_dir(args.channel) / GEMINI_EMBED_FILE
    if args.rebuild and out.exists():
        out.unlink()
        print(f"[Embed] Удалён старый {out.name}", flush=True)

    ids, embs = build_library_embeddings(args.channel, resume=args.resume)
    elapsed = time.time()
    print(f"[Embed] Готово: {embs.shape}", flush=True)
