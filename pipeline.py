"""
Video Pipeline — полный оркестратор.

Запуск одной командой:
  py pipeline.py --channel de
  py pipeline.py --channel fr --session Video_20260409_120000
  py pipeline.py --channel de --only assemble
  py pipeline.py --stop

Gosha (assemble) внутри себя делает всё:
  a. Whisper             — MP3 -> result.json (если отсутствует)
  b. Clip selection      — Gemini embeddings + Flash reranker -> клипы из библиотеки
  c. Remotion MG         — Gemini план + Remotion рендер (параллельно с clip selection)
  d. Thumbnail           — Pixel API + Gemini (параллельно, только DE/FR cosmos)
                           Нужен thumbnail_text.txt в папке сессии
  e. Timeline + trim     — тайминги по Whisper сегментам, trim клипов
  f. Audio mix           — voice + music (sidechain) + loudnorm
  g. Субтитры            — ASS karaoke (word-level Whisper timestamps)
  h. GPU рендер          — PyTorch CUDA compositing -> final.mp4
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

from paths import get_last_session, get_final_video

# ═══════════════════════════════════════════════════════════════════════════════
# Управление PID — защита от зомби-процессов
# ═══════════════════════════════════════════════════════════════════════════════

def _stop_pipeline(pid: int) -> None:
    print(f"  Останавливаем pipeline PID={pid} и всё дерево процессов...")
    subprocess.run(["taskkill", "/F", "/T", "/PID", str(pid)], capture_output=True)


def _pid_is_running(pid: int) -> bool:
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
    if not PID_FILE.exists():
        return
    try:
        old_pid = int(PID_FILE.read_text(encoding="utf-8").strip())
    except Exception:
        PID_FILE.unlink(missing_ok=True)
        return
    if old_pid == os.getpid():
        return
    if _pid_is_running(old_pid):
        print(f"\n⚠  Найден живой pipeline PID={old_pid} — завершаем его...")
        _stop_pipeline(old_pid)
        time.sleep(1.5)
    else:
        print(f"\n  (Удалён устаревший PID-файл от PID={old_pid})")
    PID_FILE.unlink(missing_ok=True)


# ── Маппинг alias -> channel_id ──────────────────────────────────────────────
_CH_ALIAS = {
    "de": "channel_001_cosmos_de",
    "fr": "channel_002_cosmos_fr",
    "es": "channel_003_religion_es",
    "us": "channel_004_cosmos_us",
}

_CH_META = {
    "channel_001_cosmos_de":  ("Cosmos DE",    "Cosmos"),
    "channel_002_cosmos_fr":  ("Cosmos FR",    "Cosmos"),
    "channel_003_religion_es":("Religion ES",  "Religion"),
    "channel_004_cosmos_us":  ("Cosmos US",    "Cosmos"),
}

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
    _flags = subprocess.CREATE_NEW_PROCESS_GROUP if sys.platform == "win32" else 0

    proc = subprocess.Popen(
        cmd,
        stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
        text=True, encoding="utf-8", errors="replace",
        cwd=str(BASE_DIR), env=env, creationflags=_flags,
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


def _check_final(channel_id: str, session: str) -> bool:
    final = get_final_video(channel_id, session)
    return final.exists() and final.stat().st_size > 10 * 1024 * 1024


# ═══════════════════════════════════════════════════════════════════════════════
# Оркестратор
# ═══════════════════════════════════════════════════════════════════════════════

def run_pipeline(
    channel_alias: str,
    session: str | None = None,
) -> None:
    channel_id = _CH_ALIAS.get(channel_alias, channel_alias)
    t_total = time.time()

    _ch_name, _ch_niche = _CH_META.get(channel_id, (channel_id, "—"))
    print(f"\n{'═'*60}")
    print(f"  VIDEO PIPELINE — {_ch_name}  [{_ch_niche}]")
    print(f"{'═'*60}")

    if session and _check_final(channel_id, session):
        print(f"\n⏭ ASSEMBLE: final.mp4 уже есть ({session})")
    else:
        cmd = [_PYTHON, "agents/assembler/gosha_rubchinskiy.py", "--channel", channel_alias]
        if session:
            cmd += ["--session", session]
        ok = _run(cmd, "ASSEMBLE — Gosha (Whisper + clips + MG + thumbnail + render)")
        if not ok:
            print("✗ Монтаж не удался.")
            return
        if not session:
            session = get_last_session(channel_id)

    if not session:
        session = get_last_session(channel_id)
    if not session:
        print("✗ Нет сессии.")
        return

    elapsed = time.time() - t_total
    m, s = divmod(int(elapsed), 60)
    h, m = divmod(m, 60)

    print(f"\n{'═'*60}")
    print(f"  PIPELINE DONE — {channel_id}  |  Сессия: {session}")
    final = get_final_video(channel_id, session)
    if final.exists():
        print(f"  Файл:  {final}  ({final.stat().st_size / 1024 / 1024:.1f} MB)")
    print(f"  Время: {f'{h}ч {m}м {s}с' if h else f'{m}м {s}с'}")
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
  py pipeline.py --stop                            # остановить текущий пайплайн
""",
    )
    parser.add_argument("--channel", help="Канал: de | fr | es")
    parser.add_argument("--session", help="Имя сессии (по умолчанию: последняя или новая из MP3)")
    parser.add_argument("--stop", action="store_true", help="Остановить текущий запущенный пайплайн")
    args = parser.parse_args()

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

    _check_existing_pipeline()
    _write_pid()
    atexit.register(_remove_pid)

    def _handle_signal(sig, frame):
        _remove_pid()
        sys.exit(1)

    signal.signal(signal.SIGTERM, _handle_signal)
    try:
        signal.signal(signal.SIGBREAK, _handle_signal)
    except (AttributeError, OSError):
        pass

    try:
        run_pipeline(channel_alias=args.channel, session=args.session)
    finally:
        _remove_pid()


if __name__ == "__main__":
    main()
