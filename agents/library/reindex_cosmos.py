"""
reindex_cosmos.py — Единый индексатор библиотеки (single-pass per clip).

АРХИТЕКТУРА — один ffmpeg-прогон → три результата:
  ffmpeg извлекает 2 кадра (0.5s + 4.5s) для каждого клипа
        │
        ├─→ /analyze_batch4_frames → keywords (Qwen)
        ├─→ CLIP ViT-B-32          → visual embedding 512-dim
        └─→ DCT pHash              → 64-bit int

РЕЖИМЫ:
  --scan          Только FFprobe-скан 5598 клипов → чистый library.json
                  Без GPU, без Qwen. Время: ~3-5 мин.

  --test N        Scan + unified-индекс первых N клипов.
                  Детальный отчёт качества keywords. (дефолт: 20)

  --full          Scan + полный unified-индекс всех клипов.
                  Overnight. Сохраняет library.json + visual_embeddings.npz.

  --resume        Продолжить с места остановки (не делать --scan заново).
                  Использовать только вместе с --full.

  --channel X     Канал (дефолт: channel_001_cosmos_de)

ПОСЛЕ ЗАВЕРШЕНИЯ:
  py -c "from agents.library.clip_selector import build_library_embeddings; \\
         build_library_embeddings('channel_001_cosmos_de')"
"""

import argparse
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

import numpy as np
import requests
from PIL import Image

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
if hasattr(sys.stderr, "reconfigure"):
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")

_utils_dir = Path(__file__).resolve().parent.parent / "utils"
if str(_utils_dir) not in sys.path:
    sys.path.insert(0, str(_utils_dir))

from paths import get_library_dir, get_clips_dir, get_library_json, get_niche  # noqa: E402

# ── Константы ──────────────────────────────────────────────────────────────────

FRAME_SIZE      = 274        # px для Qwen (CLIP сам ресайзит)
FRAME_POSITIONS = [0.5, 4.5] # 2 кадра на клип: начало + конец
PHASH_SIZE      = 32         # размер для DCT pHash
CLIP_MODEL_NAME = "clip-ViT-B-32"
BATCH_SIZE      = 4          # клипов за 1 Qwen inference
FFMPEG_WORKERS  = 8          # параллельных ffmpeg для scan-фазы
SAVE_EVERY      = 100        # чекпоинт каждые N клипов
BLACK_THRESH    = 5.0        # mean-порог (ниже = чёрный экран)
BLACK_MAX       = 30         # max-пиксель (ниже = чёрный экран)
FROZEN_DIFF     = 0.5        # diff между кадрами (ниже = frozen)
VISION_SERVERS  = ["http://localhost:8765", "http://localhost:8766"]

# ── CLIP модель (загружается один раз) ────────────────────────────────────────

_clip_model = None


def _get_clip():
    global _clip_model
    if _clip_model is None:
        from sentence_transformers import SentenceTransformer
        _clip_model = SentenceTransformer(CLIP_MODEL_NAME)
        print(f"  [CLIP] Модель загружена: {CLIP_MODEL_NAME}", flush=True)
    return _clip_model


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
    """Атомарное сохранение + автоматический датированный бэкап каждые 500 клипов."""
    lib["indexed_at"] = datetime.now(timezone.utc).isoformat()
    lib.setdefault("photos", {})
    lib["total"] = len(lib["clips"]) + len(lib["photos"])

    tmp = lib_json.with_suffix(".tmp")
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(lib, f, ensure_ascii=False, indent=2)
    tmp.replace(lib_json)

    # Авто-бэкап каждые 500 проиндексированных клипов
    indexed_count = sum(1 for c in lib["clips"].values() if c.get("indexed"))
    if indexed_count > 0 and indexed_count % 500 == 0:
        date_str = datetime.now().strftime("%Y%m%d_%H%M%S")
        bak = lib_json.with_name(f"library.json.bak.{date_str}")
        shutil.copy2(lib_json, bak)
        # Оставить только 3 последних бэкапа
        baks = sorted(lib_json.parent.glob("library.json.bak.*"))
        for old in baks[:-3]:
            try:
                old.unlink()
            except Exception:
                pass


