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

LIBRARY_DIR     = Path(__file__).resolve().parent.parent.parent / "library"
LIBRARY_JSON    = LIBRARY_DIR / "library.json"
HISTORY_FILE    = LIBRARY_DIR / "usage_history.json"
CLIPS_DIR       = LIBRARY_DIR / "clips"
EMBEDDINGS_FILE = LIBRARY_DIR / "embeddings.npz"

_EMBED_SERVER   = "http://127.0.0.1:8765"

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

def build_library_embeddings():
    """
    Предвычислить embeddings для всех проиндексированных клипов.
    Объединяет EN + DE + FR keywords для лучшего матчинга.
    Сохраняет в embeddings.npz (ключи: clip_ids, embeddings).
    """
    library = load_library()

    clips = [
        (clip_id, entry)
        for clip_id, entry in library["clips"].items()
        if entry.get("indexed", False)
        and not entry.get("rejected", False)
        and entry.get("keywords", "")
    ]

    clip_ids          = [c[0] for c in clips]
    combined_keywords = []
    for clip_id, entry in clips:
        kw_en = entry.get("keywords", "")
        kw_de = entry.get("keywords_de", "")
        kw_fr = entry.get("keywords_fr", "")
        combined = ", ".join(filter(None, [kw_en, kw_de, kw_fr]))
        combined_keywords.append(combined)

    print(
        f"⏳ Embeddings для {len(clips)} клипов...",
        flush=True,
    )

    passages = [f"passage: {kw}" for kw in combined_keywords]
    embeddings = _encode_local(passages, "passage")
    embeddings = embeddings / np.linalg.norm(embeddings, axis=1, keepdims=True)

    np.savez(
        EMBEDDINGS_FILE,
        clip_ids=np.array(clip_ids),
        embeddings=embeddings,
    )
    print(f"✅ Embeddings: {embeddings.shape}", flush=True)
    return clip_ids, embeddings


def load_library_embeddings() -> tuple[list[str], np.ndarray]:
    """
    Загрузить предвычисленные embeddings.
    Возвращает (clip_ids_list, embeddings_matrix).
    Если файла нет — пересчитывает.
    """
    if not EMBEDDINGS_FILE.exists():
        print("⚠️  embeddings.npz не найден — пересчитываем...", flush=True)
        return build_library_embeddings()

    data       = np.load(EMBEDDINGS_FILE, allow_pickle=True)
    ids        = list(data["clip_ids"])
    embeddings = data["embeddings"]
    print(f"✅ Embeddings загружены: {len(ids)} клипов", flush=True)
    return ids, embeddings


# ─── Загрузка данных ──────────────────────────

def load_library():
    with open(LIBRARY_JSON, encoding="utf-8") as f:
        return json.load(f)


def load_history():
    if HISTORY_FILE.exists():
        with open(HISTORY_FILE, encoding="utf-8") as f:
            return json.load(f)
    return {"videos": {}, "clip_usage": {}}


def save_history(history):
    with open(HISTORY_FILE, "w", encoding="utf-8") as f:
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


# ─── Penalty система ──────────────────────────

def calculate_penalty(
        clip_id,
        video_used,
        prev_video_clips,
        max_repeats_in_video=10,
        max_from_prev=20):
    """
    Рассчитать penalty для клипа.
      999  = полный запрет
      0.7  = большой штраф (повторка в видео)
      0.3  = малый штраф (из предыдущего видео)
      0.0  = нет штрафа (свежий клип)
    """
    used_count = video_used.get(clip_id, 0)

    if used_count >= 2:
        return 999

    total_repeats = sum(1 for cnt in video_used.values() if cnt > 1)
    if total_repeats >= max_repeats_in_video and used_count >= 1:
        return 999

    prev_overlap = sum(1 for c in video_used if c in prev_video_clips)
    if prev_overlap >= max_from_prev and clip_id in prev_video_clips and used_count == 0:
        return 999

    if used_count == 1:
        return 0.7

    if clip_id in prev_video_clips:
        return 0.3

    return 0.0


# ─── Стоп-слова (для Jaccard-части) ───────────

_STOPWORDS = {
    "observation", "observations", "mode", "initial", "simple", "unknown",
    "new", "first", "last", "observed", "detected", "detection", "analysis",
    "event", "process", "moment", "not", "no", "any", "all", "been", "was",
    "is", "are", "the", "a", "an", "and", "or", "of", "in", "on", "at",
    "to", "for", "with", "by", "from", "into", "through", "toward",
    "expected", "classical", "unusual", "classic", "known", "possible",
    "something", "nothing", "everything", "anything", "appears", "seems",
    "shows", "data", "image", "view", "seen", "visible",
}


