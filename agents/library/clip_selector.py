"""
Умный выбор клипов из библиотеки.
Правила:
- Внутри видео: макс 10 повторок,
  один клип макс 2 раза
- Между видео: макс 15-20 клипов
  из предыдущего видео
- Один клип в 2 видео = 1 повторка
"""
import json
import sys
from pathlib import Path
from datetime import datetime

import numpy as np

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

# ── Пути через paths.py (поддержка нескольких библиотек по нишам) ─────────────
_utils_dir = Path(__file__).resolve().parent.parent / "utils"
if str(_utils_dir) not in sys.path:
    sys.path.insert(0, str(_utils_dir))
from paths import (
    get_library_dir,
    get_library_json,
    get_clips_dir,
    get_embeddings_file,
    get_usage_history,
    get_niche,
    get_lang,
)

_EMBED_SERVER    = "http://127.0.0.1:8765"
VISUAL_WEIGHT    = 0.40   # доля visual CLIP score; 1-VISUAL_WEIGHT — text e5 score (60/40)

# ─── Embedding — сервер или локальная модель ──────────────────────────

_embed_model = None
_server_ok: bool | None = None  # None = не проверяли


def _check_server() -> bool:
    global _server_ok
    if _server_ok is not None:
        return _server_ok
    try:
        import urllib.request
        import json as _j
        body = _j.dumps({"texts": ["test"], "mode": "query"}).encode()
        req = urllib.request.Request(f"{_EMBED_SERVER}/encode", data=body, headers={"Content-Type": "application/json"})
        urllib.request.urlopen(req, timeout=3)
        _server_ok = True
        print(f"⚡ Embedding server доступен ({_EMBED_SERVER})", flush=True)
    except Exception:
        _server_ok = False
        print("📦 Embedding server недоступен — загружаем модель локально...", flush=True)
    return _server_ok


def _encode_via_server(texts: list[str], mode: str) -> np.ndarray:
    import json as _json
    import urllib.request
    body = _json.dumps({"texts": texts, "mode": mode}).encode()
    req = urllib.request.Request(
        f"{_EMBED_SERVER}/encode",
        data=body,
        headers={"Content-Type": "application/json"},
    )
    with urllib.request.urlopen(req, timeout=30) as resp:
        data = _json.loads(resp.read())
    return np.array(data["embeddings"], dtype=np.float32)


def _encode_local(texts: list[str], mode: str) -> np.ndarray:
    global _embed_model
    if _embed_model is None:
        from sentence_transformers import SentenceTransformer
        _embed_model = SentenceTransformer(
            "intfloat/multilingual-e5-large",
            model_kwargs={"torch_dtype": "float16"},
        )
        print("✅ Модель загружена", flush=True)
    prefix = "query: " if mode == "query" else "passage: "
    prefixed = [f"{prefix}{t}" for t in texts]
    return _embed_model.encode(prefixed, normalize_embeddings=True, convert_to_numpy=True)


def _encode(texts: list[str], mode: str) -> np.ndarray:
    if _check_server():
        return _encode_via_server(texts, mode)
    return _encode_local(texts, mode)


def encode_query(text: str) -> np.ndarray:
    return _encode([text], "query")[0]


def encode_passage(text: str) -> np.ndarray:
    return _encode([text], "passage")[0]


# ─── Build / load embeddings ──────────────────

def _combine_keywords(entry: dict) -> str:
    """Объединить все доступные языковые keywords в одну строку."""
    return ", ".join(filter(None, [
        entry.get("keywords", ""),
        entry.get("keywords_de", ""),
        entry.get("keywords_fr", ""),
        entry.get("keywords_es", ""),
    ]))


def build_library_embeddings(channel_id: str = "channel_001_cosmos_de"):
    """
    Предвычислить embeddings для всех проиндексированных клипов.
    Объединяет все доступные языковые keywords (EN/DE/FR/ES).
    Сохраняет в embeddings.npz (ключи: clip_ids, embeddings).
    """
    library = load_library(channel_id)

    clips = [
        (clip_id, entry)
        for clip_id, entry in library["clips"].items()
        if entry.get("indexed", False)
        and not entry.get("rejected", False)
        and entry.get("keywords", "")
    ]

    clip_ids          = [c[0] for c in clips]
    combined_keywords = [_combine_keywords(entry) for _, entry in clips]

    print(
        f"⏳ Embeddings для {len(clips)} клипов...",
        flush=True,
    )

    passages = [f"passage: {kw}" for kw in combined_keywords]
    embeddings = _encode_local(passages, "passage")
    embeddings = embeddings / np.linalg.norm(embeddings, axis=1, keepdims=True)

    emb_file = get_embeddings_file(channel_id)
    np.savez(
        emb_file,
        clip_ids=np.array(clip_ids),
        embeddings=embeddings,
    )
    print(f"✅ Embeddings: {embeddings.shape}", flush=True)
    return clip_ids, embeddings


