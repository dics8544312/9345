"""
Rate Limiter для контроля нагрузки на AI API.

ГЛАВНОЕ: ограничивает ТОКЕНЫ в минуту (TPM) — именно из-за превышения TPM
OpenAI возвращает ошибку 429. Лимитер ПРОАКТИВНО придерживает запрос, пока в
минутном бюджете не освободится место под него, поэтому до реальной ошибки 429
дело практически не доходит. Дополнительно ограничивает число одновременных
запросов.

Работает на уровне одного процесса бота (скользящее окно 60 секунд).
"""

import asyncio
import time
from typing import Optional
from collections import deque


class RateLimiter:
    """
    Лимитер токенов в минуту + одновременных запросов.
    """

    def __init__(self, max_tokens_per_minute: int = 27000, max_concurrent: int = 10):
        """
        Args:
            max_tokens_per_minute: Рабочий бюджет токенов в минуту (уже с запасом
                относительно реального лимита OpenAI).
            max_concurrent: Максимум одновременных запросов.
        """
        self.max_tokens_per_minute = max(1000, int(max_tokens_per_minute))
        self.max_concurrent = max_concurrent

        self._semaphore = asyncio.Semaphore(max_concurrent)
        self._window: deque = deque()  # элементы вида [timestamp, tokens]
        self._lock = asyncio.Lock()

    def _purge(self, now: float):
        """Убираем из окна всё старше 60 секунд."""
        while self._window and self._window[0][0] <= now - 60.0:
            self._window.popleft()

    def _current_tokens(self) -> int:
        return sum(entry[1] for entry in self._window)

    async def acquire(self, estimated_tokens: int = 0):
        """
        Берём слот: сначала слот одновременности, затем ждём, пока в минутном
        бюджете хватит места под estimated_tokens. Возвращает управление только
        когда запрос можно безопасно отправить, не упираясь в TPM.

        Возвращает «резерв» (list [timestamp, tokens]) — его нужно передать в
        settle() после успешного ответа (фиксируем фактический расход) или в
        cancel() при ошибке (снимаем резерв, токены не потрачены).
        """
        await self._semaphore.acquire()
        estimated_tokens = max(0, int(estimated_tokens))
        try:
            while True:
                async with self._lock:
                    now = time.monotonic()
                    self._purge(now)
                    current = self._current_tokens()
                    # Разрешаем, если запрос влезает в бюджет, ИЛИ окно пустое
                    # (один запрос сам по себе больше бюджета — ждать бесполезно,
                    # отдаём как есть; на этот редкий случай есть сетевой ретрай).
                    if not self._window or current + estimated_tokens <= self.max_tokens_per_minute:
                        reservation = [now, estimated_tokens]
                        self._window.append(reservation)
                        return reservation
                    # Нужно подождать, пока самая старая запись «состарится».
                    wait = 60.0 - (now - self._window[0][0])
                await asyncio.sleep(min(max(wait, 0.1), 3.0))
        except BaseException:
            # Если во время ожидания нас отменили/прервали — не удерживаем семафор.
            self._semaphore.release()
            raise

    def settle(self, reservation, actual_tokens: int):
        """После успешного ответа заменяем оценку на фактический расход (usage.total_tokens)."""
        if not reservation:
            return
        try:
            reservation[1] = max(0, int(actual_tokens))
        except Exception:
            pass

    def cancel(self, reservation):
        """Запрос не состоялся (ошибка/429/таймаут) — снимаем резерв: токены не потрачены."""
        if not reservation:
            return
        try:
            reservation[1] = 0
        except Exception:
            pass

    def release(self):
        """Освобождаем слот одновременности (учтённые токены остаются в окне ещё минуту)."""
        self._semaphore.release()


# Глобальный rate limiter для AI запросов
_ai_rate_limiter: Optional[RateLimiter] = None


def get_ai_rate_limiter() -> RateLimiter:
    """Получить AI rate limiter (настраивается из OPENAI_TPM_LIMIT)."""
    global _ai_rate_limiter
    if not _ai_rate_limiter:
        try:
            from config import settings
            tpm = int(getattr(settings, "OPENAI_TPM_LIMIT", 30000) or 30000)
        except Exception:
            tpm = 30000
        # Работаем с запасом 12% под неточность оценки токенов и прочие расходы,
        # чтобы гарантированно НЕ упираться в реальный лимит OpenAI.
        budget = max(2000, int(tpm * 0.88))
        _ai_rate_limiter = RateLimiter(max_tokens_per_minute=budget, max_concurrent=10)
    return _ai_rate_limiter
