"""
Video Pipeline — полный оркестратор.

Запуск одной командой:
  py pipeline.py --channel de
  py pipeline.py --channel fr --session Video_20260409_120000
  py pipeline.py --channel de --only transcribe
  py pipeline.py --channel de --only assemble
  py pipeline.py --channel de --only motion_graphics
  py pipeline.py --channel de --from assemble
  py pipeline.py --channel de --from motion_graphics

Основной пайплайн:
  1. TRANSCRIBE       — MP3 -> session + result.json (Whisper large-v3-turbo)
  2. ASSEMBLE         — result.json + библиотека клипов -> final.mp4 (Gosha)
  3. THUMBNAIL        — script + thumbnail_text.txt -> thumbnail_final.png + seo_report.txt
  4. MOTION GRAPHICS  — final.mp4 + Remotion -> final.mp4 с анимированными вставками

Для превью положи текст в: data/channels/<lang>/thumbnail_text.txt
  Строка 1 = маленький текст сверху (например: TESS-DATEN)
  Строка 2 = большой жирный текст (например: VERBORGEN)

Gosha (assemble) внутри себя:
  a. Visual queries      — Claude Haiku -> result_visual.json (описания для CLIP-матчинга)
  b. Clip selection      — Gemini embeddings + Flash reranker -> клипы из библиотеки
  c. Timeline + trim     — тайминги по Whisper сегментам, trim клипов
  d. Audio mix           — voice + music (sidechain) + SFX + intro + loudnorm
  e. Субтитры            — ASS karaoke (word-level Whisper timestamps)
  f. GPU рендер          — PyTorch CUDA compositing -> final.mp4

Motion Graphics (mg) внутри себя:
  a. mg_planner_gemini   — Gemini 2.5 Flash читает скрипт -> motion_graphics_plan.json
  b. mg_renderer         — Remotion рендерит каждую зону -> zone_XX.mp4
  c. mg_injector         — патчит clip_selection.json с mg_overrides
  d. gosha (повторно)    — gosha использует mg_overrides -> final.mp4 с вставками
"""

import argparse
import json
import os
import subprocess
import sys
import time
from pathlib import Path

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

BASE_DIR = Path(__file__).resolve().parent

# ── Пути ─────────────────────────────────────────────────────────────────────
_UTILS_DIR = BASE_DIR / "agents" / "utils"
if str(_UTILS_DIR) not in sys.path:
    sys.path.insert(0, str(_UTILS_DIR))

from paths import (
    get_channel_dir, get_last_session,
    get_result_json, get_final_video, get_session_dir,
)

# ── Маппинг alias ->channel_id ───────────────────────────────────────────────
_CH_ALIAS = {
    "de":  "channel_001_cosmos_de",
    "fr":  "channel_002_cosmos_fr",
    "es":  "channel_003_religion_es",
    "fr2": "channel_004_cosmos_fr",
}

STAGES = ["transcribe", "assemble", "thumbnail", "motion_graphics"]

THUMBNAIL_TEXT_FILE = "thumbnail_text.txt"   # канал/de/thumbnail_text.txt


# ═══════════════════════════════════════════════════════════════════════════════
# Утилиты
# ═══════════════════════════════════════════════════════════════════════════════

# Python-интерпретатор для subprocess: системный Python (там все пакеты),
# а не venv (может не содержать faster_whisper, torch и др.)
_SYSTEM_PYTHON = r"C:\Users\Serafim\AppData\Local\Programs\Python\Python313\python.exe"
_PYTHON = _SYSTEM_PYTHON if Path(_SYSTEM_PYTHON).exists() else sys.executable


def _run(cmd: list[str], label: str) -> bool:
    """Запустить subprocess, стримить вывод в реалтайме."""
    print(f"\n{'='*60}")
    print(f"▶ {label}")
    print(f"  cmd: {' '.join(cmd)}")
    print(f"{'='*60}\n")

    t0 = time.time()
    env = os.environ.copy()
    env["PYTHONPATH"] = str(BASE_DIR) + os.pathsep + env.get("PYTHONPATH", "")

    proc = subprocess.Popen(
        cmd,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        encoding="utf-8",
        errors="replace",
        cwd=str(BASE_DIR),
        env=env,
    )
    for line in proc.stdout:
        sys.stdout.write(f"  | {line}")
        sys.stdout.flush()
    proc.wait()

    elapsed = time.time() - t0
    m, s = divmod(int(elapsed), 60)
    ok = proc.returncode == 0
    status = "OK" if ok else f"FAIL (code {proc.returncode})"
    print(f"\n  [{status}] {label} — {m}м {s}с")
    return ok


