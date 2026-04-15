"""
translator.py — локальный переводчик Helsinki-NLP/opus-mt для clip matching.

Переводит текст сегментов (DE/FR/ES) в английский перед E5 и CLIP матчингом,
чтобы избежать кросс-лингвального мисматча.

Синглтон с кешем: одна загрузка модели на процесс, повторные переводы бесплатны.
"""

import re
from functools import lru_cache
from typing import Optional

import torch

# lang_code -> Helsinki model name
_MODEL_MAP = {
    "de": "Helsinki-NLP/opus-mt-de-en",
    "fr": "Helsinki-NLP/opus-mt-fr-en",
    "es": "Helsinki-NLP/opus-mt-es-en",
}

_models: dict = {}   # lang -> (tokenizer, model)
_cache:  dict = {}   # (lang, text) -> translated


def _get_model(lang: str):
    if lang not in _models:
        model_name = _MODEL_MAP.get(lang)
        if not model_name:
            return None, None
        try:
            from transformers import MarianMTModel, MarianTokenizer
            import warnings, os
            os.environ.setdefault("HF_HUB_DISABLE_SYMLINKS_WARNING", "1")
            with warnings.catch_warnings():
                warnings.simplefilter("ignore")
                tok = MarianTokenizer.from_pretrained(model_name)
                mdl = MarianMTModel.from_pretrained(model_name)
            device = "cuda" if torch.cuda.is_available() else "cpu"
            mdl = mdl.to(device)
            mdl.eval()
            _models[lang] = (tok, mdl, device)
            print(f"[Translator] {lang}->en loaded on {device}", flush=True)
        except Exception as e:
            print(f"[Translator] Failed to load {model_name}: {e}", flush=True)
            _models[lang] = None
    return _models.get(lang)


def translate(text: str, lang: str) -> str:
    """Перевести text с lang на английский. Возвращает оригинал если не удалось."""
    if not text or not text.strip():
        return text
    if lang == "en" or lang not in _MODEL_MAP:
        return text

    cache_key = (lang, text)
    if cache_key in _cache:
        return _cache[cache_key]

    entry = _get_model(lang)
    if not entry:
        return text
    tok, mdl, device = entry

    try:
        inputs = tok([text], return_tensors="pt", padding=True,
                     truncation=True, max_length=512).to(device)
        with torch.no_grad():
            out = mdl.generate(**inputs, num_beams=4, max_length=512)
        result = tok.decode(out[0], skip_special_tokens=True)
        _cache[cache_key] = result
        return result
    except Exception as e:
        _cache[cache_key] = text
        return text


def translate_batch(texts: list[str], lang: str) -> list[str]:
    """Батчевый перевод — эффективнее чем по одному."""
    if not texts or lang == "en" or lang not in _MODEL_MAP:
        return texts

    # Разбить на те что в кеше и те что нужно переводить
    results = [None] * len(texts)
    to_translate = []   # (original_idx, text)

    for i, t in enumerate(texts):
        key = (lang, t)
        if key in _cache:
            results[i] = _cache[key]
        elif not t or not t.strip():
            results[i] = t
        else:
            to_translate.append((i, t))

    if not to_translate:
        return results

    entry = _get_model(lang)
    if not entry:
        for i, t in to_translate:
            results[i] = t
        return results

    tok, mdl, device = entry

    try:
        batch_texts = [t for _, t in to_translate]
        inputs = tok(batch_texts, return_tensors="pt", padding=True,
                     truncation=True, max_length=512).to(device)
        with torch.no_grad():
            out = mdl.generate(**inputs, num_beams=4, max_length=512)
        translated = [tok.decode(o, skip_special_tokens=True) for o in out]
        for (orig_idx, orig_text), trans in zip(to_translate, translated):
            _cache[(lang, orig_text)] = trans
            results[orig_idx] = trans
    except Exception as e:
        for i, t in to_translate:
            results[i] = t

    return results


def lang_from_channel(channel_id: str) -> str:
    """channel_001_cosmos_de -> de"""
    m = re.search(r"_(de|fr|es)$", channel_id)
    return m.group(1) if m else "en"
