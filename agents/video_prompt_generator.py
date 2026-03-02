"""
Video Prompt Generator Agent
------------------------------
1. Берёт видео мастер-промпт из config/video_master_prompt.txt
2. Берёт result.json из последней сессии data/transcripts/
3. Спрашивает платформу и генерирует промпты через claude CLI
4. Сохраняет data/prompts/Video_YYYYMMDD_HHMMSS/video_prompts.json + .txt

Запуск: py agents/video_prompt_generator.py
"""

import argparse
import io
import json
import os
import re
import subprocess
import sys
from pathlib import Path

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")

# Claude CLI в PATH
_CLAUDE_DIR = Path(os.environ.get("APPDATA", "")) / "Claude/claude-code"
if _CLAUDE_DIR.exists():
    versions = sorted(_CLAUDE_DIR.iterdir(), reverse=True)
    for v in versions:
        exe = v / "claude.exe"
        if exe.exists():
            os.environ["PATH"] = str(v) + os.pathsep + os.environ.get("PATH", "")
            break

BASE_DIR        = Path(__file__).parent.parent
TRANSCRIPTS_DIR = BASE_DIR / "data" / "transcripts"
PROMPTS_DIR     = BASE_DIR / "data" / "prompts"
MASTER_PROMPT_FILE = BASE_DIR / "config" / "video_master_prompt.txt"

BATCH_SIZE = 10

# ---------------------------------------------------------------------------
# Конфигурация платформ (видео)
# ---------------------------------------------------------------------------

PLATFORMS = {
    "1": {
        "name": "Google Flow / Veo 3",
        "instructions": (
            "Write prompts for AI video generation (Google Flow / Veo 3). "
            "Describe a dynamic cinematic scene: what moves, how the camera moves, lighting, atmosphere. "
            "Include motion verbs (drifts, rotates, glows, expands, etc.). "
            "20-50 words per prompt. End each prompt with ', animate it'."
        ),
        "suffix": ", animate it",
    },
    "2": {
        "name": "Runway Gen-4",
        "instructions": (
            "Write prompts for Runway Gen-4 video generation. "
            "Describe the scene, camera motion (slow push-in, pan left, orbit), and atmospheric details. "
            "Focus on mood, texture, and gradual transformation. "
            "20-50 words per prompt."
        ),
        "suffix": "",
    },
    "3": {
        "name": "Sora",
        "instructions": (
            "Write prompts for OpenAI Sora video generation. "
            "Describe the scene with rich physical detail: materials, light behaviour, spatial depth. "
            "Include subtle motion and camera perspective. "
            "30-60 words per prompt."
        ),
        "suffix": "",
    },
    "4": {
        "name": "Kling AI",
        "instructions": (
            "Write prompts for Kling AI video generation. "
            "Describe the visual scene, type of motion (slow, fast, zoom, rotation), "
            "and the overall cinematic mood. "
            "20-40 words per prompt."
        ),
        "suffix": "",
    },
    "5": {
        "name": "Другое",
        "instructions": (
            "Write detailed descriptive English video generation prompts. "
            "Include scene description, camera movement, lighting, mood, and motion details. "
            "20-50 words per prompt."
        ),
        "suffix": "",
    },
}


# ---------------------------------------------------------------------------
# Выбор платформы
# ---------------------------------------------------------------------------

def ask_platform(preset: str | None = None) -> dict:
    if preset and preset in PLATFORMS:
        platform = PLATFORMS[preset].copy()
        print(f"Платформа: {platform['name']}")
        return platform

    print("\nПод какую платформу генерировать видео-промпты?")
    for key, p in PLATFORMS.items():
        print(f"  {key}. {p['name']}")
    print()

    while True:
        choice = input("Введи номер (1-5): ").strip()
        if choice in PLATFORMS:
            platform = PLATFORMS[choice].copy()
            if choice == "5":
                name = input("Введи название платформы: ").strip()
                if name:
                    platform["name"] = name
            print(f"\nВыбрано: {platform['name']}")
            return platform
        print("  Неверный ввод, попробуй ещё раз.")


# ---------------------------------------------------------------------------
# Утилиты
# ---------------------------------------------------------------------------

def find_latest_session() -> str | None:
    if not TRANSCRIPTS_DIR.exists():
        return None
    sessions = sorted(
        (d.name for d in TRANSCRIPTS_DIR.iterdir()
         if d.is_dir() and d.name.startswith("Video_")),
        reverse=True,
    )
    return sessions[0] if sessions else None


def load_master_prompt() -> str:
    if not MASTER_PROMPT_FILE.exists():
        raise FileNotFoundError(f"Не найден: {MASTER_PROMPT_FILE}")
    return MASTER_PROMPT_FILE.read_text(encoding="utf-8").strip()


def load_segments(session: str) -> list[dict]:
    path = TRANSCRIPTS_DIR / session / "result.json"
    if not path.exists():
        raise FileNotFoundError(f"Не найден: {path}")
    with open(path, encoding="utf-8") as f:
        return json.load(f)


# ---------------------------------------------------------------------------
# Генерация через claude CLI
# ---------------------------------------------------------------------------