def _has_audio(channel_id: str) -> bool:
    """Есть ли MP3/WAV в корне канала (для создания новой сессии)."""
    ch_dir = get_channel_dir(channel_id)
    for ext in ("*.mp3", "*.wav", "*.m4a"):
        if list(ch_dir.glob(ext)):
            return True
    return False


# ═══════════════════════════════════════════════════════════════════════════════
# Проверки артефактов
# ═══════════════════════════════════════════════════════════════════════════════

def _check_mg_injected(channel_id: str, session: str) -> bool:
    """clip_selection.json содержит mg_overrides (вставки уже инжектированы)."""
    cs = get_session_dir(channel_id, session) / "clip_selection.json"
    if not cs.exists():
        return False
    try:
        data = json.loads(cs.read_text(encoding="utf-8"))
        return bool(data.get("mg_overrides"))
    except Exception:
        return False


def _check_transcription(channel_id: str, session: str) -> bool:
    """result.json существует и содержит сегменты."""
    rj = get_result_json(channel_id, session)
    if not rj.exists():
        return False
    try:
        data = json.loads(rj.read_text(encoding="utf-8"))
        return len(data.get("segments", [])) > 0
    except Exception:
        return False


def _check_final(channel_id: str, session: str) -> bool:
    """final.mp4 существует и > 10MB."""
    final = get_final_video(channel_id, session)
    return final.exists() and final.stat().st_size > 10 * 1024 * 1024


# ═══════════════════════════════════════════════════════════════════════════════
# Стадии
# ═══════════════════════════════════════════════════════════════════════════════

def stage_transcribe(channel_id: str) -> str | None:
    """Запустить транскрипцию, вернуть имя сессии."""
    cmd = [
        _PYTHON,
        "agents/transcriber/transcriber.py",
        "--channel", channel_id,
        "--mode", "random",
    ]
    ok = _run(cmd, "TRANSCRIBE — Whisper ->result.json")
    if not ok:
        return None
    return get_last_session(channel_id)


def stage_assemble(channel_id: str, channel_alias: str, session: str) -> bool:
    """Запустить монтаж через gosha (clips + audio + subs ->final.mp4)."""
    cmd = [
        _PYTHON,
        "agents/assembler/gosha_rubchinskiy.py",
        "--channel", channel_alias,
        "--session", session,
    ]
    return _run(cmd, "ASSEMBLE — Gosha ->clip selection + render ->final.mp4")


def _read_thumbnail_text(channel_id: str) -> tuple[str, str] | None:
    """Читает thumbnail_text.txt из папки канала. Формат: line1\\nline2"""
    txt_path = get_channel_dir(channel_id) / THUMBNAIL_TEXT_FILE
    if not txt_path.exists():
        return None
    lines = [l.strip() for l in txt_path.read_text(encoding="utf-8").splitlines() if l.strip()]
    if len(lines) < 2:
        print(f"⚠ {THUMBNAIL_TEXT_FILE}: нужно минимум 2 строки (text1 и text2)", flush=True)
        return None
    return lines[0], lines[1]


def stage_thumbnail(channel_id: str, session: str, text1: str, text2: str) -> bool:
    """Генерация превью через thumbnail_agent."""
    cmd = [
        _PYTHON,
        "agents/thumbnail/thumbnail_agent.py",
        "--channel", channel_id,
        "--session", session,
        "--text1", text1,
        "--text2", text2,
    ]
    return _run(cmd, f"THUMBNAIL — '{text1}' / '{text2}' -> thumbnail_final.png")