def jaccard_similarity(text1: str, text2: str) -> float:
    """Jaccard по словам (без стоп-слов)."""
    w1 = set(text1.lower().replace(",", " ").split()) - _STOPWORDS
    w2 = set(text2.lower().replace(",", " ").split()) - _STOPWORDS
    if not w1 or not w2:
        return 0.0
    return len(w1 & w2) / len(w1 | w2)


# ─── Матчинг сегмента с библиотекой ──────────

def match_segment_to_clip(
        segment_text: str,
        keywords_list: list,          # (clip_id, keywords_en, duration)
        video_used: dict,
        prev_video_clips: set,
        clip_embeddings: np.ndarray,  # (N, dim) предвычисленные, нормализованные
        clip_ids_list: list[str],     # [clip_id, ...] в том же порядке
        max_repeats_in_video=10,
        max_from_prev=20,
        segment_duration=0.0,
        top_n: int = 3,
        jac_weight: float = 0.3):
    """
    Найти top_n лучших клипов по гибридному скорингу:
      hybrid = (1-jac_weight) × cosine_embedding + jac_weight × jaccard
    Использует query: префикс для e5 модели.
    Клип должен быть >= segment_duration.
    segment_text — на языке оригинала (FR/DE/EN).
    jac_weight=0.0 для каналов без keywords на языке сегмента (напр. FR без keywords_fr).
    Возвращает список clip_id (до top_n), или [] если ничего не найдено.
    """
    emb_weight    = 1.0 - jac_weight
    seg_vec       = encode_query(segment_text)
    cosine_scores = clip_embeddings @ seg_vec   # (N,) dot product = cosine
    emb_index     = {cid: i for i, cid in enumerate(clip_ids_list)}

    candidates = []
    for clip_id, keywords, clip_duration in keywords_list:
        if not keywords:
            continue
        if segment_duration > 0 and clip_duration > 0 and clip_duration < segment_duration:
            continue

        penalty = calculate_penalty(
            clip_id, video_used, prev_video_clips,
            max_repeats_in_video, max_from_prev,
        )
        if penalty == 999:
            continue

        idx       = emb_index.get(clip_id)
        emb_score = float(cosine_scores[idx]) if idx is not None else 0.0
        jac_score = jaccard_similarity(segment_text, keywords) if jac_weight > 0 else 0.0
        hybrid    = emb_weight * emb_score + jac_weight * jac_score

        candidates.append((clip_id, hybrid - penalty, hybrid, emb_score, jac_score, penalty, keywords, clip_duration))

    candidates.sort(key=lambda x: x[1], reverse=True)

    if not candidates:
        print(f"  ⚠️  Нет клипов >= {segment_duration:.1f}s → fallback", flush=True)
        fb = _fallback_match(
            segment_text, keywords_list, video_used, prev_video_clips,
            clip_embeddings, clip_ids_list, jac_weight=jac_weight,
        )
        return [fb] if fb else []

    best = candidates[0]
    print(
        f"  🎯 {best[0]} "
        f"hybrid={best[2]:.2f} emb={best[3]:.2f} jac={best[4]:.2f} "
        f"dur={best[7]:.1f}s (seg={segment_duration:.1f}s) penalty={best[5]:.1f}",
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
        jac_weight: float = 0.3):
    """Fallback: лучший по гибридному скору без учёта длины."""
    emb_weight    = 1.0 - jac_weight
    seg_vec       = encode_query(segment_text)
    cosine_scores = clip_embeddings @ seg_vec

    emb_index = {cid: i for i, cid in enumerate(clip_ids_list)}

    candidates = []
    for clip_id, keywords, clip_duration in keywords_list:
        if not keywords:
            continue
        if calculate_penalty(clip_id, video_used, prev_video_clips) == 999:
            continue

        idx       = emb_index.get(clip_id)
        emb_score = float(cosine_scores[idx]) if idx is not None else 0.0
        jac_score = jaccard_similarity(segment_text, keywords) if jac_weight > 0 else 0.0
        hybrid    = emb_weight * emb_score + jac_weight * jac_score

        candidates.append((clip_id, hybrid, emb_score, jac_score, clip_duration))

    if not candidates:
        return None

    candidates.sort(key=lambda x: x[1], reverse=True)
    best = candidates[0]
    print(
        f"  🔄 Fallback: {best[0]} "
        f"hybrid={best[1]:.2f} "
        f"emb={best[2]:.2f} "
        f"jac={best[3]:.2f} "
        f"dur={best[4]:.1f}s",
        flush=True,
    )
    return best[0]


