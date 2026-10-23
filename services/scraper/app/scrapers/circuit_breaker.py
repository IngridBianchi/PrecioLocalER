"""Circuit breaker simple (CLOSED -> OPEN -> HALF_OPEN) para hosts scrapeados."""
from __future__ import annotations

import threading
import time
from enum import Enum

from app.errors import CircuitOpenError


class _State(Enum):
    CLOSED = "closed"
    OPEN = "open"
    HALF_OPEN = "half_open"


class CircuitBreaker:
    def __init__(self, *, failure_threshold: int = 3, reset_timeout_seconds: float = 60.0) -> None:
        if failure_threshold < 1:
            raise ValueError("failure_threshold debe ser >= 1")
        self._threshold = failure_threshold
        self._reset_timeout = reset_timeout_seconds
        self._state = _State.CLOSED
        self._failures = 0
        self._opened_at: float | None = None
        self._lock = threading.Lock()

    def before(self) -> None:
        with self._lock:
            if self._state is _State.OPEN and self._opened_at is not None:
                if time.monotonic() - self._opened_at >= self._reset_timeout:
                    self._state = _State.HALF_OPEN
                else:
                    raise CircuitOpenError("circuit abierto; reintentar más tarde")
            if self._state is _State.HALF_OPEN:
                # Un solo intento de prueba; si falla, vuelve a abrirse.
                self._failures = 0

    def on_success(self) -> None:
        with self._lock:
            self._state = _State.CLOSED
            self._failures = 0
            self._opened_at = None

    def on_failure(self) -> None:
        with self._lock:
            if self._state is _State.HALF_OPEN:
                self._state = _State.OPEN
                self._opened_at = time.monotonic()
                return
            self._failures += 1
            if self._failures >= self._threshold:
                self._state = _State.OPEN
                self._opened_at = time.monotonic()

    @property
    def state(self) -> str:
        with self._lock:
            return self._state.value