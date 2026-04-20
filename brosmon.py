"""
BrosMon — Video Pipeline Watchdog & Process Monitor

Запуск:
  py brosmon.py --channel de                       # наблюдение + авто-рестарт
  py brosmon.py --channel de --session Video_xxx   # конкретная сессия
  py brosmon.py --channel de --no-restart          # только наблюдение
  py brosmon.py --stop                             # остановить brosmon

Что делает:
  - Следит за pipeline.pid — жив ли pipeline
  - Сканирует дерево процессов (ffmpeg, node, python, whisper)
  - Убивает зависшие/зомби-процессы превысившие таймаут
  - При падении pipeline — перезапускает с --session (до MAX_RETRIES раз)
  - Мониторит прогресс файловой системы (нет изменений → stall)
  - Проверяет место на диске и GPU-память
  - Выводит rolling-статус в реальном времени
"""

import argparse
import os
import re
import subprocess
import sys
import time
from datetime import datetime, timedelta
from pathlib import Path

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

# ── Пути ─────────────────────────────────────────────────────────────────────
BASE_DIR          = Path(__file__).resolve().parent
PIPELINE_PID_FILE = BASE_DIR / "pipeline.pid"
BROSMON_PID_FILE  = BASE_DIR / "brosmon.pid"

_UTILS_DIR = BASE_DIR / "agents" / "utils"
if str(_UTILS_DIR) not in sys.path:
    sys.path.insert(0, str(_UTILS_DIR))

from paths import get_session_dir, get_final_video, get_last_session, TEMP_ROOT  # noqa

# ── Config ────────────────────────────────────────────────────────────────────
POLL_INTERVAL   = 12      # секунд между итерациями
STALL_WARN_MIN  = 8       # предупреждение если нет прогресса N минут
STALL_KILL_MIN  = 25      # рестарт pipeline если нет прогресса N минут
MAX_RETRIES     = 3       # максимум рестартов
DISK_WARN_GB    = 10      # предупреждение если свободно < N GB
GPU_WARN_MB     = 500     # предупреждение если свободно < N MB

# Таймаут (минуты) до принудительного kill по имени процесса
PROC_KILL_MIN = {
    "ffmpeg":   35,
    "node":     75,
    "npx":      75,
    "python":  110,
    "py":      110,
}
DEFAULT_KILL_MIN = 55

_CH_ALIAS = {
    "de": "channel_001_cosmos_de",
    "fr": "channel_002_cosmos_fr",
    "es": "channel_003_religion_es",
}

_SYSTEM_PYTHON = r"C:\Users\Serafim\AppData\Local\Programs\Python\Python313\python.exe"
_PYTHON = _SYSTEM_PYTHON if Path(_SYSTEM_PYTHON).exists() else sys.executable


# ═══════════════════════════════════════════════════════════════════════════════
# Logging
# ═══════════════════════════════════════════════════════════════════════════════

_LOG_FILE: Path | None = None

def _ts() -> str:
    return datetime.now().strftime("%H:%M:%S")

def log(msg: str, level: str = "INFO") -> None:
    icons = {"INFO": "·", "OK": "✅", "WARN": "⚠", "KILL": "💀", "RESTART": "🔄", "DEAD": "💥", "DISK": "💾", "GPU": "🎮"}
    icon = icons.get(level, "·")
    line = f"[{_ts()}] {icon} {msg}"
    print(line, flush=True)
    if _LOG_FILE:
        try:
            with open(_LOG_FILE, "a", encoding="utf-8") as f:
                f.write(line + "\n")
        except Exception:
            pass


# ═══════════════════════════════════════════════════════════════════════════════
# Windows process utilities
# ═══════════════════════════════════════════════════════════════════════════════

def _pid_alive(pid: int) -> bool:
    r = subprocess.run(
        ["tasklist", "/FI", f"PID eq {pid}", "/NH"],
        capture_output=True, text=True, errors="replace",
    )
    return str(pid) in r.stdout