def _save_visual_embeddings(lib_dir: Path, buf: dict[str, np.ndarray]) -> None:
    """Сохранить visual_embeddings.npz из буфера {clip_id: emb}."""
    if not buf:
        return
    ids  = sorted(buf.keys())
    embs = np.array([buf[i] for i in ids], dtype=np.float32)
    vis_file = lib_dir / "visual_embeddings.npz"
    tmp_file = lib_dir / "visual_embeddings.tmp.npz"
    np.savez(tmp_file, clip_ids=np.array(ids), embeddings=embs)
    # Windows: удалить dest перед rename
    if vis_file.exists():
        vis_file.unlink()
    tmp_file.rename(vis_file)


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
        w   = int(vs.get("width",  0))
        h   = int(vs.get("height", 0))
        fps_raw = vs.get("r_frame_rate", "25/1")
        try:
            a, b = fps_raw.split("/")
            fps = round(float(a) / float(b), 3)
        except Exception:
            fps = 25.0
        return {"duration": round(dur, 3), "width": w, "height": h, "fps": fps}
    except Exception:
        return None


# ── Frame extraction ──────────────────────────────────────────────────────────

def extract_frames(clip_path: Path, tmp_dir: Path) -> list[Path]:
    """
    Извлечь 2 кадра (0.5s + 4.5s) в tmp_dir.
    Возвращает список Path к PNG-файлам (может быть пустым).
    """
    stem   = clip_path.stem
    frames = []
    vf     = (
        f"scale={FRAME_SIZE}:{FRAME_SIZE}:"
        f"force_original_aspect_ratio=decrease,"
        f"pad={FRAME_SIZE}:{FRAME_SIZE}:(ow-iw)/2:(oh-ih)/2"
    )
    for i, ss in enumerate(FRAME_POSITIONS):
        out = tmp_dir / f"{stem}_f{i}.jpg"
        r = subprocess.run(
            ["ffmpeg", "-y", "-ss", str(ss), "-i", str(clip_path),
             "-vframes", "1", "-vf", vf, "-q:v", "2", "-loglevel", "error", str(out)],
            capture_output=True, timeout=30,
        )
        if r.returncode == 0 and out.exists() and out.stat().st_size > 0:
            frames.append(out)
    return frames


def _load_pil(path: Path) -> Image.Image | None:
    try:
        return Image.open(path).convert("RGB")
    except Exception:
        return None


# ── Quality checks (same logic as vision_server) ──────────────────────────────

def _is_black(img: Image.Image) -> bool:
    arr      = np.array(img).astype(float)
    mean_val = arr.mean()
    max_val  = arr.max()
    return mean_val < BLACK_THRESH and max_val < BLACK_MAX


def _is_frozen(frames: list[Image.Image]) -> bool:
    if len(frames) < 2:
        return False
    diff = float(abs(
        np.array(frames[0]).astype(float) -
        np.array(frames[-1]).astype(float)
    ).mean())
    return diff < FROZEN_DIFF


# ── pHash ──────────────────────────────────────────────────────────────────────

def compute_phash(img: Image.Image) -> int:
    """
    DCT-based pHash (64-bit int).
    Принимает PIL Image, возвращает 64-битный int.
    Два клипа идентичны если hamming_distance < 12.
    """
    from scipy.fft import dct as _dct
    gray   = img.convert("L").resize((PHASH_SIZE, PHASH_SIZE), Image.LANCZOS)
    pixels = np.array(gray, dtype=float)
    d      = _dct(_dct(pixels, axis=0, norm="ortho"), axis=1, norm="ortho")
    patch  = d[:8, :8].flatten()
    median = np.median(patch)
    bits   = patch > median
    return int(sum(int(b) << i for i, b in enumerate(bits)))


# ── CLIP embedding ─────────────────────────────────────────────────────────────

def compute_clip_embedding(images: list[Image.Image]) -> np.ndarray:
    """
    Список PIL Images → 512-dim нормализованный вектор.
    Среднее по всем кадрам клипа.
    """
    model = _get_clip()
    embs  = model.encode(images, convert_to_numpy=True, normalize_embeddings=True,
                         batch_size=64, show_progress_bar=False)
    avg  = np.mean(embs, axis=0)
    norm = np.linalg.norm(avg)
    return (avg / (norm + 1e-9)).astype(np.float32)


# ── Vision servers ────────────────────────────────────────────────────────────

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


def call_analyze_batch4_frames(
    batch_frame_paths: list[list[str]],
    server_url: str,
) -> dict:
    """
    POST /analyze_batch4_frames
    batch_frame_paths: [["/tmp/0001_f0.jpg", "/tmp/0001_f1.jpg"], ...]
    Возвращает {"clip1": {"keywords": "...", "valid": true}, ...}
    """
    try:
        r = requests.post(
            f"{server_url}/analyze_batch4_frames",
            json={"clips": batch_frame_paths},
            timeout=120,
        )
        r.raise_for_status()
        return r.json()
    except Exception as e:
        print(f"  [ERR] /analyze_batch4_frames: {e}", flush=True)
        return {}