def load_library_embeddings(channel_id: str = "channel_001_cosmos_de") -> tuple[list[str], np.ndarray]:
    """
    Загрузить предвычисленные embeddings.
    Возвращает (clip_ids_list, embeddings_matrix).
    Если файла нет — пересчитывает.
    """
    emb_file = get_embeddings_file(channel_id)
    if not emb_file.exists():
        print(
            f"⚠️  embeddings.npz не найден "
            f"[{get_niche(channel_id)}] — пересчитываем...",
            flush=True,
        )
        return build_library_embeddings(channel_id)

    data       = np.load(emb_file, allow_pickle=True)
    ids        = list(data["clip_ids"])
    embeddings = data["embeddings"]
    print(
        f"✅ Embeddings загружены: {len(ids)} клипов "
        f"[{get_niche(channel_id)}]",
        flush=True,
    )
    return ids, embeddings


# ─── Загрузка данных ──────────────────────────

def load_library(channel_id: str = "channel_001_cosmos_de") -> dict:
    path = get_library_json(channel_id)
    if path.exists():
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    return {"total": 0, "indexed_at": "", "clips": {}, "photos": {}}


def load_history(channel_id: str = "channel_001_cosmos_de") -> dict:
    path = get_usage_history(channel_id)
    if path.exists():
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    return {"videos": {}, "clip_usage": {}}


def save_history(history: dict, channel_id: str = "channel_001_cosmos_de") -> None:
    path = get_usage_history(channel_id)
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(history, f, indent=2, ensure_ascii=False)


# ─── Получить использованные клипы ────────────

def get_prev_video_clips(channel_id, history, n_prev=1):
    """Получить клипы из последних N видео для данного канала."""
    channel_videos = [
        (vid_id, data)
        for vid_id, data in history["videos"].items()
        if data.get("channel") == channel_id
    ]
    channel_videos.sort(key=lambda x: x[1].get("date", ""), reverse=True)

    prev_clips = set()
    for _, data in channel_videos[:n_prev]:
        prev_clips.update(data.get("clips_used", []))
    return prev_clips


# ══════════════════════════════════════════════
# Настройки блокировки и кулдауна
# ══════════════════════════════════════════════

# ── Блокнотик ─────────────────────────────────
# Для каждого клипа хранится индекс видео в котором он был последний раз использован.
# Пока прошло < COOLDOWN_VIDEOS видео → клип заблокирован (999).
# После COOLDOWN_VIDEOS → клип снова ПРИОРИТЕТНЫЙ (penalty = 0, как будто никогда не использовался).
COOLDOWN_VIDEOS: int = 3

# ── Повтор внутри одного видео ────────────────
# 1-й раз можно всегда. 2-й раз — только через 5–7 минут после первого.
# Порог рандомизируется на основе hash клипа (стабильно, но разный для каждого клипа).
INTRA_REPEAT_MIN_S: float = 300.0   # 5 мин
INTRA_REPEAT_MAX_S: float = 420.0   # 7 мин

# ── Jitter ────────────────────────────────────
# Случайный шум ±SCORE_JITTER на cosine score.
# Зачем: клипы «Джеймс Уэбб», «туманность», «галактика» имеют почти одинаковые
# embeddings (кластер 0.70–0.76). Без jitter ВСЕГДА побеждает один и тот же клип
# который случайно оказался первым в отсортированном массиве.
# С jitter ±0.04 каждый раз случайно побеждает разный из кластера.
SCORE_JITTER: float = 0.04


# ─── Penalty система ──────────────────────────

