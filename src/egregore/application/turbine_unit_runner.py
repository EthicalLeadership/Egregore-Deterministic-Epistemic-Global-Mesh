from __future__ import annotations

import threading
import time
from dataclasses import dataclass

from egregore.domain.hardware_work_unit import PrecisionGear, WorkPayload
from egregore.interface.hardware_ports import ITransitLayer


@dataclass(frozen=True)
class TurbineUnitResult:
    tu_id: str
    processed_jobs: int
    mean_gflops: float


def _precision_to_mode(precision: PrecisionGear) -> str:
    if precision == PrecisionGear.FP16:
        return "fp16"
    if precision == PrecisionGear.FP32:
        return "fp32"
    if precision == PrecisionGear.INT8:
        return "int8"
    return "fp16"


def _q_round(x: float, *, decimals: int) -> float:
    # Deterministic rounding to emulate reduced precision.
    return round(float(x), decimals)


def _quantize_value(mode: str, x: float) -> float:
    if mode == "fp32":
        return float(x)
    if mode == "fp16":
        # Rough FP16-ish emulation: truncate/round
        return _q_round(x, decimals=3)
    if mode == "int8":
        # Quantize to int8 range then convert back to float for matmul.
        q = int(round(x * 10.0))
        if q > 127:
            q = 127
        if q < -127:
            q = -127
        return float(q) / 10.0
    return _q_round(x, decimals=3)


def _deterministic_matrix(n: int, *, seed: int, mode: str) -> list[list[float]]:
    # Deterministic synthetic matrix. Uses only math operations.
    import math

    a: list[list[float]] = [[0.0 for _ in range(n)] for __ in range(n)]
    s0 = seed * 0.001
    for i in range(n):
        row_i = a[i]
        fi = float(i)
        for j in range(n):
            fj = float(j)
            x = fi * 0.01 + fj * 0.02 + s0
            v = math.sin(x) + math.cos(x * 0.7)
            row_i[j] = _quantize_value(mode, v)
    return a


def _transpose(b: list[list[float]]) -> list[list[float]]:
    n = len(b)
    bt: list[list[float]] = [[0.0 for _ in range(n)] for __ in range(n)]
    for i in range(n):
        for j in range(n):
            bt[j][i] = b[i][j]
    return bt


def _matmul(a: list[list[float]], bt: list[list[float]]) -> list[list[float]]:
    """
    Multiply a (n x n) by b (n x n), but b is provided transposed as bt (n x n).
    Returns c (n x n).
    """
    n = len(a)
    c: list[list[float]] = [[0.0 for _ in range(n)] for __ in range(n)]
    for i in range(n):
        ai = a[i]
        ci = c[i]
        for j in range(n):
            bj = bt[j]
            s = 0.0
            for k in range(n):
                s += ai[k] * bj[k]
            ci[j] = s
    return c


def _maybe_numpy_matmul(
    *,
    n: int,
    batch_window: int,
    seed: int,
    precision: PrecisionGear,
) -> float | None:
    """
    NumPy fast-path (CPU only).
    Returns GFLOPS if NumPy is available; otherwise None.

    This function is intentionally isolated and uses lazy import so environments
    without NumPy still work.
    """
    try:
        import numpy as np  # type: ignore  # optional dependency: NumPy may be absent in CI
    except Exception:
        return None

    mode = _precision_to_mode(precision)

    # Vectorized deterministic construction.
    i = np.arange(n, dtype=np.float32).reshape(n, 1)
    j = np.arange(n, dtype=np.float32).reshape(1, n)
    s0 = float(seed) * 0.001
    x = (i * 0.01 + j * 0.02 + s0).astype(np.float32)

    v = np.sin(x) + np.cos(x * 0.7)
    if mode == "fp32":
        a = v.astype(np.float32, copy=False)
        b = (np.sin(x + 0.001 * n) + np.cos((x + 0.001 * n) * 0.7)).astype(
            np.float32, copy=False
        )
    elif mode == "fp16":
        a = v.astype(np.float16, copy=False)
        x2 = x + 0.001 * n
        v2 = np.sin(x2) + np.cos(x2 * 0.7)
        b = v2.astype(np.float16, copy=False)
    elif mode == "int8":
        # Emulate int8 quantization and matmul using float.
        q = np.round(v * 10.0).clip(-127, 127).astype(np.float32) / 10.0
        x2 = x + 0.001 * n
        v2 = np.sin(x2) + np.cos(x2 * 0.7)
        q2 = np.round(v2 * 10.0).clip(-127, 127).astype(np.float32) / 10.0
        a = q
        b = q2
    else:
        a = v.astype(np.float16, copy=False)
        x2 = x + 0.001 * n
        b = (np.sin(x2) + np.cos(x2 * 0.7)).astype(np.float16, copy=False)

    # Warm-up
    _ = a @ b

    start = time.monotonic()
    c = None
    for _ in range(batch_window):
        c = a @ b
    end = time.monotonic()

    # Touch output deterministically.
    if c is not None:
        _ = float(np.asarray(c[0, 0]).item() + np.asarray(c[-1, -1]).item())

    elapsed_s = max(1e-9, float(end - start))
    flops_per_matmul = 2.0 * (float(n) ** 3)
    total_flops = flops_per_matmul * float(batch_window)
    gflops = (total_flops / elapsed_s) / 1e9
    return float(gflops)


