"""
Video Pipeline — main entry point
"""

from agents.transcriber.transcriber import run as transcribe
from agents.utils.progress import ProgressBar


def main():
    print("=== Video Pipeline ===\n")
    
    start = ProgressBar.task("Запуск транскрипции")
    print("--- Шаг 1: Транскрипция ---")
    transcribe()
    ProgressBar.done(start, "Транскрипция завершена")


if __name__ == "__main__":
    main()
