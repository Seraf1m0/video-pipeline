"""
Утилиты для отображения прогресса
Использует tqdm для красивых прогресс-баров с временем
"""

from tqdm import tqdm
from typing import Iterator, Iterable, TypeVar, Callable, Any
from datetime import datetime

T = TypeVar('T')


class ProgressBar:
    """Обёртка над tqdm с кастомизацией"""
    
    @staticmethod
    def iterate(iterable: Iterable[T], desc: str = "", unit: str = "it") -> Iterator[T]:
        """
        Простой прогресс-бар для итерации
        
        Пример:
            for item in ProgressBar.iterate(items, desc="Обработка"):
                process(item)
        """
        return tqdm(iterable, desc=desc, unit=unit, 
                   bar_format='{desc}: {percentage:.0f}%|{bar}| {n_fmt}/{total_fmt} [{elapsed}<{remaining}]')
    
    @staticmethod
    def manual(total: int, desc: str = "", unit: str = "it"):
        """
        Ручное управление прогрессом
        
        Пример:
            pbar = ProgressBar.manual(100, desc="Загрузка")
            for i in range(10):
                process_chunk()
                pbar.update(10)
            pbar.close()
        """
        return tqdm(total=total, desc=desc, unit=unit,
                   bar_format='{desc}: {percentage:.0f}%|{bar}| {n_fmt}/{total_fmt} [{elapsed}<{remaining}]')
    
    @staticmethod
    def task(desc: str):
        """Простой вывод времени выполнения для задачи"""
        start = datetime.now()
        print(f"\n▶ {desc}...")
        return start
    
    @staticmethod
    def done(start: datetime, desc: str = ""):
        """Вывод времени выполнения"""
        elapsed = (datetime.now() - start).total_seconds()
        msg = f"✓ {desc}" if desc else "✓ Готово"
        print(f"{msg} ({elapsed:.1f}s)\n")
        return elapsed


def progress_loop(items: Iterable[T], desc: str = "") -> Iterator[T]:
    """Удобная функция для использования в циклах"""
    return tqdm(items, desc=desc, unit="it",
               bar_format='{desc}: {percentage:.0f}%|{bar}| {n_fmt}/{total_fmt} [{elapsed}<{remaining}]')


def progress_enumerate(items: Iterable[T], desc: str = "") -> Iterator[tuple[int, T]]:
    """Прогресс-бар с нумерацией"""
    for i, item in enumerate(tqdm(items, desc=desc, unit="it")):
        yield i, item
