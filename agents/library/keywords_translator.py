"""
Переводит EN keywords на DE и FR через Claude API одним батчем.
Обновляет library.json.

Запуск:
  py agents/library/keywords_translator.py
"""
import json
import os
import subprocess
import sys
from pathlib import Path

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

_BASE_DIR    = Path(__file__).resolve().parent.parent.parent
LIBRARY_DIR  = _BASE_DIR / "library"
LIBRARY_JSON = LIBRARY_DIR / "library.json"


def _find_claude_exe() -> str | None:
    claude_dir = Path(os.environ.get("APPDATA", "")) / "Claude/claude-code"
    if not claude_dir.exists():
        return None
    for v in sorted(claude_dir.iterdir(), reverse=True):
        exe = v / "claude.exe"
        if exe.exists():
            return str(exe)
    return None


def _call_claude(prompt: str) -> str:
    exe = _find_claude_exe()
    if not exe:
        raise RuntimeError("claude.exe не найден")
    env = os.environ.copy()
    env.pop("CLAUDECODE", None)
    r = subprocess.run(
        [exe, "-p", prompt],
        capture_output=True, text=True,
        encoding="utf-8", errors="replace",
        env=env, timeout=120,
    )
    return r.stdout.strip()


def translate_keywords_batch(keywords_list: list[str], target_lang: str = "de") -> dict[int, str]:
    """
    Перевести список keywords через Claude.
    Один запрос = до 100 клипов.
    Возвращает {index: translated_keywords}.
    """
    lang_names = {"de": "German", "fr": "French"}
    lang_name  = lang_names.get(target_lang, "German")

    batch_text = "\n".join(
        f"{i}: {kw}"
        for i, kw in enumerate(keywords_list)
    )

    prompt = (
        f"Translate these English keywords to {lang_name}.\n"
        f"Keep proper names as-is (James Webb, NASA, Hubble etc).\n"
        f"Output keywords only, comma-separated.\n"
        f"Keep same format as input.\n\n"
        f"{batch_text}\n\n"
        f"Format:\n"
        f"0: translated, keywords, here\n"
        f"1: andere, schlüsselwörter\n"
        f"Output ONLY the translations, no other text."
    )

    translations: dict[int, str] = {}
    text = _call_claude(prompt)
    for line in text.split("\n"):
        line = line.strip()
        if not line or ":" not in line:
            continue
        parts = line.split(":", 1)
        try:
            idx        = int(parts[0].strip())
            translated = parts[1].strip()
            translations[idx] = translated
        except ValueError:
            continue

    return translations


def add_multilingual_keywords(batch_size: int = 100):
    """
    Добавить DE и FR keywords для всех клипов в library.json.
    Пропускает клипы у которых keywords_de уже есть.
    Сохраняет каждые 5 батчей.
    """
    with open(LIBRARY_JSON, encoding="utf-8") as f:
        library = json.load(f)

    clips = library.get("clips", {})

    to_translate = [
        (clip_id, entry)
        for clip_id, entry in clips.items()
        if entry.get("keywords")
        and not entry.get("keywords_de")
    ]

    total = len(to_translate)
    print(f"📝 Клипов для перевода: {total}", flush=True)

    if total == 0:
        print("✅ Все клипы уже переведены", flush=True)
        return

    processed = 0

    for i in range(0, total, batch_size):
        batch          = to_translate[i : i + batch_size]
        keywords_batch = [entry.get("keywords", "") for _, entry in batch]

        batch_num   = i // batch_size + 1
        total_batch = (total + batch_size - 1) // batch_size
        print(
            f"⏳ Батч {batch_num}/{total_batch} ({len(batch)} клипов)...",
            flush=True,
        )

        de_translations = translate_keywords_batch(keywords_batch, "de")
        fr_translations = translate_keywords_batch(keywords_batch, "fr")

        for j, (clip_id, entry) in enumerate(batch):
            if j in de_translations:
                entry["keywords_de"] = de_translations[j]
            if j in fr_translations:
                entry["keywords_fr"] = fr_translations[j]
            processed += 1

        # Сохранять каждые 5 батчей
        if batch_num % 5 == 0:
            with open(LIBRARY_JSON, "w", encoding="utf-8") as f:
                json.dump(library, f, indent=2, ensure_ascii=False)
            print(f"  💾 Сохранено: {processed}/{total}", flush=True)

    # Финальное сохранение
    with open(LIBRARY_JSON, "w", encoding="utf-8") as f:
        json.dump(library, f, indent=2, ensure_ascii=False)

    print(f"""
{'='*50}
✅ МУЛЬТИЯЗЫЧНЫЕ KEYWORDS ДОБАВЛЕНЫ
  Обработано: {processed}/{total}
  Языки: EN + DE + FR
{'='*50}
""", flush=True)


if __name__ == "__main__":
    add_multilingual_keywords(batch_size=100)
