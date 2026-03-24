#!/usr/bin/env python3
"""
library_search.py — Поиск клипов из библиотеки по тексту сегмента

Два режима:
  1. Модуль: импортировать ClipMatcher и использовать в assembler
  2. CLI:    py agents/library/library_search.py --query "galaxy nebula stars"

Алгоритм:
  - Sentence-transformers (paraphrase-multilingual-MiniLM-L12-v2)
    для семантического сравнения текста сегмента с Vision-описаниями клипов
  - TF-IDF fallback если модель не загружена
  - Penalty за уже использованные клипы (--penalty, default 0.3)
  - TopN возвращает N лучших совпадений без повторов
  - Gap: минимальный интервал между повторным использованием клипа
"""

import argparse
import io
import json
import sys
from pathlib import Path
from typing import Optional

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")

BASE_DIR     = Path(__file__).resolve().parent.parent.parent
LIBRARY_JSON = BASE_DIR / "library" / "library.json"
CLIPS_DIR    = BASE_DIR / "library" / "clips"

# ── Sentence-transformer модель ───────────────────────────────────────────────

_MODEL_NAME  = "paraphrase-multilingual-MiniLM-L12-v2"  # 450MB, поддерживает RU/DE/EN
_embedder    = None
_clip_embeddings: dict[str, list[float]] = {}   # clip_id → embedding


def _load_embedder():
    global _embedder
    if _embedder is not None:
        return True
    try:
        from sentence_transformers import SentenceTransformer
        print(f"[LibSearch] Загрузка модели {_MODEL_NAME}...", flush=True)
        _embedder = SentenceTransformer(_MODEL_NAME)
        print("[LibSearch] ✅ Модель загружена", flush=True)
        return True
    except Exception as e:
        print(f"[LibSearch] ⚠️  sentence-transformers недоступен: {e}", flush=True)
        return False


def _embed(text: str) -> Optional[list[float]]:
    if not _load_embedder():
        return None
    import numpy as np
    vec = _embedder.encode(text, normalize_embeddings=True)
    return vec.tolist()


def _cosine(a: list[float], b: list[float]) -> float:
    import numpy as np
    a, b = np.array(a), np.array(b)
    denom = (np.linalg.norm(a) * np.linalg.norm(b))
    return float(np.dot(a, b) / denom) if denom > 0 else 0.0


# ── TF-IDF fallback ───────────────────────────────────────────────────────────

def _tfidf_score(query: str, clip: dict) -> float:
    """Простой TF-IDF-подобный скор по совпадению слов."""
    from collections import Counter
    import re

    def tokens(text: str) -> list[str]:
        return re.findall(r"[a-z]+", text.lower())

    q_tokens = set(tokens(query))
    if not q_tokens:
        return 0.0

    doc = " ".join(clip.get("blip_descriptions", []) + [clip.get("blip_tags", "")])
    d_tokens = tokens(doc)
    d_count  = Counter(d_tokens)
    d_total  = max(len(d_tokens), 1)

    score = 0.0
    for t in q_tokens:
        tf = d_count.get(t, 0) / d_total
        score += tf
    return score / len(q_tokens)


# ── Основной класс ────────────────────────────────────────────────────────────