class TurbineUnitRunner:
    """
    Worker thread runner:
    - pulls WorkPayload from transit layer
    - runs batched NxN matrix multiply
    - measures sustained GFLOPS over wall-clock
    """

    def __init__(
        self,
        *,
        tl: ITransitLayer[WorkPayload],
        tu_id: str,
        report_interval_sec: float = 2.0,
    ) -> None:
        self._tl = tl
        self._tu_id = tu_id
        self._report_interval_sec = float(report_interval_sec)

        self._stop = threading.Event()
        self._thread = threading.Thread(
            target=self._run, name=f"TU-{tu_id}", daemon=True
        )

        self._processed_jobs = 0
        self._gflops_sum = 0.0
        self._gflops_samples = 0

        self._last_report_t = time.monotonic()

    @property
    def tu_id(self) -> str:
        return self._tu_id

    def start(self) -> None:
        self._thread.start()

    def join(self, *, timeout_sec: float | None = None) -> None:
        self._thread.join(timeout=timeout_sec)

    def stop(self) -> None:
        self._stop.set()

    def result(self) -> TurbineUnitResult:
        mean = self._gflops_sum / self._gflops_samples if self._gflops_samples else 0.0
        return TurbineUnitResult(
            tu_id=self._tu_id,
            processed_jobs=self._processed_jobs,
            mean_gflops=float(mean),
        )

    def _run(self) -> None:
        while not self._stop.is_set():
            payload = self._tl.get(timeout_sec=0.25)
            if payload is None:
                # closed and drained
                break

            self._processed_jobs += 1
            gflops = self._run_one_job(payload)
            self._gflops_sum += gflops
            self._gflops_samples += 1

            now = time.monotonic()
            if (now - self._last_report_t) >= self._report_interval_sec:
                mean_so_far = (
                    self._gflops_sum / self._gflops_samples
                    if self._gflops_samples
                    else 0.0
                )
                print(
                    f"[TU {self._tu_id}] jobs={self._processed_jobs} mean_gflops={mean_so_far:.3f}",
                    flush=True,
                )
                self._last_report_t = now

    def _run_one_job(self, payload: WorkPayload) -> float:
        n = int(payload.matrix_size)
        batch_window = int(payload.batch_window)
        seed = int(payload.seed)

        # Try NumPy first if installed; else use deterministic pure-Python kernel.
        gflops_np = _maybe_numpy_matmul(
            n=n,
            batch_window=batch_window,
            seed=seed,
            precision=payload.precision,
        )
        if gflops_np is not None:
            return float(gflops_np)

        # Pure-Python deterministic matmul kernel.
        mode = _precision_to_mode(payload.precision)

        a = _deterministic_matrix(n, seed=seed, mode=mode)
        b = _deterministic_matrix(n, seed=seed + 1, mode=mode)
        bt = _transpose(b)

        _ = a[0][0] + bt[0][0]

        start = time.monotonic() if hasattr(time, "monotonic") else time.monotonic()
        c: list[list[float]] | None = None
        for _ in range(batch_window):
            c = _matmul(a, bt)
        end = time.monotonic()

        sink = 0.0
        if c is not None:
            sink = float(c[0][0]) + float(c[-1][-1])
        _ = sink

        elapsed_s = max(1e-9, float(end - start))
        flops_per_matmul = 2.0 * (float(n) ** 3)
        total_flops = flops_per_matmul * float(batch_window)
        gflops = (total_flops / elapsed_s) / 1e9
        return float(gflops)
