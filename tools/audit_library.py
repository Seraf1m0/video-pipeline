"""
audit_library.py — визуальный аудит библиотеки клипов.

Режимы:
  --mode suspicious   Клипы где CLIP-картинка не совпадает с текстом (Qwen ошибся)
  --mode gallery      Вся библиотека постранично (--page N, --per-page 100)
  --mode search       Найти клипы по текстовому запросу (--query "...")
  --mode clip         Показать конкретный клип (--id 2322)

Открывает HTML в браузере.

python tools/audit_library.py --mode suspicious --top 100
python tools/audit_library.py --mode gallery --page 3
python tools/audit_library.py --mode search --query "Euclid telescope"
python tools/audit_library.py --mode clip --id 2322,2323,2325
"""
import sys, os, json, argparse, base64, webbrowser, tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent / "agents" / "utils"))
sys.path.insert(0, str(Path(__file__).parent.parent / "agents" / "library"))

import numpy as np

LIBRARY_DIR  = Path("D:/Video-pipeline-library/library_cosmos")
CLIPS_DIR    = LIBRARY_DIR / "clips"
LIB_JSON     = LIBRARY_DIR / "library.json"
EMB_NPZ      = LIBRARY_DIR / "embeddings.npz"
VIS_NPZ      = LIBRARY_DIR / "visual_embeddings.npz"

THUMB_SIZE   = 200   # px ширина превью


# ── helpers ───────────────────────────────────────────────────────────────────

def load_library():
    with open(LIB_JSON, encoding="utf-8") as f:
        return json.load(f)["clips"]

def load_e5():
    d = np.load(EMB_NPZ, allow_pickle=True)
    ids  = [str(x) for x in d["clip_ids"]]
    embs = d["embeddings"].astype(np.float32)
    return ids, embs

def load_clip_visual():
    d = np.load(VIS_NPZ, allow_pickle=True)
    ids  = [str(x) for x in d["clip_ids"]]
    embs = d["embeddings"].astype(np.float32)
    return ids, embs

def frame_b64(clip_id: str, t: float = 2.0) -> str:
    """Извлечь кадр из клипа, вернуть base64 JPEG."""
    import subprocess, shutil
    if not shutil.which("ffmpeg"):
        return ""
    mp4 = CLIPS_DIR / f"{clip_id}.mp4"
    if not mp4.exists():
        return ""
    tmp = tempfile.NamedTemporaryFile(suffix=".jpg", delete=False)
    tmp.close()
    subprocess.run(
        ["ffmpeg", "-ss", str(t), "-i", str(mp4),
         "-vframes", "1", "-q:v", "3", tmp.name, "-y"],
        capture_output=True
    )
    try:
        with open(tmp.name, "rb") as fh:
            b64 = base64.b64encode(fh.read()).decode()
        os.unlink(tmp.name)
        return b64
    except Exception:
        return ""

def clip_card(clip_id: str, keywords: str, extra: str = "", b64: str = "") -> str:
    img_src = f"data:image/jpeg;base64,{b64}" if b64 else ""
    img_tag = f'<img src="{img_src}" style="width:{THUMB_SIZE}px;height:{THUMB_SIZE*9//16}px;object-fit:cover;border-radius:4px;">' if img_src else f'<div style="width:{THUMB_SIZE}px;height:{THUMB_SIZE*9//16}px;background:#1a1a2e;border-radius:4px;display:flex;align-items:center;justify-content:center;color:#444;">no frame</div>'
    return f"""
<div class="card">
  {img_tag}
  <div class="cid">{clip_id}</div>
  <div class="kw" title="{keywords}">{keywords[:90]}{"…" if len(keywords)>90 else ""}</div>
  {f'<div class="extra">{extra}</div>' if extra else ""}
</div>"""

HTML_STYLE = """
<style>
  body { background:#0d0d1a; color:#dde; font-family:sans-serif; margin:0; padding:16px; }
  h2   { color:#7af; margin-bottom:8px; }
  .info { color:#888; font-size:12px; margin-bottom:16px; }
  .grid { display:flex; flex-wrap:wrap; gap:12px; }
  .card { background:#1a1a2e; border-radius:8px; padding:8px; width:220px; }
  .cid  { font-weight:bold; color:#7af; font-size:13px; margin-top:6px; }
  .kw   { font-size:11px; color:#aac; line-height:1.3; margin-top:3px; }
  .extra{ font-size:11px; color:#fa8; margin-top:3px; }
  .pager{ margin-top:20px; color:#888; font-size:13px; }
</style>"""

def render_html(title: str, info: str, cards: list[str]) -> str:
    body = "\n".join(cards)
    return f"""<!DOCTYPE html><html><head><meta charset="utf-8"><title>{title}</title>{HTML_STYLE}</head>
<body><h2>{title}</h2><div class="info">{info}</div>
<div class="grid">{body}</div></body></html>"""

def open_html(html: str):
    tmp = tempfile.NamedTemporaryFile(suffix=".html", delete=False, mode="w", encoding="utf-8")
    tmp.write(html)
    tmp.close()
    webbrowser.open(f"file://{tmp.name}")
    print(f"Открыто: {tmp.name}")


# ── modes ─────────────────────────────────────────────────────────────────────

