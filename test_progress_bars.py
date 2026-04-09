#!/usr/bin/env python3
"""
Пример использования прогресс-баров
Демонстрирует разные способы вывода прогресса
"""

import time
from agents.utils.progress import ProgressBar, progress_loop, progress_enumerate


def demo_iterate():
    """Демо простого цикла с прогрессом"""
    print("=" * 50)
    print("📊 Пример 1: Iteration с прогрессом")
    print("=" * 50)
    
    items = range(20)
    for item in ProgressBar.iterate(items, desc="Обработка данных"):
        time.sleep(0.15)  # имитируем работу


def demo_manual():
    """Демо ручного управления прогрессом"""
    print("\n" + "=" * 50)
    print("📊 Пример 2: Ручное управление прогрессом")
    print("=" * 50)
    
    pbar = ProgressBar.manual(total=100, desc="Загрузка файла")
    for i in range(10):
        time.sleep(0.2)
        pbar.update(10)
    pbar.close()


def demo_task_timing():
    """Демо простого трекинга времени"""
    print("\n" + "=" * 50)
    print("📊 Пример 3: Трекинг времени выполнения")
    print("=" * 50)
    
    # Задача 1
    start = ProgressBar.task("Операция 1: Чтение данных")
    time.sleep(1)
    ProgressBar.done(start, "Данные прочитаны")
    
    # Задача 2
    start = ProgressBar.task("Операция 2: Обработка")
    time.sleep(1.5)
    ProgressBar.done(start, "Обработка завершена")
    
    # Задача 3
    start = ProgressBar.task("Операция 3: Сохранение")
    time.sleep(0.8)
    ProgressBar.done(start, "Результаты сохранены")


def demo_with_enumerate():
    """Демо прогресса с нумерацией"""
    print("\n" + "=" * 50)
    print("📊 Пример 4: Прогресс + нумерация")
    print("=" * 50)
    
    items = ["файл1.mp3", "файл2.wav", "файл3.mp3", "файл4.wav", "файл5.mp3"]
    for i, item in progress_enumerate(items, desc="Обработка файлов"):
        print(f"  {i+1}. {item}")
        time.sleep(0.3)


def demo_real_pipeline():
    """Демо имитирующий реальный пайплайн"""
    print("\n" + "=" * 50)
    print("📊 Пример 5: Имитация пайплайна видео")
    print("=" * 50)
    
    videos = [f"video_{i:02d}.mp4" for i in range(1, 6)]
    
    for video in ProgressBar.iterate(videos, desc="Обработка видеоролей"):
        # Шаг 1: Загрузка
        start = ProgressBar.task(f"  ⊙ {video}: загрузка")
        time.sleep(0.5)
        ProgressBar.done(start)
        
        # Шаг 2: Обработка
        start = ProgressBar.task(f"  ⊙ {video}: обработка")
        time.sleep(0.8)
        ProgressBar.done(start)
        
        # Шаг 3: Экспорт
        start = ProgressBar.task(f"  ⊙ {video}: экспорт")
        time.sleep(0.3)
        ProgressBar.done(start)


if __name__ == "__main__":
    demo_iterate()
    demo_manual()
    demo_task_timing()
    demo_with_enumerate()
    demo_real_pipeline()
    
    print("\n" + "=" * 50)
    print("✓ Все примеры завершены!")
    print("=" * 50)