def _kill_pid(pid: int) -> bool:
    r = subprocess.run(
        ["taskkill", "/F", "/T", "/PID", str(pid)],
        capture_output=True,
    )
    return r.returncode == 0


def _wmic(query: str) -> list[dict]:
    """Выполняет wmic запрос, возвращает список dict."""
    r = subprocess.run(
        ["wmic"] + query.split() + ["get", "/format:csv"],
        capture_output=True, text=True, errors="replace",
    )
    lines = [l.strip() for l in r.stdout.splitlines() if l.strip()]
    if not lines:
        return []
    # Первая строка — заголовки (Node,Field1,Field2,...)
    headers = [h.strip() for h in lines[0].split(",")]
    rows = []
    for line in lines[1:]:
        parts = line.split(",")
        if len(parts) == len(headers):
            rows.append({h: v.strip() for h, v in zip(headers, parts)})
    return rows


def _parse_wmic_date(s: str) -> datetime | None:
    """Парсит WMIC CreationDate: '20260420123456.000000+000'"""
    if not s or len(s) < 14:
        return None
    try:
        return datetime.strptime(s[:14], "%Y%m%d%H%M%S")
    except Exception:
        return None


def get_process_info(pid: int) -> dict | None:
    """Возвращает {pid, name, parent_pid, created_at} или None."""
    rows = _wmic(f"process where ProcessId={pid}")
    if not rows:
        return None
    row = rows[0]
    created = _parse_wmic_date(row.get("CreationDate", ""))
    return {
        "pid":        pid,
        "name":       row.get("Name", "").lower().replace(".exe", ""),
        "parent_pid": int(row.get("ParentProcessId", 0) or 0),
        "created_at": created,
    }


def get_children_pids(parent_pid: int) -> list[int]:
    """Возвращает список PID непосредственных детей parent_pid."""
    rows = _wmic(f"process where ParentProcessId={parent_pid}")
    pids = []
    for row in rows:
        try:
            pids.append(int(row.get("ProcessId", 0) or 0))
        except Exception:
            pass
    return [p for p in pids if p > 0]


def get_process_tree(root_pid: int, _visited: set | None = None) -> list[dict]:
    """Рекурсивно собирает всё дерево процессов от root_pid."""
    if _visited is None:
        _visited = set()
    if root_pid in _visited:
        return []
    _visited.add(root_pid)

    info = get_process_info(root_pid)
    if not info:
        return []

    result = [info]
    for child_pid in get_children_pids(root_pid):
        result.extend(get_process_tree(child_pid, _visited))
    return result


# ═══════════════════════════════════════════════════════════════════════════════
# Resource monitoring
# ═══════════════════════════════════════════════════════════════════════════════

def get_disk_free_gb(path: str = "C:") -> float | None:
    drive = path[0].upper() + ":"
    rows = _wmic(f"logicaldisk where DeviceID='{drive}'")
    if not rows:
        return None
    try:
        free = int(rows[0].get("FreeSpace", 0) or 0)
        return free / (1024 ** 3)
    except Exception:
        return None


def get_gpu_free_mb() -> float | None:
    try:
        r = subprocess.run(
            ["nvidia-smi", "--query-gpu=memory.free", "--format=csv,noheader,nounits"],
            capture_output=True, text=True, timeout=5,
        )
        if r.returncode == 0:
            return float(r.stdout.strip().split("\n")[0])
    except Exception:
        pass
    return None


# ═══════════════════════════════════════════════════════════════════════════════
# File progress tracking
# ═══════════════════════════════════════════════════════════════════════════════

def latest_mtime(dirs: list[Path]) -> float | None:
    """Возвращает самый свежий mtime среди всех файлов в dirs."""
    latest = None
    for d in dirs:
        if not d.exists():
            continue
        for f in d.rglob("*"):
            try:
                mt = f.stat().st_mtime
                if latest is None or mt > latest:
                    latest = mt
            except Exception:
                pass
    return latest


# ═══════════════════════════════════════════════════════════════════════════════
# Pipeline restart
# ═══════════════════════════════════════════════════════════════════════════════