def calculate_penalty(
        clip_id,
        video_used,
        prev_video_clips,
        max_repeats_in_video=10,
        max_from_prev=20,
        global_usage:       dict | None = None,
        clip_last_used_idx: dict | None = None,   # {clip_id: video_index}
        current_video_idx:  int         = 0,      # номер текущего видео
        ignore_hard_block:  bool        = False,  # для fallback после 5 мин
        video_used_at:      dict | None = None,   # {clip_id: first_use_time_s} внутри видео
        segment_start:      float       = 0.0,    # текущее время сегмента
):
    """
    Рассчитать penalty для клипа.
      999  = hard block (временный запрет)
      0.0  = нет штрафа (свежий или восстановившийся клип)

    Порядок проверок:
      1. In-video repeat gate: уже использован в этом видео →
           разрешён только через 5–7 мин (INTRA_REPEAT_MIN_S / MAX_S)
      2. Превышен лимит повторок в видео → 999
      3. Превышен лимит клипов из предыдущих видео → 999
      4. Notebook cooldown: использован < COOLDOWN_VIDEOS видео назад → 999
         После cooldown: убывающий penalty 0.30→0.15→0.10→0.07→0
      5. Soft: +0.3 если клип из предыдущих 5 видео
    """
    import hashlib

    used_count = video_used.get(clip_id, 0)

    # ── 1. In-video repeat gate ────────────────────────────────────────────────
    if used_count >= 1:
        if video_used_at is not None and clip_id in video_used_at:
            first_use_t = video_used_at[clip_id]
            # Стабильный порог [5–7 мин] на основе hash clip_id (разный для каждого клипа)
            h = int(hashlib.md5(clip_id.encode()).hexdigest(), 16)
            threshold = (INTRA_REPEAT_MIN_S
                         + (h % 1000) / 1000.0 * (INTRA_REPEAT_MAX_S - INTRA_REPEAT_MIN_S))
            if segment_start - first_use_t < threshold:
                return 999  # слишком рано для повтора
        else:
            return 999  # нет данных о времени → блок

    # ── 3. Max повторок в видео ────────────────────────────────────────────────
    if used_count >= 2:
        return 999  # максимум 2 раза в одном видео

    total_repeats = sum(1 for cnt in video_used.values() if cnt > 1)
    if total_repeats >= max_repeats_in_video and used_count >= 1:
        return 999

    # ── 4. Max overlap с предыдущими видео ────────────────────────────────────
    prev_overlap = sum(1 for c in video_used if c in prev_video_clips)
    if prev_overlap >= max_from_prev and clip_id in prev_video_clips and used_count == 0:
        return 999

    # ── 5. Notebook hard block + recency priority (clip_last_used_idx) ──────────
    if clip_last_used_idx is not None and clip_id in clip_last_used_idx:
        videos_since = current_video_idx - clip_last_used_idx[clip_id]
        if not ignore_hard_block and videos_since < COOLDOWN_VIDEOS:
            return 999
        # Cooldown прошёл: убывающий штраф по давности.
        # Только что разблокировался (3 видео) → 0.30 (менее приоритетен чем никогда не использованный)
        # Давно не использовался (12+ видео)   → ~0.07 (почти как новый)
        # Никогда не использован               → penalty = 0.0 (лучший приоритет)
        recency_penalty = 0.9 / max(videos_since, 1)  # 3→0.30, 6→0.15, 9→0.10, 12→0.075
        return max(0.0, recency_penalty)

    # ── 6. Soft penalty ────────────────────────────────────────────────────────
    penalty = 0.0
    if clip_id in prev_video_clips:
        penalty += 0.3

    return penalty



# ─── Контекст видео и глав ────────────────────

def build_video_context(segments: list) -> np.ndarray:
    """
    [1] Глобальный топик: объединяем все тексты сценария в один embedding.
    Возвращает нормализованный вектор темы видео.
    """
    all_text = " ".join(
        seg.get("text", "") for seg in segments
        if seg.get("text", "").strip()
    )
    print(f"🌍 Глобальный топик: {len(all_text)} символов → embedding...", flush=True)
    vec = encode_query(all_text[:2000])  # e5 max ~512 tokens, обрезаем
    return vec / (np.linalg.norm(vec) + 1e-9)


def build_chapter_embeddings(segments: list, n_chapters: int = 5) -> list[np.ndarray]:
    """
    [3] Главы: делим сегменты на N равных по времени глав,
    вычисляем embedding каждой → смысловой контекст главы.
    """
    if not segments:
        return []

    total_dur   = float(segments[-1].get("end", 0)) or 1.0
    chapter_dur = total_dur / n_chapters
    chapters: list[list[str]] = [[] for _ in range(n_chapters)]

    for seg in segments:
        t   = float(seg.get("start", 0))
        idx = min(int(t / chapter_dur), n_chapters - 1)
        txt = seg.get("text", "").strip()
        if txt:
            chapters[idx].append(txt)

    embeddings = []
    for i, texts in enumerate(chapters):
        combined = " ".join(texts)
        if combined.strip():
            vec = encode_query(combined[:1000])
            vec = vec / (np.linalg.norm(vec) + 1e-9)
        else:
            vec = np.zeros(1024, dtype=np.float32)  # dim e5-large
        embeddings.append(vec)
        print(f"  📖 Глава {i+1}/{n_chapters}: {len(texts)} сегментов", flush=True)

    return embeddings


