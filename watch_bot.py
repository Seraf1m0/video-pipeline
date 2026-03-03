"""
watch_bot.py — Watchdog для telegram_bot.py
--------------------------------------------
Перезапускает бота автоматически при любом краше.
Запуск: py watch_bot.py

Логи: bot/logs/bot.log (те же, что и у бота)
"""

import subprocess
import sys
import time
from datetime import datetime
from pathlib import Path

# ── Настройки ─────────────────────────────────────────────────────────────────

BOT_SCRIPT    = Path(__file__).parent / "bot" / "telegram_bot.py"
LOG_FILE      = Path(__file__).parent / "bot" / "logs" / "bot.log"
RESTART_DELAY = 5   # секунд до перезапуска после краша
MAX_RESTARTS  = 0   # 0 = бесконечно


# ── Логирование ───────────────────────────────────────────────────────────────

def log(msg: str) -> None:
    line = f"[WATCHDOG {datetime.now():%Y-%m-%d %H:%M:%S}] {msg}"
    print(line, flush=True)
    try:
        LOG_FILE.parent.mkdir(parents=True, exist_ok=True)
        with LOG_FILE.open("a", encoding="utf-8") as f:
            f.write(line + "\n")
    except Exception:
        pass


# ── Основной цикл ─────────────────────────────────────────────────────────────

def main() -> None:
    log("=" * 60)
    log("Watchdog запущен")
    log(f"Бот: {BOT_SCRIPT}")
    log("=" * 60)

    attempt = 0
    while True:
        attempt += 1
        log(f"Запуск бота (попытка #{attempt})")

        try:
            LOG_FILE.parent.mkdir(parents=True, exist_ok=True)
            with LOG_FILE.open("a", encoding="utf-8") as log_fh:
                proc = subprocess.run(
                    [sys.executable, str(BOT_SCRIPT)],
                    stderr=log_fh,   # stderr (необработанные исключения) → в лог
                )
            code = proc.returncode
        except KeyboardInterrupt:
            log("Watchdog остановлен пользователем (Ctrl+C)")
            sys.exit(0)
        except Exception as e:
            code = -1
            log(f"Ошибка запуска процесса: {e}")

        if code == 0:
            log(f"Бот завершился штатно (код 0) — перезапуск через {RESTART_DELAY}с")
        else:
            log(f"БОТ УПАЛ (код {code}) — перезапуск через {RESTART_DELAY}с")

        if MAX_RESTARTS and attempt >= MAX_RESTARTS:
            log(f"Достигнут лимит перезапусков ({MAX_RESTARTS}) — останавливаю watchdog")
            sys.exit(1)

        try:
            time.sleep(RESTART_DELAY)
        except KeyboardInterrupt:
            log("Watchdog остановлен пользователем (Ctrl+C)")
            sys.exit(0)


if __name__ == "__main__":
    main()
