"""
reindex_bad_clips.py — автоматический реиндекс клипов с плохими описаниями.

Алгоритм:
  1. Считает CLIP text-image consistency для каждого клипа
  2. Клипы с score < THRESHOLD — подозрительные
  3. Батчами по BATCH_SIZE отправляет кадры в Vision API (1 вызов = N клипов)
  4. Обновляет library.json + E5 embeddings

Бекенды:
  --backend claude   (по умолчанию) — claude.exe, требует Claude Pro
  --backend gemini   — Gemini 2.0 Flash Lite, бесплатно 15 RPM / 1M TPD
                       Нужен GEMINI_API_KEY в config/.env или переменной среды
                       pip install google-generativeai pillow

Запуск:
  python tools/reindex_bad_clips.py                         # топ-100, claude
  python tools/reindex_bad_clips.py --top 1600 --backend gemini
  python tools/reindex_bad_clips.py --threshold 0.20
  python tools/reindex_bad_clips.py --dry-run
  python tools/reindex_bad_clips.py --ids "2322,2323"
"""
import sys, os, json, argparse, tempfile, time, subprocess, concurrent.futures
from pathlib import Path

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

# ── .env loader (для GEMINI_API_KEY) ─────────────────────────────────────────
def _load_dotenv():
    env_path = Path(__file__).parent.parent / "config" / ".env"
    if not env_path.exists():
        return
    for line in env_path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        k, _, v = line.partition("=")
        k = k.strip()
        v = v.strip()
        if k and k not in os.environ:
            os.environ[k] = v

_load_dotenv()

sys.path.insert(0, str(Path(__file__).parent.parent / "agents" / "utils"))
sys.path.insert(0, str(Path(__file__).parent.parent / "agents" / "library"))

import numpy as np

LIBRARY_DIR = Path("D:/Video-pipeline-library/library_cosmos")
CLIPS_DIR   = LIBRARY_DIR / "clips"
LIB_JSON    = LIBRARY_DIR / "library.json"
EMB_NPZ     = LIBRARY_DIR / "embeddings.npz"
VIS_NPZ     = LIBRARY_DIR / "visual_embeddings.npz"

DEFAULT_THRESHOLD = 0.20   # CLIP text-image sim ниже этого = переиндексировать
DEFAULT_TOP       = 100
FRAME_TIME        = 2.0    # секунда для извлечения кадра
BATCH_SIZE        = 12     # кадров за один вызов vision API (10-15 оптимально)
MAX_WORKERS       = 4      # параллельных ffmpeg при извлечении кадров

GEMINI_MODEL      = "gemini-1.5-flash"   # бесплатный тир: 15 RPM, 1M TPD
GEMINI_RPM_LIMIT  = 14     # безопасный лимит (бесплатный тир: 15 RPM)


VISION_PROMPT_BATCH = """I'll show you {n} video frames. For each one write a single-line description for a video stock library.

Rules:
- Max 12 words per description
- Name specific spacecraft/telescopes (James Webb Space Telescope, Hubble, Euclid, ISS, etc.)
- Note cleanroom/lab if applicable
- Note CGI/animation vs real footage
- No fluff words
- English only

Frames:
{frame_list}

Respond ONLY with a JSON array of {n} strings, one per frame, in order.
Example: ["description 1", "description 2", "description 3"]"""


def _find_claude_exe() -> str | None:
    claude_dir = Path(os.environ.get("APPDATA", "")) / "Claude/claude-code"
    if not claude_dir.exists():
        return None
    for v in sorted(claude_dir.iterdir(), reverse=True):
        exe = v / "claude.exe"
        if exe.exists():
            return str(exe)
    return None


def extract_frame(clip_id: str, t: float = FRAME_TIME) -> Path | None:
    import shutil
    if not shutil.which("ffmpeg"):
        return None
    mp4 = CLIPS_DIR / f"{clip_id}.mp4"
    if not mp4.exists():
        return None
    tmp = Path(tempfile.mktemp(suffix=f"_{clip_id}.jpg"))
    r = subprocess.run(
        ["ffmpeg", "-ss", str(t), "-i", str(mp4),
         "-vframes", "1", "-q:v", "3", "-vf", "scale=480:-1",
         str(tmp), "-y"],
        capture_output=True
    )
    return tmp if r.returncode == 0 and tmp.exists() else None


def extract_frames_parallel(clip_ids: list[str]) -> dict[str, Path | None]:
    """Параллельное извлечение кадров для батча."""
    results = {}
    with concurrent.futures.ThreadPoolExecutor(max_workers=MAX_WORKERS) as ex:
        futures = {ex.submit(extract_frame, cid): cid for cid in clip_ids}
        for fut in concurrent.futures.as_completed(futures):
            cid = futures[fut]
            results[cid] = fut.result()
    return results


