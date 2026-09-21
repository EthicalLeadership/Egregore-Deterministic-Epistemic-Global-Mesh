from __future__ import annotations

import threading
from collections import deque
from dataclasses import dataclass
from typing import Generic, TypeVar

T = TypeVar("T")


@dataclass(frozen=True)
class TransitStats:
    queued: int
    deque_capacity: int


class LocalTransitLayer(Generic[T]):
    """
    Local Transit Layer (TL): bounded, thread-safe FIFO buffer.

    Constraints:
    - Thread-safe queue only (deque + Condition)
    - No Redis, no network calls
    - Determinism: if there is a single producer, FIFO dequeue order is deterministic.

    Interface:
    - put(item): enqueue (blocks until space or closed)
    - get(timeout): dequeue (blocks until item available or closed/timeout)
    - close(): signal no more items
    """

    def __init__(self, *, capacity: int) -> None:
        if capacity <= 0:
            raise ValueError("capacity must be > 0")
        self._capacity = int(capacity)
        self._dq: deque[T] = deque()
        self._cv = threading.Condition()
        self._closed = False

    @property
    def stats(self) -> TransitStats:
        with self._cv:
            return TransitStats(queued=len(self._dq), deque_capacity=self._capacity)

    def close(self) -> None:
        with self._cv:
            self._closed = True
            self._cv.notify_all()

    def put(
        self, item: T, *, block: bool = True, timeout_sec: float | None = None
    ) -> None:
        """
        Enqueue item.
        - If closed: raises RuntimeError
        - If queue is full and block=False: raises TimeoutError
        """
        import time

        deadline = (
            None if timeout_sec is None else (time.monotonic() + float(timeout_sec))
        )

        with self._cv:
            while True:
                if self._closed:
                    raise RuntimeError("LocalTransitLayer is closed")
                if len(self._dq) < self._capacity:
                    self._dq.append(item)
                    self._cv.notify()
                    return
                if not block:
                    raise TimeoutError("LocalTransitLayer is full")
                if deadline is not None:
                    remaining = deadline - time.monotonic()
                    if remaining <= 0:
                        raise TimeoutError("Timed out waiting for free capacity")
                    self._cv.wait(timeout=remaining)
                else:
                    self._cv.wait()

    def get(self, *, timeout_sec: float | None = None) -> T | None:
        """
        Dequeue item.
        - Returns None if closed and empty.
        - If timeout_sec is provided and expires: returns None.
        """
        import time

        deadline = (
            None if timeout_sec is None else (time.monotonic() + float(timeout_sec))
        )

        with self._cv:
            while True:
                if self._dq:
                    item = self._dq.popleft()
                    self._cv.notify()
                    return item
                if self._closed:
                    return None
                if deadline is not None:
                    remaining = deadline - time.monotonic()
                    if remaining <= 0:
                        return None
                    self._cv.wait(timeout=remaining)
                else:
                    self._cv.wait()