def mode_suspicious(top_n: int = 100):
    """Найти клипы где CLIP-картинка не совпадает с текстом keywords."""
    from visual_embedder import encode_text

    print("Загружаем visual embeddings...", flush=True)
    vis_ids, vis_embs = load_clip_visual()
    vis_idx = {cid: i for i, cid in enumerate(vis_ids)}

    lib = load_library()

    print(f"Считаем CLIP text-image consistency для {len(vis_ids)} клипов...", flush=True)

    # Батчевый encode текста через CLIP
    from sentence_transformers import SentenceTransformer
    model = SentenceTransformer("clip-ViT-L-14")

    clip_ids_list  = vis_ids
    keywords_list  = [lib.get(cid, {}).get("keywords", "") or "" for cid in clip_ids_list]

    # Encode текстов батчами
    batch = 64
    text_embs = []
    for i in range(0, len(keywords_list), batch):
        batch_kw = keywords_list[i:i+batch]
        vecs = model.encode(
            ["" if not k else k for k in batch_kw],
            normalize_embeddings=True, convert_to_numpy=True, show_progress_bar=False
        )
        text_embs.append(vecs)
        if i % 500 == 0:
            print(f"  {i}/{len(keywords_list)}", flush=True)
    text_embs = np.vstack(text_embs).astype(np.float32)

    # Cosine sim между visual и text CLIP
    sims = np.einsum("ij,ij->i", vis_embs, text_embs)

    # Топ-N самых подозрительных (низкий similarity)
    order = np.argsort(sims)[:top_n]

    print(f"Генерируем превью для {len(order)} подозрительных клипов...", flush=True)
    cards = []
    for idx in order:
        cid  = clip_ids_list[idx]
        kw   = keywords_list[idx]
        sim  = sims[idx]
        b64  = frame_b64(cid)
        cards.append(clip_card(cid, kw, f"CLIP consistency: {sim:.3f}", b64))

    html = render_html(
        f"Подозрительные клипы (низкий CLIP text-image sim)",
        f"Топ-{top_n} клипов где Qwen, возможно, написал неправильное описание. "
        f"Низкий score = картинка не совпадает с текстом.",
        cards
    )
    open_html(html)


def mode_gallery(page: int = 1, per_page: int = 80):
    """Вся библиотека постранично."""
    lib  = load_library()
    all_ids = sorted(lib.keys())
    total = len(all_ids)
    start = (page - 1) * per_page
    end   = min(start + per_page, total)
    chunk = all_ids[start:end]

    print(f"Генерируем галерею стр.{page} ({start+1}-{end} из {total})...", flush=True)
    cards = []
    for cid in chunk:
        kw  = lib.get(cid, {}).get("keywords", "") or ""
        b64 = frame_b64(cid)
        cards.append(clip_card(cid, kw, "", b64))

    html = render_html(
        f"Галерея библиотеки — страница {page}",
        f"Клипы {start+1}–{end} из {total}. "
        f"Для другой страницы: --page N  (всего страниц: {(total+per_page-1)//per_page})",
        cards
    )
    open_html(html)


def mode_search(query: str, top_n: int = 40):
    """Найти клипы по E5 текстовому запросу."""
    from clip_selector import encode_query

    lib = load_library()
    ids, embs = load_e5()

    print(f"Поиск: '{query}'", flush=True)
    q = encode_query(query)
    sims = embs @ q
    order = np.argsort(sims)[::-1][:top_n]

    cards = []
    for idx in order:
        cid = ids[idx]
        kw  = lib.get(cid, {}).get("keywords", "") or ""
        b64 = frame_b64(cid)
        cards.append(clip_card(cid, kw, f"E5 score: {sims[idx]:.4f}", b64))

    html = render_html(
        f"Поиск: {query}",
        f"Топ-{top_n} клипов по E5 запросу.",
        cards
    )
    open_html(html)


def mode_clip(clip_ids_str: str):
    """Показать конкретные клипы по ID."""
    lib = load_library()
    ids = [x.strip().zfill(4) for x in clip_ids_str.split(",")]

    cards = []
    for cid in ids:
        kw  = lib.get(cid, {}).get("keywords", "NOT IN LIBRARY") or ""
        b64 = frame_b64(cid)
        cards.append(clip_card(cid, kw, "", b64))

    html = render_html(f"Клипы: {clip_ids_str}", f"{len(ids)} клипов", cards)
    open_html(html)


# ── main ──────────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Аудит библиотеки клипов")
    parser.add_argument("--mode",     default="suspicious",
                        choices=["suspicious","gallery","search","clip"])
    parser.add_argument("--top",      type=int, default=100, help="Кол-во клипов (suspicious/search)")
    parser.add_argument("--page",     type=int, default=1,   help="Страница (gallery)")
    parser.add_argument("--per-page", type=int, default=80,  help="Клипов на странице (gallery)")
    parser.add_argument("--query",    type=str, default="",  help="Запрос (search)")
    parser.add_argument("--id",       type=str, default="",  help="ID клипов через запятую (clip)")
    args = parser.parse_args()

    os.chdir(Path(__file__).parent.parent)

    if args.mode == "suspicious":
        mode_suspicious(args.top)
    elif args.mode == "gallery":
        mode_gallery(args.page, args.per_page)
    elif args.mode == "search":
        if not args.query:
            print("Укажи --query 'текст'"); sys.exit(1)
        mode_search(args.query, args.top)
    elif args.mode == "clip":
        if not args.id:
            print("Укажи --id 2322,2323"); sys.exit(1)
        mode_clip(args.id)