def describe_batch(clip_ids: list[str], frame_paths: dict[str, Path | None]) -> dict[str, str]:
    """Один вызов claude.exe для батча из N клипов. Возвращает {clip_id: description}."""
    exe = _find_claude_exe()
    if not exe:
        return {}

    # Только клипы с успешно извлечёнными кадрами
    valid = [(cid, frame_paths[cid]) for cid in clip_ids if frame_paths.get(cid)]
    if not valid:
        return {}

    frame_list = "\n".join(f"{i+1}. {path}" for i, (_, path) in enumerate(valid))
    prompt = VISION_PROMPT_BATCH.format(n=len(valid), frame_list=frame_list)

    env = os.environ.copy()
    env.pop("CLAUDECODE", None)

    try:
        r = subprocess.run(
            [exe, "-p", "--allowedTools", "Read"],
            input=prompt,
            capture_output=True, text=True,
            encoding="utf-8", errors="replace",
            env=env, timeout=60,
        )
        output = r.stdout.strip()

        # Парсим JSON array
        import re
        m = re.search(r'\[.*\]', output, re.DOTALL)
        if not m:
            print(f"  Не удалось распарсить JSON из ответа: {output[:200]}", flush=True)
            return {}

        descriptions = json.loads(m.group())
        result = {}
        for i, (cid, _) in enumerate(valid):
            if i < len(descriptions) and descriptions[i]:
                result[cid] = str(descriptions[i]).strip().strip('"').strip("'")

        return result

    except Exception as e:
        print(f"  claude.exe batch error: {e}", flush=True)
        return {}
    finally:
        # Удаляем временные файлы
        for _, path in valid:
            if path and path.exists():
                path.unlink(missing_ok=True)


def describe_batch_gemini(clip_ids: list[str], frame_paths: dict[str, Path | None],
                          _rpm_state: list | None = None) -> dict[str, str]:
    """Gemini 2.0 Flash Lite vision — батч из N клипов. Возвращает {clip_id: description}."""
    api_key = os.environ.get("GEMINI_API_KEY", "").strip()
    if not api_key:
        print("  GEMINI_API_KEY не задан. Добавь в config/.env или env переменную.", flush=True)
        return {}

    try:
        from google import genai as _genai
        from google.genai import types as _gtypes
        from PIL import Image as PILImage
    except ImportError:
        print("  Установи: pip install google-genai pillow", flush=True)
        return {}

    valid = [(cid, frame_paths[cid]) for cid in clip_ids if frame_paths.get(cid)]
    if not valid:
        return {}

    # Rate limiting: не более GEMINI_RPM_LIMIT запросов/мин
    if _rpm_state is not None:
        now = time.time()
        while _rpm_state and now - _rpm_state[0] > 60.0:
            _rpm_state.pop(0)
        if len(_rpm_state) >= GEMINI_RPM_LIMIT:
            wait = 60.0 - (now - _rpm_state[0]) + 0.5
            if wait > 0:
                print(f"  [Gemini] Rate limit — ждём {wait:.1f}s...", flush=True)
                time.sleep(wait)
        _rpm_state.append(time.time())

    frame_list = "\n".join(f"{i+1}. (frame {i+1})" for i in range(len(valid)))
    prompt = VISION_PROMPT_BATCH.format(n=len(valid), frame_list=frame_list)

    # Загружаем изображения как PIL Image объекты
    opened_images: list = []
    valid_with_img: list = []
    for cid, path in valid:
        try:
            img = PILImage.open(str(path))
            if img.mode not in ("RGB", "L"):
                img = img.convert("RGB")
            opened_images.append(img)
            valid_with_img.append((cid, path, img))
        except Exception as e:
            print(f"  Не удалось открыть {path}: {e}", flush=True)

    if not valid_with_img:
        return {}

    # Собираем contents: [text_prompt, img1, img2, ...]
    contents: list = [prompt] + [img for _, _, img in valid_with_img]

    try:
        client = _genai.Client(api_key=api_key)
        response = client.models.generate_content(
            model=GEMINI_MODEL,
            contents=contents,
        )
        output = response.text.strip()
    except Exception as e:
        print(f"  Gemini API error: {e}", flush=True)
        return {}
    finally:
        for img in opened_images:
            try:
                img.close()
            except Exception:
                pass
        for _, path in valid:
            if path and path.exists():
                path.unlink(missing_ok=True)

    import re
    m = re.search(r'\[.*\]', output, re.DOTALL)
    if not m:
        print(f"  Не удалось распарсить JSON из ответа Gemini: {output[:200]}", flush=True)
        return {}

    try:
        descriptions = json.loads(m.group())
    except json.JSONDecodeError as e:
        print(f"  JSON decode error: {e} | raw: {output[:200]}", flush=True)
        return {}

    result = {}
    for i, (cid, _, _img) in enumerate(valid_with_img):
        if i < len(descriptions) and descriptions[i]:
            result[cid] = str(descriptions[i]).strip().strip('"').strip("'")
    return result


