from __future__ import annotations

import time
from collections import defaultdict, deque
from threading import Lock

_WINDOW_SECONDS = 3600

_history: dict[int, deque[float]] = defaultdict(deque)
_lock = Lock()


def check_and_record(telegram_id: int, max_per_hour: int) -> tuple[bool, int]:
    """Реєструє запуск сценарію. Повертає (allowed, retry_after_seconds).

    Якщо за останню годину користувач уже зробив max_per_hour запусків —
    allowed=False і retry_after_seconds показує, скільки чекати до найстарішої спроби."""
    now = time.monotonic()
    cutoff = now - _WINDOW_SECONDS

    with _lock:
        history = _history[telegram_id]
        while history and history[0] < cutoff:
            history.popleft()

        if len(history) >= max_per_hour:
            retry_after = int(history[0] + _WINDOW_SECONDS - now) + 1
            return False, max(retry_after, 1)

        history.append(now)
        return True, 0


def reset(telegram_id: int) -> None:
    """Тестовий хук — очистити історію конкретного користувача."""
    with _lock:
        _history.pop(telegram_id, None)


def reset_all() -> None:
    """Тестовий хук — очистити всю історію."""
    with _lock:
        _history.clear()