class ClipMatcher:
    """
    Загружает library.json один раз, отвечает на запросы поиска.

    Использование:
        matcher = ClipMatcher()
        clips = matcher.find(segment_text, top_n=3)
        # → [{"clip_id": "0042", "file": "0042.mp4", "score": 0.87, ...}, ...]

    Учёт использованных клипов:
        matcher.mark_used("0042")          # вручную
        clips = matcher.find(text, top_n=1, penalty=0.4)  # снижает повторы
    """

    def __init__(
        self,
        library_json: Path = LIBRARY_JSON,
        clips_dir:    Path = CLIPS_DIR,
        precompute_embeddings: bool = False,
    ):
        self.clips_dir = clips_dir
        self._used: dict[str, int] = {}   # clip_id → сколько раз использован
        self._seq_counter = 0             # счётчик вызовов find() для gap

        print(f"[LibSearch] Загрузка библиотеки: {library_json}", flush=True)
        with open(library_json, encoding="utf-8") as f:
            lib = json.load(f)
        self.clips: dict[str, dict] = lib["clips"]

        # Оставляем только клипы с файлами на диске
        available = {k: v for k, v in self.clips.items()
                     if (clips_dir / v["file"]).exists()}
        missing = len(self.clips) - len(available)
        if missing:
            print(f"[LibSearch] ⚠️  {missing} клипов нет на диске — пропущены", flush=True)
        self.clips = available

        # Разделяем: с Vision-описаниями и без
        with_vision    = sum(1 for c in self.clips.values() if c.get("blip_descriptions"))
        without_vision = len(self.clips) - with_vision
        print(f"[LibSearch] ✅ {len(self.clips)} клипов "
              f"(с Vision: {with_vision}, без: {without_vision})", flush=True)

        # Предварительный расчёт эмбеддингов (опционально)
        if precompute_embeddings:
            self._precompute_all()

    def _precompute_all(self):
        """Вычислить эмбеддинги всех клипов заранее (ускоряет поиск)."""
        if not _load_embedder():
            return
        import numpy as np
        print(f"[LibSearch] Вычисляю эмбеддинги {len(self.clips)} клипов...", flush=True)
        ids, texts = [], []
        for clip_id, clip in self.clips.items():
            doc = self._clip_text(clip)
            ids.append(clip_id)
            texts.append(doc)

        vecs = _embedder.encode(texts, normalize_embeddings=True,
                                batch_size=64, show_progress_bar=True)
        for clip_id, vec in zip(ids, vecs):
            _clip_embeddings[clip_id] = vec.tolist()
        print("[LibSearch] ✅ Эмбеддинги готовы", flush=True)

    @staticmethod
    def _clip_text(clip: dict) -> str:
        """Сформировать текст клипа для сравнения."""
        parts = clip.get("blip_descriptions", [])
        tags  = clip.get("blip_tags", "")
        return " ".join(parts + ([tags] if tags else []))

    def _score(self, query: str, clip_id: str, clip: dict) -> float:
        """Вычислить схожесть запроса и клипа."""
        # Semantic (приоритет если Vision-описание есть)
        if clip.get("blip_descriptions"):
            if _clip_embeddings.get(clip_id):
                q_vec = _embed(query)
                if q_vec:
                    return _cosine(q_vec, _clip_embeddings[clip_id])
            # Без предварительных эмбеддингов — на лету
            if _load_embedder():
                import numpy as np
                doc_text = self._clip_text(clip)
                q_vec  = _embedder.encode(query,    normalize_embeddings=True)
                d_vec  = _embedder.encode(doc_text, normalize_embeddings=True)
                return float(np.dot(q_vec, d_vec))

        # TF-IDF fallback
        return _tfidf_score(query, clip)

    def find(
        self,
        query:   str,
        top_n:   int  = 5,
        penalty: float = 0.3,
        gap:     int  = 0,
        has_text_ok: bool = False,
    ) -> list[dict]:
        """
        Найти top_n клипов по запросу.

        Args:
            query:       текст сегмента (любой язык)
            top_n:       сколько клипов вернуть
            penalty:     штраф за каждое предыдущее использование клипа (0..1)
            gap:         минимальный интервал между повторным использованием
                         (0 = без ограничений)
            has_text_ok: включать ли клипы с текстом на экране

        Returns:
            list of dicts: [{
                "clip_id", "file", "path", "score",
                "duration", "blip_tags", "blip_descriptions"
            }]
        """
        self._seq_counter += 1
        results = []

        for clip_id, clip in self.clips.items():
            # Фильтр: текст на экране
            if not has_text_ok and clip.get("has_text"):
                continue

            # Gap-ограничение
            if gap > 0:
                last_used = self._used.get(clip_id, -gap - 1)
                if isinstance(last_used, int) and (self._seq_counter - last_used) < gap:
                    continue

            base_score = self._score(query, clip_id, clip)

            # Штраф за повторное использование
            use_count  = self._used.get(clip_id, 0) if isinstance(self._used.get(clip_id), int) else 0
            adj_score  = base_score * (1.0 - penalty * use_count)

            results.append({
                "clip_id":           clip_id,
                "file":              clip["file"],
                "path":              str(self.clips_dir / clip["file"]),
                "score":             round(adj_score, 4),
                "base_score":        round(base_score, 4),
                "duration":          clip.get("duration", 0),
                "width":             clip.get("width", 1920),
                "height":            clip.get("height", 1080),
                "blip_tags":         clip.get("blip_tags", ""),
                "blip_descriptions": clip.get("blip_descriptions", []),
                "has_text":          clip.get("has_text", False),
            })

        results.sort(key=lambda x: x["score"], reverse=True)
        return results[:top_n]

    def mark_used(self, clip_id: str):
        """Пометить клип как использованный (увеличивает penalty при следующем поиске)."""
        if isinstance(self._used.get(clip_id), int):
            self._used[clip_id] += 1
        else:
            self._used[clip_id] = self._seq_counter

    def find_for_segments(
        self,
        segments:    list[dict],
        top_n:       int   = 1,
        penalty:     float = 0.3,
        gap:         int   = 5,
        has_text_ok: bool  = False,
    ) -> list[dict]:
        """
        Подобрать клипы для списка сегментов.

        Args:
            segments: [{"id": 1, "text": "..."}, ...]

        Returns:
            [{"id", "text", "clip": {...найденный клип...}}, ...]
        """
        matched = []
        for seg in segments:
            query   = seg.get("text", "")
            results = self.find(query, top_n=top_n, penalty=penalty,
                                gap=gap, has_text_ok=has_text_ok)
            best = results[0] if results else None
            if best:
                self.mark_used(best["clip_id"])

            matched.append({
                "id":   seg.get("id", len(matched) + 1),
                "text": query,
                "clip": best,
            })

        return matched