def get_chapter_idx(seg_start: float, total_dur: float, n_chapters: int = 5) -> int:
    if total_dur <= 0:
        return 0
    return min(int(seg_start / total_dur * n_chapters), n_chapters - 1)


# ─── Матчинг сегмента с библиотекой ──────────

def match_segment_to_clip(
        segment_text: str,
        keywords_list: list,          # (clip_id, keywords_en, duration)
        video_used: dict,
        prev_video_clips: set,
        clip_embeddings: np.ndarray,  # (N, 1024) e5-large, нормализованные
        clip_ids_list: list[str],     # [clip_id, ...] в том же порядке что clip_embeddings
        max_repeats_in_video=10,
        max_from_prev=20,
        segment_duration=0.0,
        top_n: int = 3,
        context_vec: np.ndarray | None = None,      # [1] глобальный топик видео
        chapter_vec: np.ndarray | None = None,      # [3] embedding текущей главы
        window_text: str = "",                      # [2] текст окна prev+next
        global_usage: dict | None = None,
        visual_query: str = "",                     # [4] Haiku-сгенерированный визуальный запрос
        clip_last_used_idx: dict | None = None,
        current_video_idx:  int         = 0,
        segment_start:      float       = 0.0,
        video_used_at:      dict | None = None,
        visual_embeddings: np.ndarray | None = None,  # (M, 512) CLIP visual
        visual_ids_list: list[str] | None = None,     # [clip_id, ...] для visual
):
    """
    Найти top_n лучших клипов по гибридному скору:
      text_score   = cosine(e5(visual_query), e5(keywords))  [60%]
      visual_score = cosine(clip_text(visual_query), clip_visual)  [40%]

    Text query приоритет: visual_query (Haiku) > window_text > segment_text
    visual_query — готовое визуальное описание сцены от Haiku, e5 матчит точнее.
    Дополнительно: +global_topic (0.75/0.25) + chapter_vec (0.85/0.15)
    """
    # ── Text score (e5-large) ─────────────────────────────────────────────────
    # Приоритет: visual_query (Haiku) > window_text > segment_text
    # visual_query содержит готовое визуальное описание сцены — e5 матчит по нему точнее
    query_text = visual_query if visual_query.strip() else (window_text if window_text.strip() else segment_text)
    seg_vec    = encode_query(query_text)
    seg_vec    = seg_vec / (np.linalg.norm(seg_vec) + 1e-9)

    if context_vec is not None:
        seg_vec = 0.75 * seg_vec + 0.25 * context_vec
        seg_vec = seg_vec / (np.linalg.norm(seg_vec) + 1e-9)

    if chapter_vec is not None and np.any(chapter_vec):
        seg_vec = 0.85 * seg_vec + 0.15 * chapter_vec
        seg_vec = seg_vec / (np.linalg.norm(seg_vec) + 1e-9)

    text_scores = clip_embeddings @ seg_vec   # (N,) cosine similarity
    emb_index   = {cid: i for i, cid in enumerate(clip_ids_list)}

    # ── Visual score (CLIP ViT-B/32 multilingual) ─────────────────────────────
    # Используем visual_query (от Haiku) если есть, иначе fallback на segment_text
    vis_scores: dict[str, float] = {}
    if visual_embeddings is not None and visual_ids_list:
        try:
            from visual_embedder import encode_text as _clip_encode_text
            _clip_text = visual_query if visual_query.strip() else segment_text
            vis_query_vec = _clip_encode_text(_clip_text)        # (512,)
            raw_vis   = visual_embeddings @ vis_query_vec         # (M,)
            for cid, sc in zip(visual_ids_list, raw_vis.tolist()):
                vis_scores[cid] = float(sc)
        except Exception as _ve:
            pass  # visual недоступно — работаем только на text

    def _hybrid_score(clip_id: str, t_score: float) -> float:
        if not vis_scores:
            return t_score
        v_score = vis_scores.get(clip_id, 0.0)
        return (1.0 - VISUAL_WEIGHT) * t_score + VISUAL_WEIGHT * v_score

    cosine_scores = text_scores  # backward compat alias used below

    import random as _random

    def _build_candidates(ignore_hard_block: bool = False) -> list:
        out = []
        for clip_id, keywords, clip_duration in keywords_list:
            if not keywords:
                continue
            if segment_duration > 0 and clip_duration > 0 and clip_duration < segment_duration:
                continue

            penalty = calculate_penalty(
                clip_id, video_used, prev_video_clips,
                max_repeats_in_video, max_from_prev,
                global_usage=global_usage,
                clip_last_used_idx=clip_last_used_idx,
                current_video_idx=current_video_idx,
                ignore_hard_block=ignore_hard_block,
                video_used_at=video_used_at,
                segment_start=segment_start,
            )
            if penalty == 999:
                continue

            idx        = emb_index.get(clip_id)
            t_score    = float(cosine_scores[idx]) if idx is not None else 0.0
            score      = _hybrid_score(clip_id, t_score)

            # Jitter: разбиваем ties внутри embedding-кластера.
            # Без этого одни и те же топ-клипы (Джеймс Уэбб, туманности) всегда побеждают
            # потому что их embeddings кластеризуются в одной точке.
            jitter = _random.uniform(-SCORE_JITTER, SCORE_JITTER)
            out.append((clip_id, score - penalty + jitter, score, penalty, keywords, clip_duration))
        return out

    candidates = _build_candidates(ignore_hard_block=False)

    _fb_kwargs = dict(
        global_usage=global_usage,
        clip_last_used_idx=clip_last_used_idx,
        current_video_idx=current_video_idx,
        video_used_at=video_used_at,
        segment_start=segment_start,
    )

    # Fallback 1: пул истощён после 5 мин → снимаем hard-block
    if not candidates and segment_start > 300.0:
        print(f"  ⚠️  Пул истощён t={segment_start:.0f}s > 5мин → снимаем hard-block", flush=True)
        candidates = _build_candidates(ignore_hard_block=True)

    # Fallback 2: всё ещё пусто → _fallback_match (без ограничения длины)
    if not candidates:
        print(f"  ⚠️  Нет кандидатов → fallback match", flush=True)
        fb = _fallback_match(
            segment_text, keywords_list, video_used, prev_video_clips,
            clip_embeddings, clip_ids_list, precomputed_vec=seg_vec, **_fb_kwargs,
        )
        return [fb] if fb else []

    candidates.sort(key=lambda x: x[1], reverse=True)

    best = candidates[0]
    vis_info = f" vis={vis_scores.get(best[0], 0.0):.2f}" if vis_scores else ""
    print(
        f"  🎯 {best[0]} "
        f"score={best[2]:.2f}{vis_info} "
        f"dur={best[5]:.1f}s (seg={segment_duration:.1f}s) penalty={best[3]:.2f}",
        flush=True,
    )
    return [c[0] for c in candidates[:top_n]]