def compute_clip_consistency(vis_ids, vis_embs, keywords_list) -> np.ndarray:
    """CLIP text embedding для keywords, затем cosine с visual."""
    from sentence_transformers import SentenceTransformer
    print("Загружаем CLIP модель для text embedding...", flush=True)
    model = SentenceTransformer("clip-ViT-L-14")

    batch = 128
    text_embs = []
    for i in range(0, len(keywords_list), batch):
        chunk = [k if k else "empty" for k in keywords_list[i:i+batch]]
        vecs = model.encode(chunk, normalize_embeddings=True,
                            convert_to_numpy=True, show_progress_bar=False)
        text_embs.append(vecs)
        if i % 1000 == 0 and i > 0:
            print(f"  text embed: {i}/{len(keywords_list)}", flush=True)

    text_embs = np.vstack(text_embs).astype(np.float32)
    sims = np.einsum("ij,ij->i", vis_embs, text_embs)
    return sims


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--top",       type=int,   default=DEFAULT_TOP)
    parser.add_argument("--threshold", type=float, default=DEFAULT_THRESHOLD)
    parser.add_argument("--dry-run",   action="store_true",
                        help="Только покажи что будет переиндексировано, не меняй")
    parser.add_argument("--ids",       type=str,   default="",
                        help="Принудительно переиндексировать конкретные ID (через запятую)")
    parser.add_argument("--backend",   type=str,   default="claude",
                        choices=["claude", "gemini"],
                        help="Vision backend: claude (default, требует Claude Pro) "
                             "или gemini (Gemini 2.0 Flash Lite, бесплатно)")
    args = parser.parse_args()

    os.chdir(Path(__file__).parent.parent)

    # ── Загрузка ──────────────────────────────────────────────────────────────
    print("Загружаем библиотеку...", flush=True)
    with open(LIB_JSON, encoding="utf-8") as f:
        lib_data = json.load(f)
    clips = lib_data["clips"]

    print("Загружаем visual embeddings...", flush=True)
    vd = np.load(VIS_NPZ, allow_pickle=True)
    vis_ids  = [str(x) for x in vd["clip_ids"]]
    vis_embs = vd["embeddings"].astype(np.float32)

    print("Загружаем E5 embeddings...", flush=True)
    ed = np.load(EMB_NPZ, allow_pickle=True)
    e5_ids   = [str(x) for x in ed["clip_ids"]]
    e5_embs  = list(ed["embeddings"])
    e5_idx   = {cid: i for i, cid in enumerate(e5_ids)}

    # ── Выбор клипов для переиндексирования ───────────────────────────────────
    if args.ids:
        target_ids = [x.strip().zfill(4) for x in args.ids.split(",")]
        print(f"\nПринудительный реиндекс {len(target_ids)} клипов: {target_ids}", flush=True)
    else:
        keywords_list = [clips.get(cid, {}).get("keywords", "") or "" for cid in vis_ids]
        sims = compute_clip_consistency(vis_ids, vis_embs, keywords_list)

        # Клипы ниже threshold
        bad_mask = sims < args.threshold
        bad_indices = np.where(bad_mask)[0]
        # Сортируем по возрастанию similarity (худшие первые)
        bad_indices = bad_indices[np.argsort(sims[bad_indices])][:args.top]

        target_ids = [vis_ids[i] for i in bad_indices]
        target_sims = {vis_ids[i]: sims[i] for i in bad_indices}

        print(f"\nНайдено {len(bad_indices)} подозрительных клипов "
              f"(CLIP sim < {args.threshold})", flush=True)

        print("\nПримеры (самые плохие):")
        for cid in target_ids[:10]:
            kw = clips.get(cid, {}).get("keywords", "")
            print(f"  [{cid}] sim={target_sims[cid]:.3f}  {str(kw)[:70]}")

    if args.dry_run:
        print(f"\n[DRY RUN] Было бы переиндексировано: {len(target_ids)} клипов")
        print("Запусти без --dry-run чтобы применить.")
        return

    if not target_ids:
        print("Нет клипов для реиндексирования.")
        return

    # ── Проверяем бекенд ──────────────────────────────────────────────────────
    if args.backend == "claude":
        if not _find_claude_exe():
            print("claude.exe не найден в %APPDATA%/Claude/claude-code/")
            sys.exit(1)
        print(f"🤖 Бекенд: claude.exe", flush=True)
        _describe_fn = lambda ids, fps, **kw: describe_batch(ids, fps)
        _rpm_state = None
    else:
        api_key = os.environ.get("GEMINI_API_KEY", "").strip()
        if not api_key:
            print("❌ GEMINI_API_KEY не задан. Добавь в config/.env:")
            print("   GEMINI_API_KEY=ваш_ключ")
            print("   Получить бесплатно: https://aistudio.google.com/")
            sys.exit(1)
        print(f"✨ Бекенд: Gemini {GEMINI_MODEL} (бесплатный тир)", flush=True)
        _rpm_state = []
        _describe_fn = lambda ids, fps, **kw: describe_batch_gemini(ids, fps, _rpm_state)

    fixed   = []   # (clip_id, old_kw, new_kw)
    skipped = []

    total   = len(target_ids)
    done    = 0
    t_start = time.time()

    print(f"\nПереиндексируем {total} клипов батчами по {BATCH_SIZE}...\n", flush=True)

    for batch_start in range(0, total, BATCH_SIZE):
        batch_ids = target_ids[batch_start:batch_start + BATCH_SIZE]

        # Параллельно извлекаем кадры
        frame_paths = extract_frames_parallel(batch_ids)

        # Один вызов vision API на весь батч
        descriptions = _describe_fn(batch_ids, frame_paths)

        for clip_id in batch_ids:
            done += 1
            old_kw = clips.get(clip_id, {}).get("keywords", "") or ""
            sim_str = f" sim={target_sims[clip_id]:.3f}" if not args.ids and clip_id in target_sims else ""

            if clip_id not in descriptions:
                print(f"[{done}/{total}] [{clip_id}]{sim_str} ПРОПУЩЕН", flush=True)
                skipped.append(clip_id)
                continue

            new_kw = descriptions[clip_id]
            print(f"[{done}/{total}] [{clip_id}]{sim_str}", flush=True)
            print(f"  было:  {old_kw[:80]}", flush=True)
            print(f"  стало: {new_kw[:80]}", flush=True)

            if clip_id in clips:
                clips[clip_id]["keywords"] = new_kw
            fixed.append((clip_id, old_kw, new_kw))

        elapsed   = time.time() - t_start
        rate      = done / elapsed * 60 if elapsed > 0 else 0
        remaining = (total - done) / (rate / 60) / 60 if rate > 0 else 0
        print(f"  -- батч {batch_start//BATCH_SIZE + 1}/{(total+BATCH_SIZE-1)//BATCH_SIZE} | "
              f"{done}/{total} клипов | {rate:.0f} кл/мин | осталось ~{remaining:.0f} мин\n", flush=True)

    if not fixed:
        print("\nНечего сохранять.")
        return

    # ── Сохраняем library.json ────────────────────────────────────────────────
    print(f"\nСохраняем library.json ({len(fixed)} изменений)...", flush=True)
    with open(LIB_JSON, "w", encoding="utf-8") as f:
        json.dump(lib_data, f, ensure_ascii=False, indent=2)

    # ── Пересчитываем E5 embeddings ───────────────────────────────────────────
    print("Пересчитываем E5 embeddings...", flush=True)
    from clip_selector import encode_passage

    for clip_id, _, new_kw in fixed:
        idx = e5_idx.get(clip_id)
        if idx is not None:
            e5_embs[idx] = encode_passage(new_kw)
            print(f"  [{clip_id}] E5 обновлён", flush=True)

    np.savez_compressed(EMB_NPZ,
        clip_ids=np.array(e5_ids),
        embeddings=np.array(e5_embs, dtype=np.float32))
    print("embeddings.npz сохранён", flush=True)

    # ── Итог ──────────────────────────────────────────────────────────────────
    print(f"\n{'='*50}")
    print(f"ГОТОВО:")
    print(f"  Исправлено: {len(fixed)}")
    print(f"  Пропущено:  {len(skipped)}")
    print(f"\nИсправленные клипы:")
    for clip_id, old_kw, new_kw in fixed:
        print(f"  [{clip_id}]")
        print(f"    было:  {old_kw[:70]}")
        print(f"    стало: {new_kw[:70]}")

    # Лог в файл
    log_path = LIBRARY_DIR / "reindex_log.jsonl"
    with open(log_path, "a", encoding="utf-8") as lf:
        for clip_id, old_kw, new_kw in fixed:
            lf.write(json.dumps({
                "clip_id": clip_id,
                "old": old_kw,
                "new": new_kw,
            }, ensure_ascii=False) + "\n")
    print(f"\nЛог: {log_path}")


if __name__ == "__main__":
    main()