# ─── Главная функция выбора клипов ────────────

def select_clips_for_video(
        session,
        channel_id,
        segments,
        max_repeats_in_video=10,
        max_from_prev=20,
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
    print(f"🌐 Канал: {channel_id}", flush=True)

    library = load_library()
    history = load_history()

    # Загрузить предвычисленные embeddings
    clip_ids_list, clip_embeddings = load_library_embeddings()

    # Клипы из предыдущего видео канала
    prev_clips = get_prev_video_clips(channel_id, history, n_prev=1)
    print(f"📋 Клипов из предыдущего видео: {len(prev_clips)}", flush=True)

    # Валидные клипы с EN+DE+FR keywords и длительностью
    available_clips = [
        (
            clip_id,
            ", ".join(filter(None, [
                entry.get("keywords", ""),
                entry.get("keywords_de", ""),
                entry.get("keywords_fr", ""),
            ])),
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

    # Определяем jac_weight: 0.0 если язык канала не имеет keywords в библиотеке
    _ch_lang = {"channel_001_cosmos_de": "de", "channel_002_cosmos_fr": "fr"}.get(channel_id, "")
    _kw_field = {"de": "keywords_de", "fr": "keywords_fr"}.get(_ch_lang, "")
    _has_lang_kw = _kw_field and any(
        entry.get(_kw_field) for entry in library["clips"].values()
    )
    jac_weight = 0.3 if (not _ch_lang or _has_lang_kw) else 0.0
    if _ch_lang:
        print(f"⚖️  Jaccard weight: {jac_weight} ({'keywords_' + _ch_lang + ' найдены' if _has_lang_kw else 'keywords_' + _ch_lang + ' отсутствуют → pure embedding'})", flush=True)

    video_used         = {}
    intro_clips        = []
    main_clips         = []
    intro_total        = 0.0
    main_total         = 0.0
    prev_clip_embedding: np.ndarray | None = None
    emb_index = {cid: i for i, cid in enumerate(clip_ids_list)}

    for seg in segments:
        seg_id       = seg.get("id", 0)
        seg_text     = seg.get("text", "")
        seg_duration = float(seg.get("end", 0)) - float(seg.get("start", 0))
        is_intro     = int(seg_id) in intro_seg_ids

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
            jac_weight=jac_weight,
        )

        # Diversity penalty: если лучший клип слишком похож на предыдущий — брать второй
        clip_id = top_candidates[0] if top_candidates else None
        if clip_id and prev_clip_embedding is not None:
            idx = emb_index.get(clip_id)
            if idx is not None:
                similarity = float(clip_embeddings[idx] @ prev_clip_embedding)
                if similarity > 0.92 and len(top_candidates) > 1:
                    alt = top_candidates[1]
                    print(
                        f"  ⚠ Diversity: {clip_id} похож на предыдущий "
                        f"(sim={similarity:.2f}) → заменяем на {alt}",
                        flush=True,
                    )
                    clip_id = alt

        # Запомнить embedding выбранного клипа для следующей итерации
        if clip_id:
            idx = emb_index.get(clip_id)
            prev_clip_embedding = clip_embeddings[idx].copy() if idx is not None else None
            video_used[clip_id] = video_used.get(clip_id, 0) + 1

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

    _save_video_to_history(
        session, channel_id,
        [c for _, c, _ in all_selected if c],
        history,
    )

    return {
        "intro_clips":    intro_clips,
        "main_clips":     main_clips,
        "intro_duration": intro_total,
        "main_duration":  main_total,
    }


def _save_video_to_history(session, channel_id, clips_used, history):
    """Сохранить использованные клипы в историю."""
    history["videos"][session] = {
        "channel":     channel_id,
        "date":        datetime.now().isoformat(),
        "clips_used":  clips_used,
        "total_clips": len(clips_used),
    }
    for clip_id in clips_used:
        history["clip_usage"][clip_id] = history["clip_usage"].get(clip_id, 0) + 1

    # Оставить только последние 50 видео
    videos = history["videos"]
    if len(videos) > 50:
        sorted_v = sorted(videos.keys(), key=lambda x: videos[x].get("date", ""), reverse=True)
        for old in sorted_v[50:]:
            del history["videos"][old]

    save_history(history)
    print(f"💾 История сохранена: {session}", flush=True)
