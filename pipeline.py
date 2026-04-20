"""
Video Pipeline — полный оркестратор.

Запуск одной командой:
  py pipeline.py --channel de
  py pipeline.py --channel fr --session Video_20260409_120000
  py pipeline.py --channel de --only assemble
  py pipeline.py --channel de --only thumbnail
  py pipeline.py --channel de --from thumbnail

Основной пайплайн:
  1. ASSEMBLE   — MP3/result.json + библиотека клипов -> final.mp4 (Gosha)
                  Whisper + Remotion MG запускаются внутри gosha автоматически
  2. THUMBNAIL  — thumbnail_text.txt -> thumbnail_final.png

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

"""

import argparse
import atexit
import os
import signal
import subprocess
import sys
import time
from pathlib import Path

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

BASE_DIR = Path(__file__).resolve().parent
PID_FILE = BASE_DIR / "pipeline.pid"

# ── Пути ─────────────────────────────────────────────────────────────────────
_UTILS_DIR = BASE_DIR / "agents" / "utils"
if str(_UTILS_DIR) not in sys.path:
    sys.path.insert(0, str(_UTILS_DIR))

from paths import (
    get_last_session,
    get_final_video, get_session_dir,
)

# ═══════════════════════════════════════════════════════════════════════════════
# Управление PID — защита от зомби-процессов
# ═══════════════════════════════════════════════════════════════════════════════

def _stop_pipeline(pid: int) -> None:
    """Убить процесс + всё дерево дочерних (taskkill /T)."""
    print(f"  Останавливаем pipeline PID={pid} и всё дерево процессов...")
    subprocess.run(
        ["taskkill", "/F", "/T", "/PID", str(pid)],
        capture_output=True,
    )


def _pid_is_running(pid: int) -> bool:
    """True если процесс с данным PID ещё жив."""
    result = subprocess.run(
        ["tasklist", "/FI", f"PID eq {pid}", "/NH"],
        capture_output=True, text=True,
    )
    return str(pid) in result.stdout


def _write_pid() -> None:
    PID_FILE.write_text(str(os.getpid()), encoding="utf-8")


def _remove_pid() -> None:
    try:
        PID_FILE.unlink(missing_ok=True)
    except Exception:
        pass


def _check_existing_pipeline() -> None:
    """Если pipeline.pid существует — убить старый инстанс."""
    if not PID_FILE.exists():
        return
    try:
        old_pid = int(PID_FILE.read_text(encoding="utf-8").strip())
    except Exception:
        PID_FILE.unlink(missing_ok=True)
        return

    if old_pid == os.getpid():
        return  # это мы сами

    if _pid_is_running(old_pid):
        print(f"\n⚠  Найден живой pipeline PID={old_pid} — завершаем его...")
        _stop_pipeline(old_pid)
        time.sleep(1.5)
    else:
        print(f"\n  (Удалён устаревший PID-файл от PID={old_pid})")

    PID_FILE.unlink(missing_ok=True)


# ── Маппинг alias ->channel_id ───────────────────────────────────────────────
_CH_ALIAS = {
    "de":  "channel_001_cosmos_de",
    "fr":  "channel_002_cosmos_fr",
    "es":  "channel_003_religion_es",
}