def stage_motion_graphics(channel_id: str, channel_alias: str, session: str) -> bool:
    """
    Стадия вставок (motion graphics):
      1. mg_planner_gemini.py  — планирует зоны -> motion_graphics_plan.json
      2. mg_renderer.py        — рендерит Remotion-композиции -> zone_XX.mp4
      3. mg_injector.py        — патчит clip_selection.json с mg_overrides
      4. удаляем final.mp4     — gosha перерендерит с вставками
      5. stage_assemble        — повторный монтаж (clip selection пропускается)
    """
    # 1. Планировщик
    ok = _run([
        _PYTHON,
        "agents/motion_graphics/mg_planner_gemini.py",
        "--channel", channel_alias,
        "--session", session,
    ], "MG PLAN — Gemini ->motion_graphics_plan.json")
    if not ok:
        print("⚠ MG planner не удался — вставки пропущены.")
        return False

    # 2. Рендер Remotion-зон
    ok = _run([
        _PYTHON,
        "agents/motion_graphics/mg_renderer.py",
        "--channel", channel_alias,
        "--session", session,
    ], "MG RENDER — Remotion ->zone_XX.mp4")
    if not ok:
        print("⚠ MG renderer не удался — вставки пропущены.")
        return False

    # 3. Патч clip_selection.json
    ok = _run([
        _PYTHON,
        "agents/motion_graphics/mg_injector.py",
        "--channel", channel_alias,
        "--session", session,
    ], "MG INJECT — патч clip_selection.json с mg_overrides")
    if not ok:
        print("⚠ MG injector не удался — вставки пропущены.")
        return False

    # 4. Удаляем final.mp4 чтобы gosha перерендерил
    final = get_final_video(channel_id, session)
    if final.exists():
        final.unlink()
        print(f"  🗑 Удалён {final.name} — gosha перерендерит с вставками")

    # 5. Повторный монтаж (gosha использует готовый clip_selection.json + mg_overrides)
    return stage_assemble(channel_id, channel_alias, session)


# ═══════════════════════════════════════════════════════════════════════════════
# Оркестратор
# ═══════════════════════════════════════════════════════════════════════════════

