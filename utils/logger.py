"""
Настройка логирования
"""

import logging
import sys
from config import settings


def setup_logger(name: str = "math_tutor_bot") -> logging.Logger:
    """
    Настройка логгера для приложения
    
    Args:
        name: Имя логгера
        
    Returns:
        Настроенный логгер
    """
    logger = logging.getLogger(name)

    # Устанавливаем уровень логирования
    level = logging.DEBUG if settings.DEBUG else logging.INFO
    logger.setLevel(level)

    # ВАЖНО: setup_logger вызывается многократно. Если обработчик уже есть —
    # не добавляем новый, иначе каждая строка лога дублируется N раз.
    if logger.handlers:
        return logger

    # Логи не всплывают в root-логгер (иначе тоже возможны дубли)
    logger.propagate = False

    # Создаем обработчик для вывода в консоль
    console_handler = logging.StreamHandler(sys.stdout)
    console_handler.setLevel(level)
    
    # Создаем форматтер
    formatter = logging.Formatter(
        '%(asctime)s - %(name)s - %(levelname)s - %(message)s',
        datefmt='%Y-%m-%d %H:%M:%S'
    )
    console_handler.setFormatter(formatter)
    
    # Добавляем обработчик к логгеру
    logger.addHandler(console_handler)
    
    return logger