def _fallback_match(
        segment_text,
        keywords_list,
        video_used,
        prev_video_clips,
        clip_embeddings,
        clip_ids_list,
        precomputed_vec: np.ndarray | None = None,
        global_usage: dict | None = None,
        clip_last_used_idx: dict | None = None,
        current_video_idx: int = 0,
        video_used_at: dict | None = None,
        segment_start: float = 0.0):
    """Fallback: лучший по embedding без учёта длины.
    Если передан precomputed_vec (уже обогащённый контекстом) — использует его."""
    seg_vec       = precomputed_vec if precomputed_vec is not None else encode_query(segment_text)
    cosine_scores = clip_embeddings @ seg_vec
    emb_index     = {cid: i for i, cid in enumerate(clip_ids_list)}

    candidates = []
    for clip_id, keywords, clip_duration in keywords_list:
        if not keywords:
            continue
        if calculate_penalty(
            clip_id, video_used, prev_video_clips,
            global_usage=global_usage,
            clip_last_used_idx=clip_last_used_idx,
            current_video_idx=current_video_idx,
            ignore_hard_block=True,   # fallback — снимаем hard block
            video_used_at=video_used_at,
            segment_start=segment_start,
        ) == 999:
            continue

        idx   = emb_index.get(clip_id)
        score = float(cosine_scores[idx]) if idx is not None else 0.0
        candidates.append((clip_id, score, clip_duration))

    if not candidates:
        # Last-resort: случайный клип из ещё не использованных в этом видео
        import random as _r
        pool = [c for c in clip_ids_list if c not in (video_used or {})]
        if not pool:
            pool = list(clip_ids_list)
        chosen = _r.choice(pool)
        print(f"  ⚠️  Last-resort random: {chosen}", flush=True)
        return chosen

    candidates.sort(key=lambda x: x[1], reverse=True)
    best = candidates[0]
    print(f"  🔄 Fallback: {best[0]} emb={best[1]:.2f} dur={best[2]:.1f}s", flush=True)
    return best[0]