def launch_pipeline(channel_alias: str, session: str | None) -> subprocess.Popen:
    cmd = [_PYTHON, str(BASE_DIR / "pipeline.py"), "--channel", channel_alias]
    if session:
        cmd += ["--session", session]
    env = os.environ.copy()
    env["PYTHONPATH"] = str(BASE_DIR) + os.pathsep + env.get("PYTHONPATH", "")
    flags = subprocess.CREATE_NEW_PROCESS_GROUP if sys.platform == "win32" else 0
    return subprocess.Popen(
        cmd, cwd=str(BASE_DIR), env=env, creationflags=flags,
    )


# ═══════════════════════════════════════════════════════════════════════════════
# Main watchdog loop
# ═══════════════════════════════════════════════════════════════════════════════

class BrosMon:
    def __init__(
        self,
        channel_alias: str,
        session: str | None,
        no_restart: bool,
    ) -> None:
        self.channel_alias  = channel_alias
        self.channel_id     = _CH_ALIAS.get(channel_alias, channel_alias)
        self.session        = session
        self.no_restart     = no_restart

        self.retry_count    = 0
        self.pipeline_proc: subprocess.Popen | None = None
        self.started_at     = time.time()
        self.last_progress  = time.time()
        self.last_stall_warn = 0.0

        # {pid: first_seen_at} — для отслеживания времени жизни процессов
        self._proc_first_seen: dict[int, float] = {}

    # ── Session resolution ──────────────────────────────────────────────────

    def _resolve_session(self) -> str | None:
        if self.session:
            return self.session
        return get_last_session(self.channel_id)

    def _watch_dirs(self) -> list[Path]:
        sess = self._resolve_session()
        dirs = []
        if sess:
            dirs.append(get_session_dir(self.channel_id, sess))
            dirs.append(TEMP_ROOT / self.channel_id / sess / "temp")
        return dirs

    # ── Pipeline PID ────────────────────────────────────────────────────────

    def _pipeline_pid(self) -> int | None:
        if self.pipeline_proc and self.pipeline_proc.poll() is None:
            return self.pipeline_proc.pid
        if PIPELINE_PID_FILE.exists():
            try:
                pid = int(PIPELINE_PID_FILE.read_text(encoding="utf-8").strip())
                if _pid_alive(pid):
                    return pid
            except Exception:
                pass
        return None

    def _pipeline_alive(self) -> bool:
        return self._pipeline_pid() is not None

    # ── Zombie / stuck process detection ────────────────────────────────────

    def _check_processes(self) -> list[int]:
        """Проверяет дерево процессов. Возвращает PIDs которые были убиты."""
        root_pid = self._pipeline_pid()
        if not root_pid:
            return []

        tree = get_process_tree(root_pid)
        now  = time.time()
        killed = []

        for proc in tree:
            pid  = proc["pid"]
            name = proc["name"]
            created = proc.get("created_at")

            if pid == root_pid:
                continue  # главный процесс не трогаем здесь

            # Запоминаем время первого обнаружения
            if pid not in self._proc_first_seen:
                self._proc_first_seen[pid] = now
                continue  # первый раз видим — даём прожить хотя бы один цикл

            alive_min = (now - self._proc_first_seen[pid]) / 60.0
            threshold = PROC_KILL_MIN.get(name, DEFAULT_KILL_MIN)

            if alive_min > threshold:
                log(f"ZOMBIE: {name} PID={pid} живёт {alive_min:.0f} мин > {threshold} мин — убиваем", "KILL")
                if _kill_pid(pid):
                    killed.append(pid)
                    del self._proc_first_seen[pid]
                else:
                    log(f"  Не удалось убить PID={pid}", "WARN")
            elif alive_min > threshold * 0.75:
                log(f"WARN: {name} PID={pid} живёт {alive_min:.0f} мин (лимит {threshold} мин)", "WARN")

        # Чистим уже мёртвые из словаря
        dead = [p for p in list(self._proc_first_seen) if p != root_pid and not _pid_alive(p)]
        for p in dead:
            self._proc_first_seen.pop(p, None)

        return killed

    # ── Stall detection ─────────────────────────────────────────────────────

    def _check_progress(self) -> None:
        dirs = self._watch_dirs()
        mt   = latest_mtime(dirs)
        now  = time.time()

        if mt and mt > self.last_progress:
            self.last_progress = mt
            self.last_stall_warn = 0.0
            return

        stall_min = (now - self.last_progress) / 60.0

        if stall_min > STALL_WARN_MIN and now - self.last_stall_warn > 120:
            log(f"STALL: нет изменений файлов уже {stall_min:.0f} мин", "WARN")
            self.last_stall_warn = now

        if stall_min > STALL_KILL_MIN and self._pipeline_alive():
            log(f"STALL KILL: нет прогресса {stall_min:.0f} мин — рестарт pipeline", "DEAD")
            pid = self._pipeline_pid()
            if pid:
                _kill_pid(pid)
            time.sleep(2)
            self._maybe_restart(reason="stall")

    # ── Resource checks ─────────────────────────────────────────────────────

    def _check_resources(self) -> None:
        # Диск C:
        free_c = get_disk_free_gb("C:")
        if free_c is not None and free_c < DISK_WARN_GB:
            log(f"DISK C: свободно {free_c:.1f} GB (лимит {DISK_WARN_GB} GB)", "DISK")

        # Диск D: (temp)
        free_d = get_disk_free_gb("D:")
        if free_d is not None and free_d < DISK_WARN_GB:
            log(f"DISK D: свободно {free_d:.1f} GB (лимит {DISK_WARN_GB} GB)", "DISK")

        # GPU
        gpu_free = get_gpu_free_mb()
        if gpu_free is not None and gpu_free < GPU_WARN_MB:
            log(f"GPU: свободно {gpu_free:.0f} MB (лимит {GPU_WARN_MB} MB)", "GPU")

    # ── Final video check ────────────────────────────────────────────────────

    def _check_final_video(self) -> bool:
        sess = self._resolve_session()
        if not sess:
            return False
        final = get_final_video(self.channel_id, sess)
        if final.exists() and final.stat().st_size > 10 * 1024 * 1024:
            elapsed = (time.time() - self.started_at) / 60
            log(f"DONE: {final.name} ({final.stat().st_size / 1024 / 1024:.1f} MB) за {elapsed:.0f} мин", "OK")
            return True
        return False

    # ── Restart logic ────────────────────────────────────────────────────────

    def _maybe_restart(self, reason: str = "crash") -> None:
        if self.no_restart:
            log(f"Pipeline упал ({reason}), --no-restart — не перезапускаем", "WARN")
            return

        if self.retry_count >= MAX_RETRIES:
            log(f"Pipeline упал ({reason}), исчерпаны все {MAX_RETRIES} попытки — сдаёмся", "DEAD")
            return

        self.retry_count += 1
        sess = self._resolve_session()
        log(f"RESTART #{self.retry_count}/{MAX_RETRIES}: {reason} → py pipeline.py --channel {self.channel_alias}"
            + (f" --session {sess}" if sess else ""), "RESTART")

        time.sleep(3)
        self.pipeline_proc = launch_pipeline(self.channel_alias, sess)
        self.last_progress = time.time()
        self._proc_first_seen.clear()
        log(f"Pipeline перезапущен PID={self.pipeline_proc.pid}", "OK")

    # ── Status print ─────────────────────────────────────────────────────────

    def _print_status(self, iteration: int) -> None:
        pid       = self._pipeline_pid()
        sess      = self._resolve_session() or "?"
        stall_min = (time.time() - self.last_progress) / 60.0
        uptime    = (time.time() - self.started_at) / 60.0

        print(
            f"\n{'─'*60}\n"
            f"  BrosMon  |  {self.channel_id}  |  {sess}\n"
            f"  Pipeline: {'ALIVE PID=' + str(pid) if pid else 'DEAD'}"
            f"  |  Uptime: {uptime:.0f} мин  |  Retry: {self.retry_count}/{MAX_RETRIES}\n"
            f"  Прогресс: {stall_min:.1f} мин назад  |  Итерация #{iteration}\n"
            f"{'─'*60}",
            flush=True,
        )

    # ── Main loop ────────────────────────────────────────────────────────────

    def run(self) -> None:
        log(f"BrosMon запущен: {self.channel_id}  {'(no-restart)' if self.no_restart else ''}", "OK")

        iteration = 0
        while True:
            iteration += 1

            # 1. Проверить финальное видео — всё готово?
            if self._check_final_video():
                log("Pipeline завершён успешно — BrosMon выходит", "OK")
                break

            # 2. Жив ли pipeline?
            if not self._pipeline_alive():
                # Проверяем ещё раз через 3с (защита от ложного срабатывания при старте)
                time.sleep(3)
                if not self._pipeline_alive():
                    if not self._check_final_video():
                        log("Pipeline не обнаружен (PID dead)", "DEAD")
                        self._maybe_restart(reason="crash")

            # 3. Зомби-процессы
            killed = self._check_processes()
            if killed:
                log(f"Убито зомби-процессов: {len(killed)}", "KILL")
                time.sleep(2)  # дать pipeline время среагировать

            # 4. Stall-детекция
            self._check_progress()

            # 5. Ресурсы (каждые 5 итераций)
            if iteration % 5 == 0:
                self._check_resources()

            # 6. Статус (каждые 3 итерации)
            if iteration % 3 == 0:
                self._print_status(iteration)

            time.sleep(POLL_INTERVAL)


