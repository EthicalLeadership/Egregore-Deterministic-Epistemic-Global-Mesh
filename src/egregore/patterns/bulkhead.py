#!/usr/bin/env python3
"""Egregore Bulkhead Pattern"""

import threading
from collections.abc import Callable
from functools import wraps


class Bulkhead:
    def __init__(self, name: str, max_concurrent: int = 10, max_queue: int = 50):
        self.name = name
        self.max_concurrent = max_concurrent
        self.max_queue = max_queue
        self._semaphore = threading.Semaphore(max_concurrent)
        self._queue_size = 0
        self._lock = threading.Lock()
        self._active_count = 0
        self._total_rejected = 0

    def _acquire(self, timeout: float = 30.0) -> bool:
        with self._lock:
            if self._queue_size >= self.max_queue:
                self._total_rejected += 1
                return False
            self._queue_size += 1
        acquired = self._semaphore.acquire(timeout=timeout)
        with self._lock:
            self._queue_size -= 1
        return acquired

    def _release(self):
        self._semaphore.release()
        with self._lock:
            self._active_count -= 1

    def __call__(self, func: Callable) -> Callable:
        @wraps(func)
        def wrapper(*args, **kwargs):
            if not self._acquire():
                raise Exception(f"Bulkhead '{self.name}' capacity exceeded")
            with self._lock:
                self._active_count += 1
            try:
                return func(*args, **kwargs)
            finally:
                self._release()

        return wrapper

    def get_state(self) -> dict:
        with self._lock:
            return {
                "name": self.name,
                "active": self._active_count,
                "rejected": self._total_rejected,
            }


BULKHEADS: dict = {}


def get_bulkhead(name: str) -> "Bulkhead":
    if name not in BULKHEADS:
        BULKHEADS[name] = Bulkhead(name)
    return BULKHEADS[name]
