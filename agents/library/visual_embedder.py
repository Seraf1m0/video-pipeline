"""
visual_embedder.py — CLIP visual embeddings для клипов библиотеки.

Оптимизированная версия:
  - 2 кадра вместо 3 (первый + последний, 10%/90%)
  - Параллельная ffmpeg-экстракция (ThreadPoolExecutor, 8 воркеров)
  - Батчевый CLIP encode (64 изображения за вызов)

Время: cosmos 5637 клипов ~50-60 мин, religion 945 ~8-10 мин.

Запуск:
    py agents/library/visual_embedder.py --channel channel_001_cosmos_de
    py agents/library/visual_embedder.py --channel channel_003_religion_es
    py agents/library/visual_embedder.py --channel channel_001_cosmos_de --rebuild
"""
import argparse
import io
import json
import subprocess
import sys
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

import numpy as np

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

_utils_dir = Path(__file__).resolve().parent.parent / "utils"
if str(_utils_dir) not in sys.path:
    sys.path.insert(0, str(_utils_dir))
from paths import get_library_json, get_clips_dir, get_library_dir

CLIP_MODEL       = "clip-ViT-B-32"   # 512-dim, CLIP image encoder (совместим с multilingual-v1 для текста)
N_FRAMES         = 2       # кадров с клипа: 10% и 90% длительности
BATCH_SIZE       = 64      # клипов за чекпоинт
ENCODE_BATCH     = 64      # изображений за один CLIP encode вызов
FFMPEG_WORKERS   = 8       # параллельных ffmpeg процессов
VIS_EMB_FILENAME = "visual_embeddings.npz"

_clip_model = None


# ── Модель ────────────────────────────────────────────────────────────────────

def _get_clip_model():
    global _clip_model
    if _clip_model is None:
        from sentence_transformers import SentenceTransformer
        _clip_model = SentenceTransformer(CLIP_MODEL)
        print(f"✅ CLIP модель загружена: {CLIP_MODEL}", flush=True)
    return _clip_model


# ── Кадры ─────────────────────────────────────────────────────────────────────

def _get_duration(clip_path: Path) -> float:
    r = subprocess.run(
        ["ffprobe", "-v", "quiet", "-show_entries", "format=duration",
         "-of", "csv=p=0", str(clip_path)],
        capture_output=True, text=True,
    )
    try:
        return float(r.stdout.strip())
    except (ValueError, AttributeError):
        return 5.0


def _extract_frame(clip_path: Path, t: float):
    """Извлечь один кадр в момент t → PIL Image или None."""
    from PIL import Image
    r = subprocess.run(
        ["ffmpeg", "-ss", f"{t:.3f}", "-i", str(clip_path),
         "-vframes", "1", "-vf", "scale=224:224",
         "-f", "image2pipe", "-vcodec", "png", "-loglevel", "quiet", "-"],
        capture_output=True,
    )
    if r.returncode == 0 and r.stdout:
        try:
            return Image.open(io.BytesIO(r.stdout)).convert("RGB")
        except Exception:
            pass
    return None


def extract_frames_parallel(clip_path: Path, n_frames: int = N_FRAMES) -> list:
    """
    Извлечь n_frames кадров параллельно (10%/90% длительности).
    Возвращает список PIL Images.
    """
    dur = _get_duration(clip_path)
    positions = [dur * (i + 1) / (n_frames + 1) for i in range(n_frames)]

    frames = []
    with ThreadPoolExecutor(max_workers=n_frames) as ex:
        futures = {ex.submit(_extract_frame, clip_path, t): t for t in positions}
        for fut in as_completed(futures):
            img = fut.result()
            if img is not None:
                frames.append(img)
    return frames


# ── Кодирование ───────────────────────────────────────────────────────────────

def encode_images_batch(images: list) -> list[np.ndarray]:
    """
    Батчевый CLIP encode: список PIL Images → список нормализованных векторов (512,).
    Все изображения кодируются за один вызов модели.
    """
    if not images:
        return []
    model = _get_clip_model()
    embs  = model.encode(images, convert_to_numpy=True, normalize_embeddings=True,
                         batch_size=ENCODE_BATCH, show_progress_bar=False)
    return [emb.astype(np.float32) for emb in embs]


def _avg_embedding(embs: list[np.ndarray]) -> np.ndarray:
    if not embs:
        return np.zeros(512, dtype=np.float32)
    avg  = np.mean(embs, axis=0)
    norm = np.linalg.norm(avg)
    return (avg / (norm + 1e-9)).astype(np.float32)


def encode_text(text: str) -> np.ndarray:
    """Текст → нормализованный CLIP вектор (512-dim)."""
    model = _get_clip_model()
    emb   = model.encode([text], convert_to_numpy=True, normalize_embeddings=True)[0]
    return emb.astype(np.float32)


# ── Build / load ──────────────────────────────────────────────────────────────

