"""In-memory inference metrics tracker for the Core API.

Tracks request counts, latency, token usage, and error rates for
/v1/chat/completions over a rolling time window.
"""

from __future__ import annotations

import time
from collections import deque
from dataclasses import dataclass
from typing import Any


@dataclass
class _RequestSample:
    timestamp: float
    latency_ms: float
    prompt_tokens: int
    completion_tokens: int
    total_tokens: int
    error: bool


class InferenceMetrics:
    """Thread-safe-ish (GIL-protected) rolling metrics store."""

    def __init__(self, window_seconds: float = 300.0) -> None:
        self._window = window_seconds
        self._samples: deque[_RequestSample] = deque()
        self._total_requests = 0
        self._total_errors = 0
        self._total_prompt_tokens = 0
        self._total_completion_tokens = 0

    def record(
        self,
        latency_ms: float,
        prompt_tokens: int = 0,
        completion_tokens: int = 0,
        error: bool = False,
    ) -> None:
        now = time.time()
        sample = _RequestSample(
            timestamp=now,
            latency_ms=latency_ms,
            prompt_tokens=prompt_tokens,
            completion_tokens=completion_tokens,
            total_tokens=prompt_tokens + completion_tokens,
            error=error,
        )
        self._samples.append(sample)
        self._total_requests += 1
        if error:
            self._total_errors += 1
        self._total_prompt_tokens += prompt_tokens
        self._total_completion_tokens += completion_tokens
        self._expire_old(now)

    def record_error(self, latency_ms: float = 0.0) -> None:
        self.record(latency_ms=latency_ms, error=True)

    def _expire_old(self, now: float) -> None:
        cutoff = now - self._window
        while self._samples and self._samples[0].timestamp < cutoff:
            self._samples.popleft()

    def snapshot(self) -> dict[str, Any]:
        now = time.time()
        self._expire_old(now)

        if not self._samples:
            return {
                "requests_total": self._total_requests,
                "errors_total": self._total_errors,
                "requests_per_min": 0.0,
                "tokens_per_sec": 0.0,
                "avg_latency_ms": 0.0,
                "p50_latency_ms": 0.0,
                "p95_latency_ms": 0.0,
                "error_rate": 0.0,
                "prompt_tokens_total": self._total_prompt_tokens,
                "completion_tokens_total": self._total_completion_tokens,
            }

        window_start = self._samples[0].timestamp
        window_duration = max(now - window_start, 1.0)

        latencies = sorted(s.latency_ms for s in self._samples if not s.error)
        requests_in_window = len(self._samples)
        errors_in_window = sum(1 for s in self._samples if s.error)
        total_tokens_in_window = sum(s.total_tokens for s in self._samples)

        def _percentile(values: list[float], p: float) -> float:
            if not values:
                return 0.0
            k = (len(values) - 1) * p
            f = int(k)
            c = f + 1 if f + 1 < len(values) else f
            if f == c:
                return values[f]
            return values[f] * (c - k) + values[c] * (k - f)

        return {
            "requests_total": self._total_requests,
            "errors_total": self._total_errors,
            "requests_per_min": round(requests_in_window / (window_duration / 60), 2),
            "tokens_per_sec": round(total_tokens_in_window / window_duration, 2),
            "avg_latency_ms": round(sum(latencies) / len(latencies), 2) if latencies else 0.0,
            "p50_latency_ms": round(_percentile(latencies, 0.5), 2) if latencies else 0.0,
            "p95_latency_ms": round(_percentile(latencies, 0.95), 2) if latencies else 0.0,
            "error_rate": round(errors_in_window / requests_in_window, 4),
            "prompt_tokens_total": self._total_prompt_tokens,
            "completion_tokens_total": self._total_completion_tokens,
        }


# Global singleton metrics instance
INFERENCE_METRICS = InferenceMetrics()