# ─── Главная функция выбора клипов ────────────

def select_clips_for_video(
        session,
        channel_id,
        segments,
        max_repeats_in_video=10,
        max_from_prev=60,
        intro_duration=90):
    """
    Выбрать клипы для всего видео, разделив на intro и main.
      session:        ID сессии (Video_20260318_...)
      channel_id:     ID канала (channel_001_cosmos_de, ...)
      segments:       [{id, text, start, end}, ...]
      intro_duration: первые N секунд озвучки → intro_clips

    Возвращает dict:
      {
        "intro_clips":    [(seg_id, clip_id, seg_duration), ...],
        "main_clips":     [(seg_id, clip_id, seg_duration), ...],
        "intro_duration": float,
        "main_duration":  float,
      }
    """
    print(
        f"🌐 Канал: {channel_id}\n"
        f"   Язык:  {get_lang(channel_id)}\n"
        f"   Ниша:  {get_niche(channel_id)}\n"
        f"   Библиотека: {get_library_dir(channel_id)}",
        flush=True,
    )

    library = load_library(channel_id)
    history = load_history(channel_id)

    # Загрузить предвычисленные text embeddings (e5-large, 1024-dim)
    clip_ids_list, clip_embeddings = load_library_embeddings(channel_id)

    # Загрузить visual embeddings (CLIP ViT-B/32, 512-dim) если доступны
    visual_ids_list: list[str] | None  = None
    visual_embeddings: np.ndarray | None = None
    try:
        from visual_embedder import load_visual_embeddings as _load_vis
        _vis = _load_vis(channel_id)
        if _vis is not None:
            visual_ids_list, visual_embeddings = _vis
    except ImportError:
        pass

    # Клипы из последних 5 видео канала (для блокировки недавно использованных)
    prev_clips          = get_prev_video_clips(channel_id, history, n_prev=5)
    global_usage        = history.get("clip_usage", {})
    clip_last_used_idx  = history.get("clip_last_used_idx", {})
    current_video_idx   = len(history.get("videos", {}))   # номер этого видео

    # pHash: {clip_id: int} для визуального anti-repetition
    phash_map: dict[str, int] = {
        cid: entry["phash"]
        for cid, entry in library["clips"].items()
        if isinstance(entry.get("phash"), int)
    }
    if phash_map:
        print(f"🖼 pHash загружен: {len(phash_map)} клипов", flush=True)

    hard_blocked  = sum(1 for cid in clip_last_used_idx
                        if (current_video_idx - clip_last_used_idx[cid]) < COOLDOWN_VIDEOS)
    cooldown_free = sum(1 for cid in clip_last_used_idx
                        if (current_video_idx - clip_last_used_idx[cid]) >= COOLDOWN_VIDEOS)
    print(f"🔒 Hard-blocked: {hard_blocked}  |  Вышло из кулдауна: {cooldown_free}", flush=True)

    # Валидные клипы с EN+DE+FR keywords и длительностью
    available_clips = [
        (
            clip_id,
            _combine_keywords(entry),
            entry.get("duration", 0),
        )
        for clip_id, entry in library["clips"].items()
        if entry.get("indexed", False)
        and not entry.get("rejected", False)
        and entry.get("keywords", "")
    ]
    long_clips = sum(1 for _, _, d in available_clips if d >= 10)
    print(f"📚 Доступно клипов: {len(available_clips)} (>= 10s: {long_clips})", flush=True)

    # Разделить сегменты на intro / main по накопленной длительности
    intro_seg_ids: set[int] = set()
    accumulated = 0.0
    for seg in segments:
        seg_dur = float(seg.get("end", 0)) - float(seg.get("start", 0))
        if accumulated < intro_duration:
            intro_seg_ids.add(int(seg.get("id", 0)))
            accumulated += seg_dur
        else:
            break

    print(
        f"🎬 Интро: {len(intro_seg_ids)} сегментов (~{accumulated:.1f}s из {intro_duration}s)",
        flush=True,
    )

    # [1] Глобальный топик всего видео
    video_context_vec = build_video_context(segments)

    # [3] Embedding каждой главы
    total_dur     = float(segments[-1].get("end", 0)) if segments else 1.0
    N_CHAPTERS    = 5
    chapter_vecs  = build_chapter_embeddings(segments, n_chapters=N_CHAPTERS)

    video_used         = {}
    video_used_at      = {}   # {clip_id: first_use_time_s} — для in-video repeat gate
    intro_clips        = []
    main_clips         = []
    intro_total        = 0.0
    main_total         = 0.0
    prev_clip_embeddings: list[np.ndarray] = []   # embedding diversity window (last 5)
    recent_phashes:     list[int]          = []   # pHash window (last 20 клипов в видео)
    PHASH_WINDOW       = 20    # сколько последних клипов проверяем на визуальный дубль
    PHASH_THRESHOLD    = 12    # hamming distance < 12 из 64 бит = визуально идентичны
    emb_index = {cid: i for i, cid in enumerate(clip_ids_list)}

    for i, seg in enumerate(segments):
        seg_id       = seg.get("id", 0)
        seg_text     = seg.get("text", "")
        seg_start    = float(seg.get("start", 0))
        seg_duration = float(seg.get("end", 0)) - seg_start
        is_intro     = int(seg_id) in intro_seg_ids

        # [2] Скользящее окно: prev + current + next
        prev_text = segments[i - 1].get("text", "") if i > 0 else ""
        next_text = segments[i + 1].get("text", "") if i < len(segments) - 1 else ""
        window_text = " ".join(filter(None, [prev_text, seg_text, next_text]))

        # [3] Embedding главы для текущего сегмента
        ch_idx      = get_chapter_idx(seg_start, total_dur, N_CHAPTERS)
        chapter_vec = chapter_vecs[ch_idx] if chapter_vecs else None

        section = "INTRO" if is_intro else "MAIN"
        print(f"\n[{seg_id}][{section}] {seg_duration:.1f}s '{seg_text[:60]}'", flush=True)

        top_candidates = match_segment_to_clip(
            segment_text=seg_text,
            keywords_list=available_clips,
            video_used=video_used,
            prev_video_clips=prev_clips,
            clip_embeddings=clip_embeddings,
            clip_ids_list=clip_ids_list,
            max_repeats_in_video=max_repeats_in_video,
            max_from_prev=max_from_prev,
            segment_duration=seg_duration,
            top_n=3,
            context_vec=video_context_vec,
            chapter_vec=chapter_vec,
            window_text=window_text,
            global_usage=global_usage,
            clip_last_used_idx=clip_last_used_idx,
            current_video_idx=current_video_idx,
            segment_start=seg_start,
            video_used_at=video_used_at,
            visual_embeddings=visual_embeddings,
            visual_ids_list=visual_ids_list,
            visual_query=seg.get("visual_query", ""),
        )

        # [5] Embedding diversity window (last 5): cosine > 0.92 → берём следующего кандидата
        clip_id = top_candidates[0] if top_candidates else None
        if clip_id and prev_clip_embeddings:
            idx = emb_index.get(clip_id)
            if idx is not None:
                for prev_vec in prev_clip_embeddings:
                    if float(clip_embeddings[idx] @ prev_vec) > 0.92 and len(top_candidates) > 1:
                        clip_id = top_candidates[1]
                        print(f"  ⚠ Emb-diversity → {clip_id}", flush=True)
                        break

        # [6] pHash window (last 20): визуально идентичные кадры → берём следующего кандидата
        if clip_id and phash_map and recent_phashes:
            h = phash_map.get(clip_id)
            if h is not None:
                for rh in recent_phashes:
                    if bin(h ^ rh).count("1") < PHASH_THRESHOLD:
                        alts = [c for c in top_candidates if c != clip_id]
                        if alts:
                            clip_id = alts[0]
                            print(f"  ⚠ pHash-duplicate → {clip_id}", flush=True)
                        break

        # Обновить окна: embedding window (last 5) + pHash window (last 20)
        if clip_id:
            idx = emb_index.get(clip_id)
            if idx is not None:
                prev_clip_embeddings.append(clip_embeddings[idx].copy())
                if len(prev_clip_embeddings) > 5:
                    prev_clip_embeddings.pop(0)
            if phash_map and clip_id in phash_map:
                recent_phashes.append(phash_map[clip_id])
                if len(recent_phashes) > PHASH_WINDOW:
                    recent_phashes.pop(0)
            video_used[clip_id] = video_used.get(clip_id, 0) + 1
            if clip_id not in video_used_at:
                video_used_at[clip_id] = seg_start

        entry = (seg_id, clip_id, seg_duration)
        if is_intro:
            intro_clips.append(entry)
            intro_total += seg_duration
        else:
            main_clips.append(entry)
            main_total += seg_duration

    repeats      = sum(1 for cnt in video_used.values() if cnt > 1)
    prev_overlap = sum(1 for c in video_used if c in prev_clips)
    all_selected = intro_clips + main_clips
    found        = sum(1 for _, c, _ in all_selected if c)

    print(f"""
{'='*50}
📊 ВЫБОР КЛИПОВ ЗАВЕРШЁН
  Канал:               {channel_id}
  Сегментов:           {len(segments)}
  Интро:               {len(intro_clips)} сег ({intro_total:.1f}s)
  Основное:            {len(main_clips)} сег ({main_total:.1f}s)
  Клипов выбрано:      {found}
  Повторок в видео:    {repeats}/{max_repeats_in_video}
  Из предыдущего:      {prev_overlap}/{max_from_prev}
{'='*50}
""", flush=True)

    clips_used = [c for _, c, _ in all_selected if c]

    result = {
        "intro_clips":    intro_clips,
        "main_clips":     main_clips,
        "intro_duration": intro_total,
        "main_duration":  main_total,
        "_clips_used":    clips_used,
        "_history":       history,
        "_channel_id":    channel_id,
        "_session":       session,
    }

    # Сохраняем clip_selection.json — чтобы при повторном рендере не пересчитывать клипы
    try:
        import json as _json
        from pathlib import Path as _Path
        from paths import CHANNELS_DIR as _CHANNELS_DIR, get_lang as _get_lang
        cs_dir = _CHANNELS_DIR / _get_lang(channel_id) / session
        if not cs_dir.exists():
            for _ch in ("de", "fr", "en", "es"):
                _p = _CHANNELS_DIR / _ch / session
                if _p.exists():
                    cs_dir = _p
                    break
        cs_path = cs_dir / "clip_selection.json"
        _save_data = {
            "intro_clips": intro_clips,
            "main_clips":  main_clips,
            "channel_id":  channel_id,
            "session":     session,
        }
        with open(cs_path, "w", encoding="utf-8") as _f:
            _json.dump(_save_data, _f, ensure_ascii=False, indent=2)
        print(f"💾 clip_selection.json сохранён: {cs_path}", flush=True)
    except Exception as _e:
        print(f"⚠ clip_selection.json не сохранён: {_e}", flush=True)

    return result