def build_visual_embeddings(channel_id: str, rebuild: bool = False) -> tuple[list, np.ndarray]:
    """
    Построить visual_embeddings.npz.
    Параллельная ffmpeg-экстракция + батчевый CLIP encode.
    """
    lib_json  = get_library_json(channel_id)
    clips_dir = get_clips_dir(channel_id)
    lib_dir   = get_library_dir(channel_id)
    vis_file  = lib_dir / VIS_EMB_FILENAME

    with open(lib_json, encoding="utf-8") as f:
        library = json.load(f)

    valid_clips = [
        clip_id
        for clip_id, entry in library["clips"].items()
        if entry.get("indexed") and not entry.get("rejected")
    ]
    print(f"📸 Клипов для visual embedding: {len(valid_clips)}", flush=True)

    # Resume
    done_embs: dict[str, np.ndarray] = {}
    if vis_file.exists() and not rebuild:
        data = np.load(vis_file, allow_pickle=True)
        for cid, emb in zip(data["clip_ids"], data["embeddings"]):
            done_embs[str(cid)] = emb.astype(np.float32)
        print(f"  ↩️  Resume: {len(done_embs)} уже готово", flush=True)

    todo = [cid for cid in valid_clips if cid not in done_embs]
    print(f"  Осталось: {len(todo)} клипов", flush=True)
    if not todo:
        ids = [c for c in valid_clips if c in done_embs]
        return ids, np.array([done_embs[i] for i in ids], dtype=np.float32)

    # Грузим модель заранее
    _get_clip_model()

    total_batches = max(1, (len(todo) + BATCH_SIZE - 1) // BATCH_SIZE)

    for b_idx in range(total_batches):
        batch_ids = todo[b_idx * BATCH_SIZE : (b_idx + 1) * BATCH_SIZE]
        if not batch_ids:
            break

        # 1. Параллельная ffmpeg-экстракция кадров для всего батча
        clip_frames: dict[str, list] = {}

        def _extract_for_clip(cid):
            p = clips_dir / f"{cid}.mp4"
            if not p.exists():
                return cid, []
            try:
                return cid, extract_frames_parallel(p)
            except Exception:
                return cid, []

        with ThreadPoolExecutor(max_workers=FFMPEG_WORKERS) as ex:
            futures = {ex.submit(_extract_for_clip, cid): cid for cid in batch_ids}
            for fut in as_completed(futures):
                cid, frames = fut.result()
                clip_frames[cid] = frames

        # 2. Собрать все изображения батча + маппинг обратно к клипам
        all_images: list = []
        clip_frame_idx: dict[str, tuple[int, int]] = {}  # cid → (start, end) в all_images

        for cid in batch_ids:
            frames = clip_frames.get(cid, [])
            start  = len(all_images)
            all_images.extend(frames)
            clip_frame_idx[cid] = (start, len(all_images))

        # 3. Батчевый CLIP encode всех изображений сразу
        if all_images:
            all_embs = encode_images_batch(all_images)
        else:
            all_embs = []

        # 4. Усреднить по клипам
        ok = fail = 0
        for cid in batch_ids:
            start, end = clip_frame_idx[cid]
            frame_embs = all_embs[start:end] if all_embs else []
            if frame_embs:
                done_embs[cid] = _avg_embedding(frame_embs)
                ok += 1
            else:
                done_embs[cid] = np.zeros(512, dtype=np.float32)
                fail += 1

        print(
            f"  [{b_idx+1}/{total_batches}] +{ok} ok, {fail} fail  "
            f"(total {len(done_embs)})",
            flush=True,
        )
        _save(vis_file, valid_clips, done_embs)

    _save(vis_file, valid_clips, done_embs)
    print(f"✅ Visual embeddings готовы → {vis_file}", flush=True)

    ordered_ids = [cid for cid in valid_clips if cid in done_embs]
    ordered_emb = np.array([done_embs[i] for i in ordered_ids], dtype=np.float32)
    return ordered_ids, ordered_emb


def _save(vis_file: Path, ordered_ids: list, done_embs: dict) -> None:
    ids_ready = [cid for cid in ordered_ids if cid in done_embs]
    emb_ready = np.array([done_embs[i] for i in ids_ready], dtype=np.float32)
    np.savez(vis_file, clip_ids=np.array(ids_ready), embeddings=emb_ready)


def load_visual_embeddings(channel_id: str) -> tuple[list, np.ndarray] | None:
    """Загрузить visual_embeddings.npz. Возвращает (clip_ids, embeddings) или None."""
    vis_file = get_library_dir(channel_id) / VIS_EMB_FILENAME
    if not vis_file.exists():
        return None
    try:
        data = np.load(vis_file, allow_pickle=True)
        ids  = [str(x) for x in data["clip_ids"]]
        embs = data["embeddings"].astype(np.float32)
        print(f"✅ Visual embeddings: {len(ids)} клипов (512-dim CLIP)", flush=True)
        return ids, embs
    except Exception as e:
        print(f"⚠ visual_embeddings.npz load error: {e}", flush=True)
        return None


# ── CLI ───────────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--channel", default="channel_001_cosmos_de")
    parser.add_argument("--rebuild", action="store_true")
    args = parser.parse_args()
    build_visual_embeddings(args.channel, rebuild=args.rebuild)