STAGES = ["assemble", "thumbnail"]

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

    # CREATE_NEW_PROCESS_GROUP — дочерний процесс получает свою группу,
    # что позволяет taskkill /T корректно убивать всё дерево при --stop
    _flags = subprocess.CREATE_NEW_PROCESS_GROUP if sys.platform == "win32" else 0

    proc = subprocess.Popen(
        cmd,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        encoding="utf-8",
        errors="replace",
        cwd=str(BASE_DIR),
        env=env,
        creationflags=_flags,
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


# ═══════════════════════════════════════════════════════════════════════════════
# Проверки артефактов
# ═══════════════════════════════════════════════════════════════════════════════

def _check_final(channel_id: str, session: str) -> bool:
    """final.mp4 существует и > 10MB."""
    final = get_final_video(channel_id, session)
    return final.exists() and final.stat().st_size > 10 * 1024 * 1024


# ═══════════════════════════════════════════════════════════════════════════════
# Стадии
# ═══════════════════════════════════════════════════════════════════════════════

def stage_assemble(channel_id: str, channel_alias: str, session: str | None) -> bool:
    """Запустить монтаж через gosha (clips + audio + subs ->final.mp4).
    session=None — gosha сам найдёт/создаст сессию из MP3 или последней сессии.
    """
    cmd = [_PYTHON, "agents/assembler/gosha_rubchinskiy.py", "--channel", channel_alias]
    if session:
        cmd += ["--session", session]
    return _run(cmd, "ASSEMBLE — Gosha ->clip selection + render ->final.mp4")


def _read_thumbnail_text(channel_id: str, session: str) -> str | None:
    """Читает thumbnail_text.txt из папки сессии. Возвращает сырой текст."""
    txt_path = get_session_dir(channel_id, session) / THUMBNAIL_TEXT_FILE
    if not txt_path.exists():
        return None
    lines = [l.strip() for l in txt_path.read_text(encoding="utf-8").splitlines()
             if l.strip() and not l.strip().startswith("#")]
    if not lines:
        return None
    return " ".join(lines)


def stage_thumbnail(channel_id: str, session: str, thumbnail_text: str) -> bool:
    """Генерация превью через thumbnail_agent."""
    cmd = [
        _PYTHON,
        "agents/thumbnail/thumbnail_agent.py",
        "--channel", channel_id,
        "--session", session,
        "--text", thumbnail_text,
    ]
    return _run(cmd, f"THUMBNAIL — '{thumbnail_text}' -> thumbnail_final.png")



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
    Полный пайплайн: assemble ->thumbnail.

    Каждая стадия проверяет артефакты и пропускается если уже выполнена.
    Транскрипция Whisper и Remotion MG выполняются внутри gosha автоматически.
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
    # 1. ASSEMBLE
    # Gosha сам создаёт сессию из MP3 (если session=None) и запускает Whisper
    # если result.json отсутствует.
    # ══════════════════════════════════════════════════════════════════════════
    if "assemble" in active_stages:
        if session and _check_final(channel_id, session):
            print(f"\n⏭ ASSEMBLE: final.mp4 уже есть")
        else:
            ok = stage_assemble(channel_id, channel_alias, session)
            if not ok:
                print("✗ Монтаж не удался.")
                return
            # Gosha мог создать новую сессию из MP3 — обновляем
            if not session:
                session = get_last_session(channel_id)

    # Если сессия всё ещё не определена — ищем последнюю
    if not session:
        session = get_last_session(channel_id)
    if not session:
        print("✗ Нет сессии — положи MP3 в корень канала и запусти снова.")
        return

    print(f"\n  Сессия: {session}")

    # Создаём thumbnail_text.txt в папке сессии если его ещё нет
    _thumb_txt = get_session_dir(channel_id, session) / THUMBNAIL_TEXT_FILE
    if not _thumb_txt.exists():
        _thumb_txt.write_text("# Строка 1: маленький текст сверху (напр. SOLAR-DATEN)\n# Строка 2: большой жирный текст (напр. VERNICHTET)\n", encoding="utf-8")
        print(f"\n  📝 Создан {_thumb_txt} — заполни текст для превью")

    # ══════════════════════════════════════════════════════════════════════════
    # 2. THUMBNAIL
    # ══════════════════════════════════════════════════════════════════════════
    if "thumbnail" in active_stages:
        thumb_dir = get_session_dir(channel_id, session) / "thumbnail"
        if (thumb_dir / "thumbnail_final.png").exists():
            print(f"\n⏭ THUMBNAIL: thumbnail_final.png уже есть")
        else:
            thumb_text = _read_thumbnail_text(channel_id, session)
            if thumb_text is None:
                print(
                    f"\n⏭ THUMBNAIL: заполни {get_session_dir(channel_id, session) / THUMBNAIL_TEXT_FILE}\n"
                    f"  Строка 1 = маленький текст сверху, строка 2 = большой жирный"
                )
            else:
                ok = stage_thumbnail(channel_id, session, thumb_text)
                if not ok:
                    print("⚠ Thumbnail не удался — продолжаем без превью.")

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
  py pipeline.py --channel de                      # полный пайплайн
  py pipeline.py --channel fr --session Video_xxx  # конкретная сессия
  py pipeline.py --channel de --only assemble      # только монтаж (+ Whisper + MG внутри)
  py pipeline.py --channel de --only thumbnail     # только превью
  py pipeline.py --stop                            # остановить текущий пайплайн
""",
    )
    parser.add_argument("--channel",
                        help="Канал: de | fr | es")
    parser.add_argument("--session",
                        help="Имя сессии (по умолчанию: последняя или новая из MP3)")
    parser.add_argument("--only", choices=STAGES,
                        help="Запустить только одну стадию")
    parser.add_argument("--from", dest="start_from", choices=STAGES,
                        help="Начать с указанной стадии")
    parser.add_argument("--stop", action="store_true",
                        help="Остановить текущий запущенный пайплайн")
    args = parser.parse_args()

    # ── --stop: убить текущий пайплайн ────────────────────────────────────────
    if args.stop:
        if not PID_FILE.exists():
            print("pipeline.pid не найден — пайплайн не запущен.")
            return
        try:
            pid = int(PID_FILE.read_text(encoding="utf-8").strip())
        except Exception:
            print("Не удалось прочитать pipeline.pid.")
            return
        if _pid_is_running(pid):
            _stop_pipeline(pid)
            print(f"  Pipeline PID={pid} остановлен.")
        else:
            print(f"  Pipeline PID={pid} уже не запущен.")
        PID_FILE.unlink(missing_ok=True)
        return

    if not args.channel:
        parser.error("--channel обязателен (если не используется --stop)")

    # ── Защита от зомби: убить старый инстанс, записать свой PID ─────────────
    _check_existing_pipeline()
    _write_pid()
    atexit.register(_remove_pid)

    def _handle_signal(sig, frame):
        _remove_pid()
        sys.exit(1)

    signal.signal(signal.SIGTERM, _handle_signal)
    try:
        signal.signal(signal.SIGBREAK, _handle_signal)   # Windows Ctrl+Break
    except (AttributeError, OSError):
        pass

    # ── Запуск пайплайна ──────────────────────────────────────────────────────
    try:
        run_pipeline(
            channel_alias=args.channel,
            session=args.session,
            only=args.only,
            start_from=args.start_from,
        )
    finally:
        _remove_pid()


if __name__ == "__main__":
    main()
