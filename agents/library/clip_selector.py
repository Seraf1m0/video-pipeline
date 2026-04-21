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
    get_usage_history,
    get_niche,
    get_lang,
)

def _combine_keywords(entry: dict) -> str:
    """Объединить все доступные языковые keywords в одну строку."""
    return ", ".join(filter(None, [
        entry.get("keywords", ""),
        entry.get("keywords_de", ""),
        entry.get("keywords_fr", ""),
        entry.get("keywords_es", ""),
    ]))


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
        print(f"⚠ Gemini embeddings недоступны: {_ge}", flush=True)


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

    video_used         = {}
    video_used_at      = {}   # {clip_id: first_use_time_s} — для in-video repeat gate
    intro_clips        = []
    main_clips         = []
    intro_total        = 0.0
    main_total         = 0.0
    recent_phashes:      list[tuple[float, int]]         = []   # pHash window (5 мин)
    PHASH_WINDOW_S          = 300   # 5 минут — окно pHash по времени
    PHASH_THRESHOLD         = 12    # hamming distance < 12 из 64 бит = визуально идентичны

    _lang = get_lang(channel_id)

    # ── Gemini batch embedding всех сегментов (один API call) ─────────────────
    _gemini_seg_embs: np.ndarray | None = None
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
            print(f"⚠ Gemini batch embed сегментов: {_gbe}", flush=True)
            _gemini_seg_embs = None

    _prev_clip_desc: str = ""   # описание последнего выбранного клипа (для контекста запроса)
    _last_clip_id: str | None = None  # immediately preceding selected clip — никогда не повторять подряд

    for i, seg in enumerate(segments):
        seg_id       = seg.get("id", 0)
        seg_text     = seg.get("text", "")
        seg_start    = float(seg.get("start", 0))
        seg_duration = float(seg.get("end", 0)) - seg_start
        is_intro     = int(seg_id) in intro_seg_ids

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
        _gemini_margin_val = 1.0
        top_candidates: list[str] = []

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
                # Повторы: хард-блок после 2 использований, мягкий штраф для 1-го
                _uses = video_used.get(_gcid, 0)
                if _uses >= 2:
                    continue
                if _uses >= max_repeats_in_video:
                    continue
                if _uses > 0:
                    _gscore *= 0.5
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

            if top_candidates:
                _best_raw = _g_raw200[0][1] if _g_raw200 else 0.0
                _best_score = _g_cands[0][1]
                print(f"  [Gemini] top={top_candidates[0]} "
                      f"raw={_best_raw:.3f} penalized={_best_score:.3f} margin={_gemini_margin_val:.4f} "
                      f"({len(top_candidates)} cands)", flush=True)
        # [4c] Хард-блок: немедленный повтор (тот же клип что и предыдущий сегмент)
        if _last_clip_id and top_candidates and top_candidates[0] == _last_clip_id:
            _no_repeat = [c for c in top_candidates if c != _last_clip_id]
            if _no_repeat:
                top_candidates = _no_repeat
                print(f"  ⚠ No-immediate-repeat → {top_candidates[0]}", flush=True)

        clip_id = top_candidates[0] if top_candidates else None

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

        if clip_id:
            if phash_map and clip_id in phash_map:
                recent_phashes.append((seg_start, phash_map[clip_id]))
            video_used[clip_id] = video_used.get(clip_id, 0) + 1
            if clip_id not in video_used_at:
                video_used_at[clip_id] = seg_start
            _prev_clip_desc = library["clips"].get(clip_id, {}).get("keywords", "")[:120]
            _last_clip_id = clip_id  # хард-блок немедленного повтора в следующем сегменте

        entry = (seg_id, clip_id, seg_duration)
        if is_intro:
            intro_clips.append(entry)
            intro_total += seg_duration
        else:
            main_clips.append(entry)
            main_total += seg_duration

    # ── Финальный пост-процессинг: убираем consecutive duplicates ──────────────
    _all_clips_merged = sorted(intro_clips + main_clips, key=lambda x: x[0])
    _consec_fixed = 0
    for _ci in range(1, len(_all_clips_merged)):
        _prev_sid, _prev_cid, _prev_dur = _all_clips_merged[_ci - 1]
        _cur_sid, _cur_cid, _cur_dur   = _all_clips_merged[_ci]
        if _cur_cid and _cur_cid == _prev_cid:
            _cur_si = next((idx for idx, seg in enumerate(segments)
                            if seg.get("id", 0) == _cur_sid), None)
            _g_top = [cid for cid, _ in (
                sorted(
                    [(c, float(_gemini_emb[_gemini_ids.index(c)] @ _gemini_seg_embs[_cur_si]))
                     for c in list(video_used.keys() | {_prev_cid})
                     if c in (_gemini_ids or []) and c != _prev_cid],
                    key=lambda x: x[1], reverse=True
                ) if _gemini_seg_embs is not None and _gemini_ids and _cur_si is not None else []
            )]
            if _g_top:
                _new_cid = _g_top[0]
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