# ── Phase 1: Scan ─────────────────────────────────────────────────────────────

def scan_phase(channel_id: str) -> dict:
    """
    Сканировать все .mp4 в clips/, собрать чистый library.json с indexed=False.
    Параллельный FFprobe (16 воркеров). Быстро: ~3-5 мин на 5598 клипов.
    """
    clips_dir = get_clips_dir(channel_id)
    lib_json  = get_library_json(channel_id)

    mp4_files = sorted(clips_dir.glob("*.mp4"))
    total     = len(mp4_files)
    print(f"\n[SCAN] Найдено .mp4: {total}", flush=True)

    # Бэкап старого library.json
    if lib_json.exists():
        date_str = datetime.now().strftime("%Y%m%d_%H%M%S")
        bak = lib_json.with_name(f"library.json.bak.{date_str}")
        shutil.copy2(lib_json, bak)
        # Оставить 3 последних
        for old in sorted(lib_json.parent.glob("library.json.bak.*"))[:-3]:
            try:
                old.unlink()
            except Exception:
                pass
        print(f"[SCAN] Бэкап → {bak.name}", flush=True)

    lib    = {"total": 0, "indexed_at": "", "clips": {}, "photos": {}}
    _lock  = threading.Lock()
    done   = [0]
    errors = [0]
    t0     = time.time()

    def probe_one(mp4: Path) -> tuple[str, dict | None]:
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
                    errors[0] += 1
                if done[0] % 500 == 0 or done[0] == total:
                    print(f"  [SCAN] {done[0]}/{total} ffprobe...", flush=True)

    elapsed = time.time() - t0
    print(f"\n[SCAN] ✅ {len(lib['clips'])} клипов  ошибок: {errors[0]}  за {elapsed:.0f}s", flush=True)
    _save_lib(lib, lib_json)
    print(f"[SCAN] Сохранено → {lib_json}", flush=True)
    return lib


# ── Phase 2: Unified indexer ──────────────────────────────────────────────────