# ── CLI ───────────────────────────────────────────────────────────────────────

def _cli():
    parser = argparse.ArgumentParser(description="Поиск клипов по тексту")
    parser.add_argument("--query",    "-q", required=True, help="Текст запроса")
    parser.add_argument("--top",      "-n", type=int, default=5, help="Сколько клипов вернуть")
    parser.add_argument("--penalty",  "-p", type=float, default=0.3, help="Штраф за повтор")
    parser.add_argument("--library",  "-l", default=str(LIBRARY_JSON), help="Путь к library.json")
    parser.add_argument("--precompute", action="store_true", help="Предвычислить все эмбеддинги")
    parser.add_argument("--segments-json", "-s", help="JSON-файл с сегментами [{id, text}]")
    args = parser.parse_args()

    matcher = ClipMatcher(
        library_json=Path(args.library),
        precompute_embeddings=args.precompute,
    )

    if args.segments_json:
        with open(args.segments_json, encoding="utf-8") as f:
            segments = json.load(f)
        results = matcher.find_for_segments(segments, top_n=args.top, penalty=args.penalty)
        print(f"\n{'═'*60}")
        print(f"ПОДБОР КЛИПОВ для {len(segments)} сегментов")
        print(f"{'═'*60}")
        for r in results:
            clip = r["clip"]
            if clip:
                print(f"\n[{r['id']:03d}] {r['text'][:60]}...")
                print(f"       → {clip['file']}  score={clip['score']:.3f}  {clip['duration']:.1f}s")
                print(f"         {clip['blip_tags'][:70]}")
            else:
                print(f"\n[{r['id']:03d}] ❌ клип не найден")
    else:
        results = matcher.find(args.query, top_n=args.top, penalty=args.penalty)
        print(f"\n{'═'*60}")
        print(f"РЕЗУЛЬТАТЫ для: '{args.query}'")
        print(f"{'═'*60}")
        for i, r in enumerate(results, 1):
            print(f"\n#{i}  {r['file']}  score={r['score']:.3f}  ({r['duration']:.1f}s)")
            if r["blip_descriptions"]:
                for desc in r["blip_descriptions"]:
                    print(f"    {desc}")
            else:
                print(f"    tags: {r['blip_tags']}")


if __name__ == "__main__":
    _cli()
