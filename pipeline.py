"""
Video Pipeline — main entry point
"""

from agents.transcription_agent import run as transcribe


def main():
    print("=== Video Pipeline ===\n")
    print("--- Шаг 1: Транскрипция ---")
    transcribe()


if __name__ == "__main__":
    main()