# ═══════════════════════════════════════════════════════════════════════════════
# CLI
# ═══════════════════════════════════════════════════════════════════════════════

def main() -> None:
    parser = argparse.ArgumentParser(
        description="BrosMon — Video Pipeline Watchdog",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Примеры:
  py brosmon.py --channel de                       # наблюдать + авто-рестарт
  py brosmon.py --channel de --session Video_xxx   # конкретная сессия
  py brosmon.py --channel de --no-restart          # только наблюдение
  py brosmon.py --stop                             # остановить brosmon
""",
    )
    parser.add_argument("--channel",    help="Канал: de | fr | es")
    parser.add_argument("--session",    help="Конкретная сессия (иначе последняя)")
    parser.add_argument("--no-restart", action="store_true", help="Не перезапускать упавший pipeline")
    parser.add_argument("--stop",       action="store_true", help="Остановить запущенный brosmon")
    args = parser.parse_args()

    if args.stop:
        if not BROSMON_PID_FILE.exists():
            print("brosmon.pid не найден — BrosMon не запущен.")
            return
        try:
            pid = int(BROSMON_PID_FILE.read_text(encoding="utf-8").strip())
        except Exception:
            print("Не удалось прочитать brosmon.pid")
            return
        if _pid_alive(pid):
            _kill_pid(pid)
            print(f"BrosMon PID={pid} остановлен.")
        else:
            print(f"BrosMon PID={pid} уже не запущен.")
        BROSMON_PID_FILE.unlink(missing_ok=True)
        return

    if not args.channel:
        parser.error("--channel обязателен")

    # Пишем PID
    BROSMON_PID_FILE.write_text(str(os.getpid()), encoding="utf-8")

    # Лог-файл рядом с pipeline.pid
    global _LOG_FILE
    _LOG_FILE = BASE_DIR / "brosmon.log"

    import atexit, signal
    atexit.register(lambda: BROSMON_PID_FILE.unlink(missing_ok=True))

    def _sig(s, f):
        BROSMON_PID_FILE.unlink(missing_ok=True)
        sys.exit(0)
    signal.signal(signal.SIGTERM, _sig)
    try:
        signal.signal(signal.SIGBREAK, _sig)
    except (AttributeError, OSError):
        pass

    mon = BrosMon(
        channel_alias=args.channel,
        session=args.session,
        no_restart=args.no_restart,
    )
    try:
        mon.run()
    finally:
        BROSMON_PID_FILE.unlink(missing_ok=True)


if __name__ == "__main__":
    main()