def index_clips(
    channel_id: str,
    limit: int | None = None,
    verbose: bool = False,
) -> dict:
    """
    Единый индексатор: single ffmpeg pass → pHash + CLIP + Qwen keywords.

    Параметры:
      limit   — ограничить N клипами (для теста)
      verbose — подробный вывод keywords

    Возвращает статистику.
    """
    lib_dir   = get_library_dir(channel_id)
    clips_dir = get_clips_dir(channel_id)
    lib_json  = get_library_json(channel_id)
    niche     = get_niche(channel_id)

    lib = _load_lib(lib_json)
    if not lib["clips"]:
        print("[INDEX] library.json пустой — сначала запусти --scan", flush=True)
        return {}

    # Клипы для индексации
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

    # Проверить Qwen серверы
    servers = get_available_servers()
    if not servers:
        print(
            "[INDEX] ⚠️  Нет доступных Vision серверов!\n"
            "  Запусти: py -X utf8 agents/vision/vision_server.py --port 8765",
            flush=True,
        )
        return {}

    print(f"[INDEX] Vision серверов: {len(servers)}/2", flush=True)

    # Загрузить CLIP модель заранее
    _get_clip()

    # Загрузить существующие visual embeddings (resume)
    vis_file = lib_dir / "visual_embeddings.npz"
    emb_buf: dict[str, np.ndarray] = {}
    if vis_file.exists():
        try:
            data = np.load(vis_file, allow_pickle=True)
            for cid, emb in zip(data["clip_ids"], data["embeddings"]):
                emb_buf[str(cid)] = emb.astype(np.float32)
            data.close()  # Закрыть файловый дескриптор (важно для Windows)
            print(f"[INDEX] Resume visual embeddings: {len(emb_buf)} уже готово", flush=True)
        except Exception:
            pass

    stats = {"ok": 0, "rejected": 0, "error": 0}
    t_start = time.time()
    processed = 0

    with tempfile.TemporaryDirectory(prefix="reindex_cosmos_") as tmp_str:
        tmp_dir = Path(tmp_str)

        for batch_start in range(0, len(todo), BATCH_SIZE):
            batch_ids = todo[batch_start: batch_start + BATCH_SIZE]
            server    = servers[batch_start // BATCH_SIZE % len(servers)]

            # ── Шаг 1: Извлечь кадры параллельно ─────────────────────────────
            frame_map: dict[str, list[Path]] = {}  # cid → [frame_path, ...]

            def _extract(cid: str) -> tuple[str, list[Path]]:
                p = clips_dir / f"{cid}.mp4"
                if not p.exists():
                    return cid, []
                return cid, extract_frames(p, tmp_dir)

            with ThreadPoolExecutor(max_workers=min(BATCH_SIZE, 4)) as ex:
                futures = {ex.submit(_extract, cid): cid for cid in batch_ids}
                for fut in as_completed(futures):
                    cid, fps = fut.result()
                    frame_map[cid] = fps

            # ── Шаг 2: Качество кадров + pHash + CLIP ─────────────────────────
            valid_cids:   list[str]              = []  # для Qwen
            batch_frames: list[list[str]]        = []  # пути кадров для /analyze_batch4_frames
            reject_map:   dict[str, str]         = {}

            for cid in batch_ids:
                fps = frame_map.get(cid, [])
                entry = lib["clips"][cid]

                if not fps:
                    reject_map[cid] = "no_frames"
                    continue

                pil_imgs = [_load_pil(p) for p in fps]
                pil_imgs = [im for im in pil_imgs if im is not None]

                if not pil_imgs:
                    reject_map[cid] = "pil_load_error"
                    continue

                # Black / frozen check
                if _is_black(pil_imgs[0]):
                    reject_map[cid] = "black_screen"
                    continue
                if _is_frozen(pil_imgs):
                    reject_map[cid] = "frozen"
                    continue

                # pHash (из первого кадра)
                try:
                    entry["phash"] = compute_phash(pil_imgs[0])
                except Exception:
                    pass  # pHash некритичен

                # CLIP embedding
                try:
                    emb = compute_clip_embedding(pil_imgs)
                    emb_buf[cid] = emb
                except Exception as e:
                    print(f"  [CLIP ERR] {cid}: {e}", flush=True)

                valid_cids.append(cid)
                batch_frames.append([str(p) for p in fps])

            # ── Шаг 3: Qwen vision → keywords ─────────────────────────────────
            qwen_results: dict = {}
            if valid_cids:
                raw = call_analyze_batch4_frames(batch_frames, server)
                for i, cid in enumerate(valid_cids):
                    key = f"clip{i+1}"
                    qwen_results[cid] = raw.get(key, {})

            # ── Шаг 4: Записать результаты в lib ──────────────────────────────
            for cid in batch_ids:
                entry = lib["clips"][cid]

                if cid in reject_map:
                    entry["indexed"]       = True
                    entry["rejected"]      = True
                    entry["reject_reason"] = reject_map[cid]
                    stats["rejected"] += 1
                    if verbose:
                        print(f"  ❌ {cid}: {reject_map[cid]}", flush=True)
                    continue

                r = qwen_results.get(cid, {})
                kw = r.get("keywords", "").strip()

                if r.get("valid") and kw:
                    entry["indexed"]  = True
                    entry["rejected"] = False
                    entry["keywords"] = kw
                    stats["ok"] += 1
                    if verbose:
                        print(f"  ✅ {cid}: {kw}", flush=True)
                else:
                    # Кадры ОК, но Qwen ничего не вернул — помечаем rejected
                    reason = r.get("reason", "no_response") if r else "no_response"
                    entry["indexed"]       = True
                    entry["rejected"]      = True
                    entry["reject_reason"] = reason
                    # Удалить из emb_buf — rejected не нужен
                    emb_buf.pop(cid, None)
                    stats["rejected"] += 1
                    if verbose:
                        print(f"  ⚠  {cid}: Qwen → {reason}", flush=True)

            processed += len(batch_ids)

            # ── Чекпоинт ──────────────────────────────────────────────────────
            if processed % SAVE_EVERY == 0 or processed == len(todo):
                _save_lib(lib, lib_json)
                _save_visual_embeddings(lib_dir, emb_buf)
                elapsed = time.time() - t_start
                rate    = processed / elapsed * 3600 if elapsed > 0 else 0
                eta_s   = (len(todo) - processed) / (processed / elapsed) if processed > 0 and elapsed > 0 else 0
                eta_h   = eta_s / 3600
                print(
                    f"  [{processed}/{len(todo)}] "
                    f"ok={stats['ok']} rej={stats['rejected']} "
                    f"rate={rate:.0f}/hr  ETA={eta_h:.1f}h",
                    flush=True,
                )

    # Финальное сохранение
    _save_lib(lib, lib_json)
    _save_visual_embeddings(lib_dir, emb_buf)

    elapsed = time.time() - t_start
    total_done = stats["ok"] + stats["rejected"]
    pct_ok = 100 * stats["ok"] // max(total_done, 1)

    print(f"\n{'='*60}", flush=True)
    print(f"[INDEX] ✅ ГОТОВО за {elapsed:.0f}s ({elapsed/3600:.1f}h)", flush=True)
    print(f"  OK:       {stats['ok']}", flush=True)
    print(f"  Rejected: {stats['rejected']}", flush=True)
    print(f"  Error:    {stats['error']}", flush=True)
    print(f"  Годных:   {pct_ok}%", flush=True)
    print(f"  Visual emb: {len(emb_buf)} клипов → {lib_dir / 'visual_embeddings.npz'}", flush=True)
    print(f"{'='*60}\n", flush=True)

    return stats


# ── Quality test report ───────────────────────────────────────────────────────

def quality_test(channel_id: str, n: int) -> None:
    """
    Запустить unified-индексатор на первых N клипах.
    Вывести подробный отчёт качества keywords.
    """
    print(f"\n{'='*60}", flush=True)
    print(f"[TEST] Quality test на {n} клипах", flush=True)
    print(f"{'='*60}\n", flush=True)

    stats = index_clips(channel_id, limit=n, verbose=True)

    if stats:
        ok  = stats.get("ok", 0)
        rej = stats.get("rejected", 0)
        total = ok + rej
        print(f"\n[TEST] ИТОГ: {ok}/{total} годных ({100*ok//max(total,1)}%)", flush=True)
        if ok / max(total, 1) >= 0.80:
            print("[TEST] ✅ Качество OK (≥80% годных) — можно запускать --full", flush=True)
        else:
            print("[TEST] ⚠️  Качество ниже 80% — проверь Qwen серверы и настройки", flush=True)


# ── Main ──────────────────────────────────────────────────────────────────────

def main() -> None:
    parser = argparse.ArgumentParser(
        description="Единый индексатор космической библиотеки (scan + pHash + CLIP + Qwen)"
    )
    parser.add_argument("--channel", default="channel_001_cosmos_de",
                        help="ID канала (дефолт: channel_001_cosmos_de)")

    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--scan",   action="store_true",
                      help="Только FFprobe-скан → чистый library.json (indexed=False)")
    mode.add_argument("--test",   type=int, metavar="N", nargs="?", const=20,
                      help="Scan + quality test на N клипах (дефолт: 20)")
    mode.add_argument("--full",   action="store_true",
                      help="Scan + полный unified-индекс всех клипов")
    mode.add_argument("--resume", action="store_true",
                      help="Продолжить индексацию с места остановки (без scan)")

    args = parser.parse_args()

    print(f"\n{'='*60}")
    print(f"  COSMOS LIBRARY REINDEX — unified single-pass indexer")
    print(f"  Channel : {args.channel}")
    mode_str = "scan" if args.scan else ("test-"+str(args.test) if args.test else ("full" if args.full else "resume"))
    print(f"  Mode    : {mode_str}")
    print(f"  Time    : {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    print(f"{'='*60}\n", flush=True)

    if args.scan:
        scan_phase(args.channel)
        print("\n[NEXT STEP]", flush=True)
        print(f"  py agents/library/reindex_cosmos.py --full --channel {args.channel}", flush=True)

    elif args.test is not None:
        scan_phase(args.channel)
        quality_test(args.channel, n=args.test)
        print("\n[NEXT STEP] Если качество ОК:", flush=True)
        print(f"  py agents/library/reindex_cosmos.py --full --channel {args.channel}", flush=True)
        print("  Потом:", flush=True)
        print(f"  py -c \"from agents.library.clip_selector import build_library_embeddings; "
              f"build_library_embeddings('{args.channel}')\"", flush=True)

    elif args.full:
        scan_phase(args.channel)
        index_clips(args.channel, limit=None, verbose=False)
        lib_json = get_library_json(args.channel)
        print("\n[NEXT STEP] Rebuild e5-large embeddings:", flush=True)
        print(f"  py -c \"from agents.library.clip_selector import build_library_embeddings; "
              f"build_library_embeddings('{args.channel}')\"", flush=True)

    elif args.resume:
        print("[RESUME] Продолжаю с места остановки...", flush=True)
        index_clips(args.channel, limit=None, verbose=False)
        print("\n[NEXT STEP] Rebuild e5-large embeddings:", flush=True)
        print(f"  py -c \"from agents.library.clip_selector import build_library_embeddings; "
              f"build_library_embeddings('{args.channel}')\"", flush=True)


if __name__ == "__main__":
    main()
