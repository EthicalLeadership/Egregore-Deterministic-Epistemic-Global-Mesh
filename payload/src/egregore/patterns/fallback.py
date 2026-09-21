#!/usr/bin/env python3
# epistemic marker: provenance / auditability
"""Egregore Fallback Pattern"""

import contextlib
from collections.abc import Callable
from functools import wraps
from typing import Any


def fallback_value(default: Any, exceptions: tuple | None = None):
    """Decorator that returns default value on failure."""

    def decorator(func: Callable) -> Callable:
        @wraps(func)
        def wrapper(*args, **kwargs):
            catch = exceptions if exceptions is not None else Exception
            try:
                return func(*args, **kwargs)
            except catch:
                return default

        return wrapper

    return decorator


class Fallback:
    def __init__(self, fallback_fn: Callable | None = None, fallback_value: Any = None):
        self.fallback_fn = fallback_fn
        self.fallback_value = fallback_value
        self._primary_successes = 0
        self._primary_failures = 0
        self._fallback_uses = 0

    def __call__(self, func: Callable) -> Callable:
        @wraps(func)
        def wrapper(*args, **kwargs):
            try:
                result = func(*args, **kwargs)
                self._primary_successes += 1
                return result
            except Exception:
                self._primary_failures += 1
                if self.fallback_fn:
                    with contextlib.suppress(Exception):
                        result = self.fallback_fn(*args, **kwargs)
                        self._fallback_uses += 1
                        return result
                if self.fallback_value is not None:
                    self._fallback_uses += 1
                    return self.fallback_value
                raise

        return wrapper

    def get_metrics(self) -> dict:
        total = self._primary_successes + self._primary_failures
        return {
            "successes": self._primary_successes,
            "failures": self._primary_failures,
            "fallback_uses": self._fallback_uses,
            "fallback_rate": self._fallback_uses / total if total > 0 else 0,
        }
