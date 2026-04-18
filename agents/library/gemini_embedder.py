"""
gemini_embedder.py — Gemini embedding-001 для библиотеки клипов.

Заменяет E5-large: нативно мультиязычный, 3072-dim, батчевый.
Один раз строим gemini_embeddings.npz, потом только читаем.

Usage:
    py agents/library/gemini_embedder.py --channel channel_001_cosmos_de
    py agents/library/gemini_embedder.py --channel channel_001_cosmos_de --rebuild
"""

import argparse
import os
import sys
import time
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "agents" / "utils"))
from paths import get_library_json, get_library_dir

GEMINI_EMBED_MODEL  = "gemini-embedding-001"
GEMINI_EMBED_FILE   = "gemini_embeddings.npz"
BATCH_SIZE          = 50    # Уменьшен: меньше риск 429 за один запрос
RETRY_DELAY         = 5.0   # секунд между ретраями (базовый)
MAX_RETRIES         = 8     # больше попыток для rate limit
INTER_BATCH_DELAY   = 1.0   # задержка между батчами (QPM rate limit)

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


CHECKPOINT_EVERY = 20   # сохранять checkpoint каждые N батчей


def embed_batch(texts: list[str]) -> np.ndarray:
    """Батчевый embed через Gemini. Возвращает (N, 3072) нормализованный массив."""
    client = _get_client()
    all_vecs = []

    for i in range(0, len(texts), BATCH_SIZE):
        batch = texts[i:i + BATCH_SIZE]
        for attempt in range(MAX_RETRIES):
            try:
                resp = client.models.embed_content(
                    model=GEMINI_EMBED_MODEL,
                    contents=batch,
                )
                vecs = np.array([e.values for e in resp.embeddings], dtype=np.float32)
                # Нормализуем
                norms = np.linalg.norm(vecs, axis=1, keepdims=True) + 1e-9
                vecs = vecs / norms
                all_vecs.append(vecs)
                if i % 500 == 0 and i > 0:
                    print(f"  [GeminiEmbed] прогресс: {i}/{len(texts)}...", flush=True)
                break
            except Exception as e:
                err_str = str(e)
                is_rate_limit = "429" in err_str or "RESOURCE_EXHAUSTED" in err_str
                print(f"  [ERROR] batch {i} attempt {attempt+1}: {err_str[:300]}", flush=True)
                if attempt < MAX_RETRIES - 1:
                    if is_rate_limit:
                        delay = min(RETRY_DELAY * (3 ** attempt), 300.0)
                        print(f"  [WARN] Rate limit batch {i}: waiting {delay:.0f}s "
                              f"(attempt {attempt+1}/{MAX_RETRIES})", flush=True)
                    else:
                        delay = RETRY_DELAY * (attempt + 1)
                    time.sleep(delay)
                else:
                    raise RuntimeError(f"Gemini embed batch {i}-{i+len(batch)} failed: {e}")
        else:
            continue
        # Задержка между батчами чтобы не превышать QPM
        if INTER_BATCH_DELAY > 0:
            time.sleep(INTER_BATCH_DELAY)

    return np.vstack(all_vecs)


def _clip_embed_text(entry: dict) -> str:
    """Собрать богатый текст клипа для RETRIEVAL_DOCUMENT embedding.

    Объединяет все языковые keywords + category + tags для максимального покрытия.
    """
    parts = []
    for field in ("keywords", "keywords_de", "keywords_fr", "keywords_es"):
        v = entry.get(field, "").strip()
        if v:
            parts.append(v)
    cat = entry.get("category", "").strip()
    if cat and cat not in ("other",):
        parts.append(cat)
    tags = entry.get("tags", [])
    if isinstance(tags, list) and tags:
        parts.append(", ".join(tags))
    return "; ".join(parts)


