#!/usr/bin/env python3
"""Egregore Circuit Breaker Pattern"""

import threading
import time
from collections.abc import Callable
from enum import Enum, auto
from functools import wraps


class CircuitState(Enum):
    CLOSED = auto()
    OPEN = auto()
    HALF_OPEN = auto()


class CircuitBreaker:
    def __init__(
        self, name: str, failure_threshold: int = 5, recovery_timeout: float = 30.0
    ):
        self.name = name
        self.failure_threshold = failure_threshold
        self.recovery_timeout = recovery_timeout
        self.state = CircuitState.CLOSED
        self.failure_count = 0
        self.last_failure_time = 0.0
        self._lock = threading.RLock()

    def _can_attempt(self) -> bool:
        with self._lock:
            if self.state == CircuitState.CLOSED:
                return True
            if self.state == CircuitState.OPEN:
                if time.time() - self.last_failure_time >= self.recovery_timeout:
                    self.state = CircuitState.HALF_OPEN
                    return True
                return False
            return True

    def _record_success(self):
        with self._lock:
            if self.state == CircuitState.HALF_OPEN:
                self.state = CircuitState.CLOSED
                self.failure_count = 0
            else:
                self.failure_count = max(0, self.failure_count - 1)

    def _record_failure(self):
        with self._lock:
            self.failure_count += 1
            if self.failure_count >= self.failure_threshold:
                self.state = CircuitState.OPEN
                self.last_failure_time = time.time()

    def __call__(self, func: Callable) -> Callable:
        @wraps(func)
        def wrapper(*args, **kwargs):
            if not self._can_attempt():
                raise Exception(f"Circuit breaker '{self.name}' is OPEN")
            try:
                result = func(*args, **kwargs)
                self._record_success()
                return result
            except Exception:
                self._record_failure()
                raise

        return wrapper

    def get_state(self) -> dict:
        with self._lock:
            return {
                "name": self.name,
                "state": self.state.name,
                "failure_count": self.failure_count,
            }


CIRCUIT_BREAKERS: dict = {}


def get_circuit_breaker(name: str) -> "CircuitBreaker":
    if name not in CIRCUIT_BREAKERS:
        CIRCUIT_BREAKERS[name] = CircuitBreaker(name)
    return CIRCUIT_BREAKERS[name]
