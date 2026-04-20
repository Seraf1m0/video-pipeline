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
_utils_dir  = Path(__file__).resolve().parent.parent / "utils"
_tools_dir  = Path(__file__).resolve().parent.parent.parent / "tools"
if str(_utils_dir) not in sys.path:
    sys.path.insert(0, str(_utils_dir))
if str(_tools_dir) not in sys.path:
    sys.path.insert(0, str(_tools_dir))
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
COOLDOWN_VIDEOS: int = 2   # дефолт; в select_clips_for_video пересчитывается динамически

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
SCORE_JITTER: float = 0.005
# Снижено с 0.04 → 0.005: разброс E5 между релевантным и нерелевантным клипом
# всего 0.005–0.01, jitter 0.04 полностью уничтожал сигнал.


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
        video_used_at:      dict | None = None,   # {clip_id: first_use_time_s} внутри видео
        segment_start:      float       = 0.0,    # текущее время сегмента
):
    """
    Рассчитать penalty для клипа. Нет хард-блоков по cooldown — только плавные штрафы.
      999  = hard block (только внутривидео правила)
      0.0  = нет штрафа (свежий клип)

    Порядок:
      1. In-video repeat gate: использован в этом видео → блок до 5-7 мин
      2. Лимит повторок в видео (макс 2 раза) → 999
      3. Лимит overlap с предыдущими видео → 999
      4. Плавный recency штраф: 0.6/videos_since (1→0.60, 2→0.30, 3→0.20, ...)
         Никогда не использован → penalty=0.0
    """
    import hashlib

    used_count = video_used.get(clip_id, 0)

    # ── 1. In-video repeat gate ────────────────────────────────────────────────
    if used_count >= 1:
        if video_used_at is not None and clip_id in video_used_at:
            first_use_t = video_used_at[clip_id]
            h = int(hashlib.md5(clip_id.encode()).hexdigest(), 16)
            threshold = (INTRA_REPEAT_MIN_S
                         + (h % 1000) / 1000.0 * (INTRA_REPEAT_MAX_S - INTRA_REPEAT_MIN_S))
            if segment_start - first_use_t < threshold:
                return 999  # слишком рано для повтора
        else:
            return 999

    # ── 2. Max повторок в видео ────────────────────────────────────────────────
    if used_count >= 2:
        return 999  # максимум 2 раза в одном видео

    total_repeats = sum(1 for cnt in video_used.values() if cnt > 1)
    if total_repeats >= max_repeats_in_video and used_count >= 1:
        return 999

    # ── 3. Max overlap с предыдущими видео ────────────────────────────────────
    prev_overlap = sum(1 for c in video_used if c in prev_video_clips)
    if prev_overlap >= max_from_prev and clip_id in prev_video_clips and used_count == 0:
        return 999

    # ── 4. Хард-блок на COOLDOWN_VIDEOS видео, затем плавное восстановление ──
    # Сразу после кулдауна — минимальный приоритет (штраф 0.5).
    # С каждым следующим видео штраф убывает на 0.1, через 5 видео = 0 (полный приоритет).
    if clip_last_used_idx is not None and clip_id in clip_last_used_idx:
        videos_since = max(1, current_video_idx - clip_last_used_idx[clip_id])
        if videos_since <= COOLDOWN_VIDEOS:
            return 999  # хард-блок: последние 1-2 видео
        videos_after_cooldown = videos_since - COOLDOWN_VIDEOS
        return max(0.0, 0.5 - videos_after_cooldown * 0.1)  # 0.4 → 0.3 → 0.2 → 0.1 → 0.0

    return 0.0



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
        clip_last_used_idx: dict | None = None,
        current_video_idx:  int         = 0,
        segment_start:      float       = 0.0,
        video_used_at:      dict | None = None,
        prev_video_centroid: np.ndarray | None = None,
        clip_tags: dict[str, set[str]] | None = None,
        query_tags: list[str] | None = None,
):
    """
    Найти top_n лучших клипов по Gemini embedding скору.
    Text query: window_text (скользящее окно) > segment_text.
    Дополнительно: +global_topic (0.90/0.10) + chapter_vec (0.85/0.15)
    """
    # ── Text score ────────────────────────────────────────────────────────────
    query_text = window_text if window_text.strip() else segment_text
    seg_vec    = encode_query(query_text)
    seg_vec    = seg_vec / (np.linalg.norm(seg_vec) + 1e-9)

    if context_vec is not None:
        seg_vec = 0.90 * seg_vec + 0.10 * context_vec
        seg_vec = seg_vec / (np.linalg.norm(seg_vec) + 1e-9)
        # Снижено с 0.75/0.25 → 0.90/0.10: контекст сжимал все запросы к
        # общему "space" вектору, уничтожая специфику (spacecraft vs nebula)

    if chapter_vec is not None and np.any(chapter_vec):
        seg_vec = 0.85 * seg_vec + 0.15 * chapter_vec
        seg_vec = seg_vec / (np.linalg.norm(seg_vec) + 1e-9)

    text_scores = clip_embeddings @ seg_vec   # (N,) cosine similarity
    emb_index   = {cid: i for i, cid in enumerate(clip_ids_list)}

    cosine_scores = text_scores

    import random as _random

    def _build_candidates() -> list:
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
                video_used_at=video_used_at,
                segment_start=segment_start,
            )
            if penalty == 999:
                continue

            idx     = emb_index.get(clip_id)
            score = float(cosine_scores[idx]) if idx is not None else 0.0

            # Семантический штраф за схожесть с предыдущим видео:
            # если клип близок к центроиду предыдущего видео → небольшой доп штраф
            cross_penalty = 0.0
            if prev_video_centroid is not None and idx is not None:
                sim = float(clip_embeddings[idx] @ prev_video_centroid)
                cross_penalty = max(0.0, (sim - 0.75) * 1.0)

            # Tag boost: если теги из visual_query совпадают с тегами клипа
            tag_boost = 0.0
            if query_tags and clip_tags:
                ctags = clip_tags.get(clip_id, set())
                matched = set(query_tags) & ctags
                if matched:
                    tag_boost = 0.08 * min(len(matched), 2)  # max +0.16 за 2+ совпадений

            # Jitter: разбиваем ties внутри embedding-кластера
            jitter = _random.uniform(-SCORE_JITTER, SCORE_JITTER)
            out.append((clip_id, score - penalty - cross_penalty + tag_boost + jitter,
                        score, penalty, keywords, clip_duration))
        return out

    candidates = _build_candidates()

    # Print tag boost stats if tags were used
    if query_tags and clip_tags:
        boosted_count = sum(
            1 for clip_id, _, _, _, _, _ in candidates
            if set(query_tags) & clip_tags.get(clip_id, set())
        )
        if boosted_count > 0:
            print(f"  🏷 Tags from query: {query_tags} → {boosted_count} clips boosted", flush=True)

    # Fallback: нет кандидатов → _fallback_match (без ограничения длины)
    if not candidates:
        print(f"  ⚠️  Нет кандидатов → fallback match", flush=True)
        fb = _fallback_match(
            segment_text, keywords_list, video_used, prev_video_clips,
            clip_embeddings, clip_ids_list, precomputed_vec=seg_vec,
            global_usage=global_usage,
            clip_last_used_idx=clip_last_used_idx,
            current_video_idx=current_video_idx,
            video_used_at=video_used_at,
            segment_start=segment_start,
        )
        return [fb] if fb else []

    candidates.sort(key=lambda x: x[1], reverse=True)

    best = candidates[0]
    vis_info = ""
    if query_tags and clip_tags:
        ctags = clip_tags.get(best[0], set())
        matched_tags = set(query_tags) & ctags
        if matched_tags:
            vis_info += f" tags={matched_tags}"
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
        intro_duration=90,
        text_only=False):
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

    # ── Gemini embeddings (primary) ───────────────────────────────────────────
    _gemini_ids: list[str] | None   = None
    _gemini_emb: np.ndarray | None  = None
    try:
        from gemini_embedder import load_library_embeddings as _load_gemini
        _gemini_ids, _gemini_emb = _load_gemini(channel_id)
        print(f"✅ Gemini embeddings: {len(_gemini_ids)} клипов ({_gemini_emb.shape[1]}-dim)", flush=True)
    except Exception as _ge:
        print(f"⚠ Gemini embeddings недоступны: {_ge} — fallback E5", flush=True)

    # ── E5 fallback ───────────────────────────────────────────────────────────
    clip_ids_list, clip_embeddings = load_library_embeddings(channel_id)

    # ── Flash reranker ────────────────────────────────────────────────────────
    _flash_available = False
    try:
        from gemini_reranker import rerank_batch as _rerank_batch
        _flash_available = True
        print(f"✅ Flash reranker: gemini-2.5-flash", flush=True)
    except Exception as _fe:
        print(f"⚠ Flash reranker недоступен: {_fe}", flush=True)

    # Клипы из последних 5 видео канала (для блокировки недавно использованных)
    prev_clips          = get_prev_video_clips(channel_id, history, n_prev=5)
    global_usage        = history.get("clip_usage", {})
    clip_last_used_idx  = history.get("clip_last_used_idx", {})
    current_video_idx   = len(history.get("videos", {}))   # номер этого видео

    # pHash: {clip_id: int} для визуального anti-repetition
    phash_map: dict[str, int] = {} if text_only else {
        cid: entry["phash"]
        for cid, entry in library["clips"].items()
        if isinstance(entry.get("phash"), int)
    }
    if phash_map:
        print(f"🖼 pHash загружен: {len(phash_map)} клипов", flush=True)

    # Валидные клипы с EN+DE+FR keywords и длительностью
    # Исключаем garbage (квота) и human (нерелевантные)
    _EXCLUDED_CATEGORIES = {"garbage", "human"}
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
        and entry.get("category", "other") not in _EXCLUDED_CATEGORIES
    ]
    # Индекс тегов: {clip_id: set(tags + [category])} для быстрого lookup
    _clip_tags: dict[str, set[str]] = {
        clip_id: set(entry.get("tags", []) + [entry.get("category", "other")])
        for clip_id, entry in library["clips"].items()
        if entry.get("indexed", False) and not entry.get("rejected", False)
    }
    garbage_count = sum(1 for e in library["clips"].values()
                        if e.get("category") in _EXCLUDED_CATEGORIES)
    long_clips = sum(1 for _, _, d in available_clips if d >= 10)
    print(f"📚 Доступно клипов: {len(available_clips)} (>= 10s: {long_clips}) "
          f"[исключено garbage/human: {garbage_count}]", flush=True)

    # Статистика по recency (штраф вместо хард-блока)
    _recently_used = sum(1 for cid in clip_last_used_idx
                         if (current_video_idx - clip_last_used_idx[cid]) <= 2)
    print(f"🔒 Использовано в последних 2 видео: {_recently_used} клипов "
          f"(получат штраф 0.20–0.60, не заблокированы)", flush=True)

    # Центроид предыдущего видео: среднее e5-embedding всех клипов из последней сессии.
    # Клипы семантически похожие на предыдущее видео получат доп штраф (cross-video diversity).
    prev_video_centroid: np.ndarray | None = None
    _emb_index_map = {cid: i for i, cid in enumerate(clip_ids_list)}
    channel_videos = sorted(
        [(vid, d) for vid, d in history.get("videos", {}).items()
         if d.get("channel") == channel_id],
        key=lambda x: x[1].get("date", ""), reverse=True,
    )
    if channel_videos:
        last_clips_used = channel_videos[0][1].get("clips_used", [])
        last_indices    = [_emb_index_map[c] for c in last_clips_used if c in _emb_index_map]
        if last_indices:
            centroid = clip_embeddings[last_indices].mean(axis=0)
            norm     = np.linalg.norm(centroid)
            if norm > 1e-9:
                prev_video_centroid = centroid / norm
                print(f"📼 Центроид предыдущего видео: {len(last_indices)} клипов → "
                      f"cross-video diversity активен", flush=True)

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
    prev_clip_embeddings: list[tuple[float, np.ndarray]] = []   # E5 text diversity window (2 мин)
    recent_phashes:      list[tuple[float, int]]         = []   # pHash window (5 мин)
    EMB_DIVERSITY_WINDOW_S  = 120   # 2 минуты — окно embedding diversity по времени
    PHASH_WINDOW_S          = 300   # 5 минут — окно pHash по времени
    PHASH_THRESHOLD         = 12    # hamming distance < 12 из 64 бит = визуально идентичны
    emb_index = {cid: i for i, cid in enumerate(clip_ids_list)}

    _lang = get_lang(channel_id)

    # ── Индекс описаний клипов (для Flash reranker) ───────────────────────────
    _clip_desc_map: dict[str, str] = {
        cid: entry.get("keywords", "")
        for cid, entry in library["clips"].items()
    }

    # ── Gemini batch embedding всех сегментов (один API call) ─────────────────
    _gemini_seg_embs: np.ndarray | None = None
    _gemini_seg_index = {cid: i for i, cid in enumerate(_gemini_ids)} if _gemini_ids else {}

    if _gemini_ids is not None and _gemini_emb is not None:
        try:
            from gemini_embedder import embed_batch as _gemini_embed_batch
            _all_windows: list[str] = []
            for _si, _seg in enumerate(segments):
                _p2 = segments[_si-2].get("text","") if _si > 1 else ""
                _p1 = segments[_si-1].get("text","") if _si > 0 else ""
                _c  = _seg.get("text","")
                _n1 = segments[_si+1].get("text","") if _si < len(segments)-1 else ""
                _n2 = segments[_si+2].get("text","") if _si < len(segments)-2 else ""
                _all_windows.append(" ".join(filter(None, [_p2,_p1,_c,_n1,_n2])))
            print(f"🔢 Gemini: batch embedding {len(_all_windows)} сегментов...", flush=True)
            _gemini_seg_embs = _gemini_embed_batch(_all_windows)
            print(f"✅ Gemini сегменты: {_gemini_seg_embs.shape}", flush=True)
        except Exception as _gbe:
            print(f"⚠ Gemini batch embed сегментов: {_gbe} — fallback E5", flush=True)
            _gemini_seg_embs = None

    # ── Flash rerank: буфер задач ─────────────────────────────────────────────
    MARGIN_THRESHOLD = 0.015   # margin score[0]-score[1] ниже этого → неуверенность
    FLASH_TOP_CANDS  = 20      # сколько кандидатов передавать Flash
    _flash_tasks: list[dict]  = []
    _initial_results: dict[int, str] = {}  # seg_loop_idx → initial clip_id
    _gemini_cands_map: dict[int, list[str]] = {}  # seg_loop_idx → top candidates
    _prev_clip_desc: str = ""   # описание последнего выбранного клипа для Flash контекста
    _last_clip_id: str | None = None  # immediately preceding selected clip — никогда не повторять подряд

    for i, seg in enumerate(segments):
        seg_id       = seg.get("id", 0)
        seg_text     = seg.get("text", "")
        seg_start    = float(seg.get("start", 0))
        seg_duration = float(seg.get("end", 0)) - seg_start
        is_intro     = int(seg_id) in intro_seg_ids

        # [2] Скользящее окно: prev + current + next (5 сегментов для контекста)
        prev2_text = segments[i - 2].get("text", "") if i > 1 else ""
        prev_text  = segments[i - 1].get("text", "") if i > 0 else ""
        next_text  = segments[i + 1].get("text", "") if i < len(segments) - 1 else ""
        next2_text = segments[i + 2].get("text", "") if i < len(segments) - 2 else ""
        window_text = " ".join(filter(None, [prev2_text, prev_text, seg_text, next_text, next2_text]))

        # [3] Embedding главы для текущего сегмента
        ch_idx      = get_chapter_idx(seg_start, total_dur, N_CHAPTERS)
        chapter_vec = chapter_vecs[ch_idx] if chapter_vecs else None

        section = "INTRO" if is_intro else "MAIN"
        print(f"\n[{seg_id}][{section}] {seg_duration:.1f}s '{seg_text[:60]}'", flush=True)

        # ── Talking Head Detection ────────────────────────────────────────────
        # Если сегмент намекает на интервью/учёного — запоминаем для буста talking_head
        _INTERVIEW_KEYWORDS = {
            "interview", "scientist", "expert", "researcher", "professor",
            "говорит", "учёный", "интервью", "эксперт", "исследователь",
            "sagt", "forscher", "wissenschaftler", "experte", "interview",
            "says", "according to", "told", "explains", "stated",
        }
        _seg_text_lower = seg_text.lower()
        _is_interview_seg = any(kw in _seg_text_lower for kw in _INTERVIEW_KEYWORDS)

        # ── Stage 1: Recall ───────────────────────────────────────────────────
        # Если есть Gemini embeddings — используем их для recall (мультиязычно, 3072-dim)
        # Иначе fallback на E5 через match_segment_to_clip
        _gemini_margin_val = 1.0

        if _gemini_seg_embs is not None and _gemini_emb is not None and _gemini_ids:
            # Gemini cosine: (3072,) × (N_clips × 3072) → (N_clips,)
            _gq_vec = _gemini_seg_embs[i]
            _g_scores = _gemini_emb @ _gq_vec   # нормализованные → косинус напрямую

            # Множество допустимых clip_id (с учётом фильтров available_clips)
            _valid_cids_set = {cid for cid, _, _ in available_clips}

            # Строим ранжированный список: набираем 200 raw-top, применяем штрафы, сортируем
            _g_raw200: list[tuple[str, float, float]] = []  # (cid, raw_score, penalized_score)
            _g_sorted = np.argsort(_g_scores)[::-1]
            for _gidx in _g_sorted:
                _gcid = _gemini_ids[_gidx]
                if _gcid not in _valid_cids_set:
                    continue
                # Никогда не повторять тот же клип подряд (хард-блок на немедленный повтор)
                if _gcid == _last_clip_id:
                    continue
                _graw = float(_g_scores[_gidx])
                _gscore = _graw
                # Мягкий штраф за повторы (не хард-блок)
                _uses = video_used.get(_gcid, 0)
                if _uses >= max_repeats_in_video:
                    continue
                if _uses > 0:
                    _gscore *= (0.7 ** _uses)
                # Штраф за recency (последние 2 видео)
                _last = clip_last_used_idx.get(_gcid, -999)
                _recency_gap = current_video_idx - _last
                if _recency_gap <= 1:
                    _gscore *= 0.4
                elif _recency_gap == 2:
                    _gscore *= 0.7
                # Штраф за prev_clips
                if _gcid in prev_clips:
                    _gscore *= 0.8
                # Talking Head Boost: если сегмент — интервью, буст для human/talking_head клипов
                if _is_interview_seg:
                    _clip_cat = library["clips"].get(_gcid, {}).get("category", "")
                    if _clip_cat in ("human", "talking_head", "interview", "real"):
                        _gscore *= 1.35   # +35% для реальных людей в кадре
                _g_raw200.append((_gcid, _graw, _gscore))
                if len(_g_raw200) >= 200:
                    break

            # Сортируем по финальному (штрафному) скору
            _g_raw200.sort(key=lambda x: x[2], reverse=True)
            _g_cands: list[tuple[str, float]] = [(cid, sc) for cid, _, sc in _g_raw200[:50]]

            # Предупреждение если интервью-сегмент не нашёл talking_head в топ-10
            if _is_interview_seg:
                _top10_cats = [
                    library["clips"].get(cid, {}).get("category", "")
                    for cid, _ in _g_cands[:10]
                ]
                _has_human = any(c in ("human", "talking_head", "interview", "real")
                                 for c in _top10_cats)
                if not _has_human:
                    print(f"  [WARN] Interview seg but no human/real clip in top-10 "
                          f"— library may lack talking-head footage", flush=True)

            # Margin = разница между 1-м и 2-м кандидатом (по штрафному скору)
            if len(_g_cands) >= 2:
                _gemini_margin_val = _g_cands[0][1] - _g_cands[1][1]
            elif len(_g_cands) == 1:
                _gemini_margin_val = 1.0

            top_candidates = [cid for cid, _ in _g_cands]
            _gemini_cands_map[i] = top_candidates[:FLASH_TOP_CANDS]

            if top_candidates:
                _best_raw = _g_raw200[0][1] if _g_raw200 else 0.0
                _best_score = _g_cands[0][1]
                print(f"  [Gemini] top={top_candidates[0]} "
                      f"raw={_best_raw:.3f} penalized={_best_score:.3f} margin={_gemini_margin_val:.4f} "
                      f"({len(top_candidates)} cands)", flush=True)
        else:
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
                top_n=8,
                context_vec=video_context_vec,
                chapter_vec=chapter_vec,
                window_text=window_text,
                global_usage=global_usage,
                clip_last_used_idx=clip_last_used_idx,
                current_video_idx=current_video_idx,
                segment_start=seg_start,
                video_used_at=video_used_at,
                prev_video_centroid=prev_video_centroid,
                clip_tags=_clip_tags,
                query_tags=None,
            )

        # [4c] Хард-блок: немедленный повтор (тот же клип что и предыдущий сегмент)
        if _last_clip_id and top_candidates and top_candidates[0] == _last_clip_id:
            _no_repeat = [c for c in top_candidates if c != _last_clip_id]
            if _no_repeat:
                top_candidates = _no_repeat
                print(f"  ⚠ No-immediate-repeat → {top_candidates[0]}", flush=True)

        # [5a] E5 text diversity window (2 мин): cosine > 0.92 → берём следующего кандидата
        while prev_clip_embeddings and (seg_start - prev_clip_embeddings[0][0]) > EMB_DIVERSITY_WINDOW_S:
            prev_clip_embeddings.pop(0)
        clip_id = top_candidates[0] if top_candidates else None
        if clip_id and prev_clip_embeddings:
            idx = emb_index.get(clip_id)
            if idx is not None:
                for _ts, prev_vec in prev_clip_embeddings:
                    if float(clip_embeddings[idx] @ prev_vec) > 0.92 and len(top_candidates) > 1:
                        clip_id = top_candidates[1]
                        print(f"  ⚠ Emb-diversity → {clip_id}", flush=True)
                        break

        # [6] pHash window (5 мин): визуально идентичные кадры → берём следующего кандидата
        # Сначала вычищаем устаревшие записи (старше PHASH_WINDOW_S секунд)
        while recent_phashes and (seg_start - recent_phashes[0][0]) > PHASH_WINDOW_S:
            recent_phashes.pop(0)
        if clip_id and phash_map and recent_phashes:
            _recent_hashes = [rh for _ts, rh in recent_phashes]
            def _phash_blocked(cid: str) -> bool:
                h = phash_map.get(cid)
                if h is None:
                    return False
                return any(bin(h ^ rh).count("1") < PHASH_THRESHOLD for rh in _recent_hashes)
            if _phash_blocked(clip_id):
                # Перебираем кандидатов пока не найдём незаблокированного
                alts = [c for c in top_candidates if c != clip_id and not _phash_blocked(c)]
                if alts:
                    clip_id = alts[0]
                    print(f"  ⚠ pHash-duplicate → {clip_id}", flush=True)
                else:
                    # Все кандидаты заблокированы — берём любого кроме текущего
                    alts_any = [c for c in top_candidates if c != clip_id]
                    if alts_any:
                        clip_id = alts_any[0]
                        print(f"  ⚠ pHash-duplicate (no clean alt) → {clip_id}", flush=True)

        # ── Topic Change Detection ─────────────────────────────────────────────
        # Сравниваем embedding текущего сегмента с предыдущим.
        # Если косинусное сходство < порога → тема сменилась → обнуляем prev_clip_desc для Flash.
        TOPIC_SHIFT_THRESHOLD = 0.72   # ниже этого = смена темы
        _topic_changed = False
        if _gemini_seg_embs is not None and i > 0:
            _sim_to_prev = float(_gemini_seg_embs[i] @ _gemini_seg_embs[i - 1])
            if _sim_to_prev < TOPIC_SHIFT_THRESHOLD:
                _topic_changed = True
                print(f"  [TopicShift] sim_to_prev={_sim_to_prev:.3f} < {TOPIC_SHIFT_THRESHOLD} "
                      f"-> prev_clip reset", flush=True)

        # ── Stage 2: Margin Sampling → собрать задачи для Flash rerank ──────────
        if _flash_available and _gemini_seg_embs is not None and clip_id:
            _cands_for_flash = _gemini_cands_map.get(i, [])
            if _gemini_margin_val < MARGIN_THRESHOLD and len(_cands_for_flash) >= 2:
                _flash_tasks.append({
                    "seg_idx":        i,
                    "segment_text":   seg_text,
                    "candidates":     [(cid, _clip_desc_map.get(cid, "")) for cid in _cands_for_flash],
                    "prev_clip_desc": "" if _topic_changed else _prev_clip_desc,
                    "topic_changed":  _topic_changed,
                })
                print(f"  [Flash] queued: margin={_gemini_margin_val:.4f} "
                      f"(threshold {MARGIN_THRESHOLD})", flush=True)

        # Обновить окна: E5 text (2 мин) + pHash (5 мин)
        if clip_id:
            idx = emb_index.get(clip_id)
            if idx is not None:
                prev_clip_embeddings.append((seg_start, clip_embeddings[idx].copy()))
            if phash_map and clip_id in phash_map:
                recent_phashes.append((seg_start, phash_map[clip_id]))
            video_used[clip_id] = video_used.get(clip_id, 0) + 1
            if clip_id not in video_used_at:
                video_used_at[clip_id] = seg_start
            # Обновляем контекст для Flash reranker (описание текущего клипа)
            _prev_clip_desc = _clip_desc_map.get(clip_id, "")[:120]
            _last_clip_id = clip_id  # хард-блок немедленного повтора в следующем сегменте

        # Сохраняем начальный выбор (до Flash коррекции)
        _initial_results[i] = clip_id

        entry = (seg_id, clip_id, seg_duration)
        if is_intro:
            intro_clips.append(entry)
            intro_total += seg_duration
        else:
            main_clips.append(entry)
            main_total += seg_duration

    # ═══════════════════════════════════════════════════════════════════════════
    # Stage 3: Batch Flash rerank (параллельный, 16 workers)
    # ═══════════════════════════════════════════════════════════════════════════
    if _flash_available and _flash_tasks:
        print(f"\n⚡ Flash rerank: {len(_flash_tasks)} неуверенных сегментов...", flush=True)
        try:
            _flash_results = _rerank_batch(_flash_tasks)  # {seg_idx: clip_id}
            _flash_applied = 0

            # Строим быстрый lookup: seg_idx → (seg_id, is_intro, seg_duration)
            _seg_meta = {}
            for _si, _seg in enumerate(segments):
                _seg_meta[_si] = (
                    _seg.get("id", 0),
                    int(_seg.get("id", 0)) in intro_seg_ids,
                    float(_seg.get("end", 0)) - float(_seg.get("start", 0)),
                )

            for _si, _new_clip_id in _flash_results.items():
                _old_clip_id = _initial_results.get(_si)
                if not _new_clip_id or _new_clip_id == _old_clip_id:
                    continue

                _final_clip_id = _new_clip_id

                # Обновляем финальный результат
                _seg_id_v, _is_intro_v, _seg_dur_v = _seg_meta[_si]
                _final_clip_id = _new_clip_id

                # Откатываем старый video_used
                if _old_clip_id:
                    _old_cnt = video_used.get(_old_clip_id, 1)
                    if _old_cnt <= 1:
                        video_used.pop(_old_clip_id, None)
                    else:
                        video_used[_old_clip_id] = _old_cnt - 1

                # Обновляем video_used для нового
                video_used[_final_clip_id] = video_used.get(_final_clip_id, 0) + 1
                if _final_clip_id not in video_used_at:
                    _seg_start_v = float(segments[_si].get("start", 0))
                    video_used_at[_final_clip_id] = _seg_start_v

                # Патчим intro_clips / main_clips
                _target_list = intro_clips if _is_intro_v else main_clips
                for _li, (_eid, _ecid, _edur) in enumerate(_target_list):
                    if _eid == _seg_id_v:
                        _target_list[_li] = (_eid, _final_clip_id, _edur)
                        break

                _flash_applied += 1
                print(f"  ⚡ Flash [{_si}]: {_old_clip_id} → {_final_clip_id}", flush=True)

            print(f"✅ Flash rerank: применено {_flash_applied}/{len(_flash_tasks)} замен", flush=True)
        except Exception as _fex:
            print(f"⚠ Flash rerank ошибка: {_fex}", flush=True)

    # ── Финальный пост-процессинг: убираем оставшиеся consecutive duplicates ──
    # Бежим по всем клипам (intro + main как один список в порядке seg_id).
    # Если два соседних = одинаковый clip_id → меняем второй на следующего кандидата.
    _all_clips_merged = sorted(intro_clips + main_clips, key=lambda x: x[0])
    _consec_fixed = 0
    for _ci in range(1, len(_all_clips_merged)):
        _prev_sid, _prev_cid, _prev_dur = _all_clips_merged[_ci - 1]
        _cur_sid, _cur_cid, _cur_dur   = _all_clips_merged[_ci]
        if _cur_cid and _cur_cid == _prev_cid:
            # Ищем альтернативу из кандидатов для этого сегмента
            # Определяем seg_loop_idx по seg_id
            _cur_si = next((idx for idx, seg in enumerate(segments)
                            if seg.get("id", 0) == _cur_sid), None)
            _alts = [c for c in (_gemini_cands_map.get(_cur_si, []) if _cur_si is not None else [])
                     if c != _prev_cid]
            if _alts:
                _new_cid = _alts[0]
                # Patch в нужном списке
                _is_intro_ci = int(_cur_sid) in intro_seg_ids
                _target = intro_clips if _is_intro_ci else main_clips
                for _li, (_eid, _ecid, _edur) in enumerate(_target):
                    if _eid == _cur_sid:
                        _target[_li] = (_eid, _new_cid, _edur)
                        _all_clips_merged[_ci] = (_cur_sid, _new_cid, _cur_dur)
                        break
                _consec_fixed += 1
    if _consec_fixed:
        print(f"  [dedup] Исправлено consecutive-дублей: {_consec_fixed}", flush=True)

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

    # Сохраняем BLIP ITM кэш на диск
    _save_blip_cache()

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