def build_batch_prompt(master: str, platform: dict, segments: list[dict]) -> str:
    lines = [
        f'{s["id"]}. [{s["start"]}s-{s["end"]}s] "{s["text"]}"'
        for s in segments
    ]
    return (
        f"You are a creative director for a cinematic documentary.\n\n"
        f"MASTER VIDEO STYLE (apply to every prompt):\n{master}\n\n"
        f"TARGET PLATFORM: {platform['name']}\n"
        f"PLATFORM INSTRUCTIONS:\n{platform['instructions']}\n\n"
        f"TASK:\n"
        f"For each numbered segment below, write one VIDEO generation prompt.\n"
        f"Rules:\n"
        f"- Translate the segment text to English to understand its meaning\n"
        f"- Create a visual VIDEO scene representing the concept or mood of the text\n"
        f"- Emphasize motion: camera movement, atmospheric dynamics, subtle animation\n"
        f"- Apply the master video style AND platform instructions to every prompt\n"
        f"- No quotation marks inside prompts, no segment numbering inside prompts\n"
        f"- Return ONLY a JSON array of strings, one per segment, in the same order\n\n"
        f"SEGMENTS:\n"
        + "\n".join(lines)
        + "\n\nReturn format (example for 3 segments):\n"
        f'["prompt one", "prompt two", "prompt three"]'
    )


def extract_json_array(text: str) -> list[str]:
    text = re.sub(r"```(?:json)?\s*", "", text).strip()
    start = text.find("[")
    end   = text.rfind("]") + 1
    if start == -1 or end == 0:
        raise ValueError(f"JSON-массив не найден в ответе:\n{text[:400]}")
    return json.loads(text[start:end])


def call_claude(prompt: str) -> str:
    env = os.environ.copy()
    env.pop("CLAUDECODE", None)

    result = subprocess.run(
        ["claude", "-p", prompt],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        env=env,
    )
    if result.returncode != 0:
        raise RuntimeError(
            f"claude CLI вернул код {result.returncode}:\n"
            f"STDERR: {result.stderr[:300]}\n"
            f"STDOUT: {result.stdout[:300]}"
        )
    return result.stdout.strip()


def generate_batch(master: str, platform: dict, segments: list[dict]) -> list[str]:
    prompt  = build_batch_prompt(master, platform, segments)
    raw     = call_claude(prompt)
    prompts = extract_json_array(raw)
    if len(prompts) != len(segments):
        raise ValueError(
            f"Ожидалось {len(segments)} промптов, получено {len(prompts)}\n"
            f"Ответ: {raw[:300]}"
        )
    return prompts


def generate_all(master: str, platform: dict, segments: list[dict]) -> list[str]:
    all_prompts: list[str] = []
    batches = [segments[i:i + BATCH_SIZE] for i in range(0, len(segments), BATCH_SIZE)]

    for idx, batch in enumerate(batches):
        start_id = batch[0]["id"]
        end_id   = batch[-1]["id"]
        print(f"  Батч {idx+1}/{len(batches)}: сегменты #{start_id}–#{end_id} ...", end=" ", flush=True)

        batch_prompts = generate_batch(master, platform, batch)
        all_prompts.extend(batch_prompts)
        print(f"OK ({len(batch_prompts)} промптов)")

    return all_prompts


# ---------------------------------------------------------------------------
# Сохранение
# ---------------------------------------------------------------------------

def apply_suffix(prompts: list[str], suffix: str) -> list[str]:
    if not suffix:
        return prompts
    return [p.rstrip().rstrip(",") + suffix for p in prompts]


def save_json(session: str, segments: list[dict], prompts: list[str]) -> Path:
    out_dir = PROMPTS_DIR / session
    out_dir.mkdir(parents=True, exist_ok=True)

    result = [
        {
            "id":           seg["id"],
            "start":        seg["start"],
            "end":          seg["end"],
            "text":         seg["text"],
            "video_prompt": video_prompt,
        }
        for seg, video_prompt in zip(segments, prompts)
    ]

    out_file = out_dir / "video_prompts.json"
    with open(out_file, "w", encoding="utf-8") as f:
        json.dump(result, f, ensure_ascii=False, indent=2)
    return out_file


def save_txt(session: str, prompts: list[str]) -> Path:
    out_dir = PROMPTS_DIR / session
    out_dir.mkdir(parents=True, exist_ok=True)

    out_file = out_dir / "video_prompts.txt"
    with open(out_file, "w", encoding="utf-8") as f:
        f.write("\n\n".join(p.strip() for p in prompts))
    return out_file


# ---------------------------------------------------------------------------
# Запуск
# ---------------------------------------------------------------------------

def run():
    parser = argparse.ArgumentParser(description="Генератор видео-промптов по сегментам")
    parser.add_argument("--project",  help="Имя папки сессии (по умолчанию — последняя)")
    parser.add_argument("--platform", choices=["1", "2", "3", "4", "5"],
                        help="Номер платформы без интерактивного вопроса")
    args = parser.parse_args()

    check = subprocess.run(
        ["claude", "--version"],
        capture_output=True, text=True,
    )
    if check.returncode != 0:
        print("ОШИБКА: claude CLI не найден.")
        sys.exit(1)

    session = args.project or find_latest_session()
    if not session:
        print("Нет сессий в data/transcripts/")
        sys.exit(1)

    print(f"Сессия  : {session}")

    master = load_master_prompt()
    print(f"Видео мастер-промпт: {len(master)} символов")

    segments = load_segments(session)
    print(f"Сегментов: {len(segments)}")

    platform = ask_platform(args.platform)

    print(f"\nГенерирую видео-промпты батчами по {BATCH_SIZE} сегментов через claude CLI...")
    prompts = generate_all(master, platform, segments)
    prompts = apply_suffix(prompts, platform["suffix"])

    json_path = save_json(session, segments, prompts)
    txt_path  = save_txt(session, prompts)

    print(f"\n--- Статистика ---")
    print(f"Платформа               : {platform['name']}")
    print(f"Сегментов в result.json : {len(segments)}")
    print(f"Промптов сгенерировано  : {len(prompts)}")
    print(f"Совпадают               : {'Да' if len(segments) == len(prompts) else 'НЕТ'}")
    print(f"\nJSON : {json_path}")
    print(f"TXT  : {txt_path}")


if __name__ == "__main__":
    run()