def build_library_embeddings(channel_id: str, resume: bool = False) -> tuple[list[str], np.ndarray]:
    """Построить gemini_embeddings.npz для канала. Возвращает (clip_ids, embeddings).

    resume=True: если частичный файл уже есть (от прерванного запуска),
                 пропустить уже обработанные клипы и продолжить с оставшихся.
    """
    lib_path = get_library_json(channel_id)
    import json
    lib = json.loads(lib_path.read_text(encoding="utf-8"))
    clips = lib["clips"]

    # Собираем (clip_id, text) только для indexed + not rejected
    # Используем _clip_embed_text: keywords всех языков + category + tags (caption augmentation)
    valid = [
        (cid, entry)
        for cid, entry in clips.items()
        if entry.get("indexed") and not entry.get("rejected") and entry.get("keywords")
    ]
    clip_ids = [cid   for cid, _     in valid]
    texts    = [_clip_embed_text(entry) for _, entry in valid]

    out_path = get_library_dir(channel_id) / GEMINI_EMBED_FILE

    # Resume: загружаем уже посчитанные embeddings
    done_ids: list[str] = []
    done_embs: np.ndarray | None = None
    if resume and out_path.exists():
        try:
            _d = np.load(str(out_path), allow_pickle=True)
            done_ids = _d["clip_ids"].tolist()
            done_embs = _d["embeddings"]
            print(f"[GeminiEmbed] Resume: уже готово {len(done_ids)} клипов", flush=True)
        except Exception:
            done_ids = []
            done_embs = None

    # Пропускаем уже обработанные
    done_set = set(done_ids)
    remaining = [(cid, txt) for cid, txt in zip(clip_ids, texts) if cid not in done_set]

    if not remaining:
        print(f"[GeminiEmbed] Все клипы уже обработаны ({len(done_ids)})", flush=True)
        if done_embs is not None:
            return done_ids, done_embs

    rem_ids   = [cid for cid, _ in remaining]
    rem_texts = [kw  for _, kw in remaining]

    print(f"[GeminiEmbed] Embedding {len(rem_texts)} клипов батчами по {BATCH_SIZE}...", flush=True)
    t0 = time.time()

    # Batch с checkpoint: сохраняем каждые CHECKPOINT_EVERY батчей
    client = _get_client()
    acc_ids: list[str] = list(done_ids)
    acc_embs_list: list[np.ndarray] = [done_embs] if done_embs is not None and len(done_ids) > 0 else []
    batch_count = 0

    for bi in range(0, len(rem_texts), BATCH_SIZE):
        batch_texts = rem_texts[bi:bi + BATCH_SIZE]
        batch_ids   = rem_ids[bi:bi + BATCH_SIZE]
        for attempt in range(MAX_RETRIES):
            try:
                resp = client.models.embed_content(
                    model=GEMINI_EMBED_MODEL,
                    contents=batch_texts,
                    config={"task_type": "RETRIEVAL_DOCUMENT"},
                )
                vecs = np.array([e.values for e in resp.embeddings], dtype=np.float32)
                norms = np.linalg.norm(vecs, axis=1, keepdims=True) + 1e-9
                vecs = vecs / norms
                acc_ids.extend(batch_ids)
                acc_embs_list.append(vecs)
                batch_count += 1
                if bi % 500 == 0 and bi > 0:
                    print(f"  [GeminiEmbed] progress: {bi}/{len(rem_texts)}...", flush=True)
                break
            except Exception as e:
                err_str = str(e)
                is_rate_limit = "429" in err_str or "RESOURCE_EXHAUSTED" in err_str
                if attempt < MAX_RETRIES - 1:
                    delay = min(RETRY_DELAY * (3 ** attempt), 300.0) if is_rate_limit else RETRY_DELAY * (attempt + 1)
                    print(f"  [WARN] Rate limit batch {bi}: waiting {delay:.0f}s "
                          f"(attempt {attempt+1}/{MAX_RETRIES})", flush=True)
                    time.sleep(delay)
                else:
                    # Сохраняем что успели перед крашем
                    if acc_embs_list:
                        _partial = np.vstack(acc_embs_list)
                        np.savez_compressed(str(out_path), clip_ids=acc_ids, embeddings=_partial)
                        print(f"  [GeminiEmbed] Checkpoint saved: {len(acc_ids)} clips", flush=True)
                    raise RuntimeError(f"Gemini embed batch {bi}-{bi+len(batch_texts)} failed: {e}")
        else:
            continue
        if INTER_BATCH_DELAY > 0:
            time.sleep(INTER_BATCH_DELAY)
        # Checkpoint save
        if batch_count % CHECKPOINT_EVERY == 0 and acc_embs_list:
            _ckpt = np.vstack(acc_embs_list)
            np.savez_compressed(str(out_path), clip_ids=acc_ids, embeddings=_ckpt)
            print(f"  [GeminiEmbed] Checkpoint: {len(acc_ids)} clips saved", flush=True)

    elapsed = time.time() - t0
    all_embs = np.vstack(acc_embs_list) if acc_embs_list else np.zeros((0, 3072), dtype=np.float32)
    all_ids  = acc_ids
    print(f"[GeminiEmbed] Done: {all_embs.shape} in {elapsed:.1f}s", flush=True)

    np.savez_compressed(str(out_path), clip_ids=all_ids, embeddings=all_embs)
    print(f"[GeminiEmbed] Saved: {out_path} ({len(all_ids)} clips)", flush=True)
    return all_ids, all_embs


def load_library_embeddings(channel_id: str) -> tuple[list[str], np.ndarray] | None:
    """Загрузить gemini_embeddings.npz. Если нет — строим."""
    out_path = get_library_dir(channel_id) / GEMINI_EMBED_FILE
    if not out_path.exists():
        print(f"[GeminiEmbed] {out_path.name} не найден — строим...", flush=True)
        return build_library_embeddings(channel_id)
    data = np.load(str(out_path), allow_pickle=True)
    clip_ids   = data["clip_ids"].tolist()
    embeddings = data["embeddings"]
    print(f"[GeminiEmbed] Загружено: {len(clip_ids)} клипов ({embeddings.shape[1]}-dim)", flush=True)
    return clip_ids, embeddings


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--channel", default="channel_001_cosmos_de")
    ap.add_argument("--rebuild", action="store_true")
    ap.add_argument("--resume",  action="store_true", help="Продолжить с места остановки")
    args = ap.parse_args()

    lib_dir = get_library_dir(args.channel)
    out = lib_dir / GEMINI_EMBED_FILE
    if args.rebuild and out.exists():
        out.unlink()
        print(f"[GeminiEmbed] Удалён старый {out.name}", flush=True)

    build_library_embeddings(args.channel, resume=args.resume)