def run_pipeline(
    channel_alias: str,
    session: str | None = None,
    only: str | None = None,
    start_from: str | None = None,
) -> None:
    """
    Полный пайплайн: transcribe ->assemble.

    Каждая стадия проверяет артефакты и пропускается если уже выполнена.
    """
    channel_id = _CH_ALIAS.get(channel_alias, channel_alias)
    t_total = time.time()

    print(f"\n{'═'*60}")
    print(f"  VIDEO PIPELINE — {channel_id}")
    print(f"{'═'*60}")

    # ── Определяем какие стадии запускать ─────────────────────────────────
    if only:
        active_stages = [only]
    elif start_from:
        idx = STAGES.index(start_from)
        active_stages = STAGES[idx:]
    else:
        active_stages = list(STAGES)

    print(f"  Стадии: {' ->'.join(active_stages)}")

    # ══════════════════════════════════════════════════════════════════════════
    # 1. TRANSCRIBE
    # ══════════════════════════════════════════════════════════════════════════
    if "transcribe" in active_stages:
        if session and _check_transcription(channel_id, session):
            print(f"\n⏭ TRANSCRIBE: result.json уже есть ({session})")
        elif _has_audio(channel_id) or not session:
            new_session = stage_transcribe(channel_id)
            if new_session:
                session = new_session
            else:
                print("✗ Транскрипция не удалась.")
                return
        else:
            print(f"\n⏭ TRANSCRIBE: нет аудио в корне канала, используем сессию {session}")

    # Если сессия не определена — ищем последнюю
    if not session:
        session = get_last_session(channel_id)
    if not session:
        print("✗ Нет сессии — положи MP3 в корень канала и запусти снова.")
        return

    print(f"\n  Сессия: {session}")

    # ══════════════════════════════════════════════════════════════════════════
    # 2. ASSEMBLE
    # ══════════════════════════════════════════════════════════════════════════
    if "assemble" in active_stages:
        # Пропускаем если final.mp4 уже есть И НЕТ следующей стадии MG
        # (если запущены motion_graphics — final.mp4 будет удалён и перерендерен там)
        _mg_coming = "motion_graphics" in active_stages
        if _check_final(channel_id, session) and not _mg_coming:
            print(f"\n⏭ ASSEMBLE: final.mp4 уже есть")
        elif _check_final(channel_id, session) and _mg_coming:
            # final.mp4 есть — первый проход уже был, MG стадия сделает второй
            print(f"\n⏭ ASSEMBLE: final.mp4 уже есть (MG стадия перерендерит после вставок)")
        else:
            if not _check_transcription(channel_id, session):
                print("✗ Нет result.json — нужна транскрипция сначала.")
                return
            ok = stage_assemble(channel_id, channel_alias, session)
            if not ok:
                print("✗ Монтаж не удался.")
                return

    # ══════════════════════════════════════════════════════════════════════════
    # 3. THUMBNAIL
    # ══════════════════════════════════════════════════════════════════════════
    if "thumbnail" in active_stages:
        thumb_dir = get_session_dir(channel_id, session) / "thumbnail"
        if (thumb_dir / "thumbnail_final.png").exists():
            print(f"\n⏭ THUMBNAIL: thumbnail_final.png уже есть")
        else:
            thumb_text = _read_thumbnail_text(channel_id)
            if thumb_text is None:
                print(
                    f"\n⏭ THUMBNAIL: файл не найден или неполный — "
                    f"создай {get_channel_dir(channel_id) / THUMBNAIL_TEXT_FILE}\n"
                    f"  Формат: строка 1 = маленький текст, строка 2 = большой текст"
                )
            else:
                text1, text2 = thumb_text
                ok = stage_thumbnail(channel_id, session, text1, text2)
                if not ok:
                    print("⚠ Thumbnail не удался — продолжаем без превью.")

    # ══════════════════════════════════════════════════════════════════════════
    # 4. MOTION GRAPHICS
    # ══════════════════════════════════════════════════════════════════════════
    if "motion_graphics" in active_stages:
        if not _check_final(channel_id, session):
            print("✗ Нет final.mp4 — нужен assemble сначала.")
            return
        _injected  = _check_mg_injected(channel_id, session)
        if _injected and _check_final(channel_id, session):
            print(f"\n⏭ MOTION GRAPHICS: вставки уже применены")
        else:
            ok = stage_motion_graphics(channel_id, channel_alias, session)
            if not ok:
                print("⚠ Motion graphics не удались (финальное видео без вставок).")

    # ══════════════════════════════════════════════════════════════════════════
    # Итог
    # ══════════════════════════════════════════════════════════════════════════
    elapsed = time.time() - t_total
    m, s = divmod(int(elapsed), 60)
    h, m = divmod(m, 60)

    print(f"\n{'═'*60}")
    print(f"  PIPELINE DONE — {channel_id}")
    print(f"  Сессия: {session}")

    final = get_final_video(channel_id, session)
    if final.exists():
        size_mb = final.stat().st_size / 1024 / 1024
        print(f"  Файл:   {final}  ({size_mb:.1f} MB)")

    time_str = f"{h}ч {m}м {s}с" if h else f"{m}м {s}с"
    print(f"  Время:  {time_str}")
    print(f"{'═'*60}")


# ═══════════════════════════════════════════════════════════════════════════════
# CLI
# ═══════════════════════════════════════════════════════════════════════════════

def main():
    parser = argparse.ArgumentParser(
        description="Video Pipeline — полный оркестратор",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Примеры:
  py pipeline.py --channel de                                    # полный пайплайн
  py pipeline.py --channel fr --session Video_xxx                # конкретная сессия
  py pipeline.py --channel de --only transcribe                  # только транскрипция
  py pipeline.py --channel de --only assemble                    # только монтаж
  py pipeline.py --channel de --only thumbnail                   # только превью
  py pipeline.py --channel de --only motion_graphics             # только вставки
  py pipeline.py --channel de --from assemble                    # пропустить транскрипцию
  py pipeline.py --channel de --from thumbnail                   # только превью + вставки
  py pipeline.py --channel de --from motion_graphics             # только вставки (если final.mp4 есть)
""",
    )
    parser.add_argument("--channel", required=True,
                        help="Канал: de | fr | es")
    parser.add_argument("--session",
                        help="Имя сессии (по умолчанию: последняя или новая из MP3)")
    parser.add_argument("--only", choices=STAGES,
                        help="Запустить только одну стадию")
    parser.add_argument("--from", dest="start_from", choices=STAGES,
                        help="Начать с указанной стадии")
    args = parser.parse_args()

    run_pipeline(
        channel_alias=args.channel,
        session=args.session,
        only=args.only,
        start_from=args.start_from,
    )


if __name__ == "__main__":
    main()