def commit_clip_history(result: dict) -> None:
    """Зафиксировать историю клипов ПОСЛЕ успешного рендера."""
    history    = result.get("_history")
    session    = result.get("_session")
    channel_id = result.get("_channel_id")
    clips_used = result.get("_clips_used", [])
    if history is None or not session:
        return
    _save_video_to_history(session, channel_id, clips_used, history)


def _save_video_to_history(session, channel_id, clips_used, history):
    """Сохранить использованные клипы в историю."""
    video_idx = len(history["videos"])   # индекс этого видео (0-based)

    history["videos"][session] = {
        "channel":     channel_id,
        "date":        datetime.now().isoformat(),
        "clips_used":  clips_used,
        "total_clips": len(clips_used),
    }
    for clip_id in clips_used:
        history["clip_usage"][clip_id] = history["clip_usage"].get(clip_id, 0) + 1

    # Запоминаем индекс последнего видео в котором использовался клип
    # (нужно для cooldown: через COOLDOWN_VIDEOS видео hard block снимается)
    if "clip_last_used_idx" not in history:
        history["clip_last_used_idx"] = {}
    for clip_id in clips_used:
        history["clip_last_used_idx"][clip_id] = video_idx

    # Оставить только последние 50 видео
    videos = history["videos"]
    if len(videos) > 50:
        sorted_v = sorted(videos.keys(), key=lambda x: videos[x].get("date", ""), reverse=True)
        for old in sorted_v[50:]:
            del history["videos"][old]

    save_history(history, channel_id)
    print(f"💾 История сохранена: {session}", flush=True)
