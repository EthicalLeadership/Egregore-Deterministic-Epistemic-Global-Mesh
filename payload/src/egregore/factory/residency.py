"""VRAM residency manager — pre-flight checks and the heavy-pass swap.

Phase 6. Two rules:

1. **Pre-flight, not post-crash.** Before each station, check free VRAM. A
   predicted shortfall becomes BLOCKED (``vram_insufficient``) in
   milliseconds instead of a mid-run CUDA OOM that costs the whole station.
2. **Swap is serialized.** The heavy pass (HF 8-bit) never coexists with the
   hot GGUF residents. Unload hot → load heavy → run → unload heavy →
   restore hot. A single lock makes overlapping 7B-class loads impossible.

Telemetry note: every check emits nothing by itself — callers attach
``vram_free_mb`` to station events (see factory_router).
"""

from __future__ import annotations

import gc
import logging
import threading
from collections.abc import Iterator
from contextlib import contextmanager
from typing import Any

logger = logging.getLogger("egregore.factory.residency")


class VramInsufficientError(RuntimeError):
    """Raised by pre_flight when predicted need exceeds free VRAM."""

    def __init__(self, need_mb: int, free_mb: int, station: str = "") -> None:
        self.need_mb = need_mb
        self.free_mb = free_mb
        self.station = station
        super().__init__(
            f"vram_insufficient: need ~{need_mb} MB, have {free_mb} MB free"
            + (f" (station: {station})" if station else "")
        )


def vram_free_mb() -> int:
    """Free VRAM in MiB. NVML first (no torch init), torch fallback."""
    try:
        import pynvml  # type: ignore[import-untyped]

        pynvml.nvmlInit()
        try:
            handle = pynvml.nvmlDeviceGetHandleByIndex(0)
            info = pynvml.nvmlDeviceGetMemoryInfo(handle)
            return int(info.free // (1024 * 1024))
        finally:
            pynvml.nvmlShutdown()
    except ImportError:
        logger.debug("pynvml unavailable, falling back to torch")
    try:
        import torch

        if torch.cuda.is_available():
            free, _total = torch.cuda.mem_get_info(0)
            return int(free // (1024 * 1024))
    except Exception as exc:  # noqa: BLE001
        logger.debug("torch mem_get_info failed: %s", exc)
    return -1  # unknown — pre-flight treats as "cannot verify", logs only


class ResidencyManager:
    """Owns the hot/swap layout on a single GPU.

    hot: GgufBackend residents (7B + 1.5B, ~6.7 GB)
    heavy: CoderBackend HF 8-bit (~8 GB) — loaded only inside heavy_pass()
    """

    def __init__(
        self,
        *,
        gguf_backend: Any = None,
        heavy_backend_factory: Any = None,
        station_budget_mb: int = 2500,
        min_free_mb: int = 1200,
    ) -> None:
        self._gguf = gguf_backend
        self._heavy_factory = heavy_backend_factory
        self._station_budget_mb = station_budget_mb
        self._min_free_mb = min_free_mb
        self._heavy: Any = None
        self._swap_lock = threading.Lock()

    # ------------------------------------------------------------ pre-flight
    def pre_flight(self, station: str = "") -> int:
        """Return free MB. Raise VramInsufficient on predicted shortfall."""
        free = vram_free_mb()
        if free < 0:
            logger.warning("pre_flight: VRAM state unknown, proceeding (station=%s)", station)
            return free
        if free < self._min_free_mb or free < self._station_budget_mb // 2:
            raise VramInsufficientError(
                need_mb=max(self._station_budget_mb, self._min_free_mb),
                free_mb=free,
                station=station,
            )
        return free

    # ------------------------------------------------------------ swap
    @contextmanager
    def heavy_pass(self) -> Iterator[Any]:
        """Run the HF 8-bit heavy pass with the hot residents unloaded.

        Serialized: only one heavy pass at a time, and the hot GGUF models
        are always restored afterwards (even on failure).
        """
        with self._swap_lock:
            unloaded: list[str] = []
            if self._gguf is not None:
                unloaded = self._gguf.loaded_models()
                self._gguf.unload_all()
                gc.collect()
            try:
                if self._heavy is None:
                    if self._heavy_factory is None:
                        raise RuntimeError("no heavy backend factory configured")
                    logger.info("heavy_pass: loading HF 8-bit heavy backend")
                    self._heavy = self._heavy_factory()
                yield self._heavy
            finally:
                if self._heavy is not None:
                    logger.info("heavy_pass: unloading heavy backend")
                    close = getattr(self._heavy, "close", None)
                    if callable(close):
                        close()
                    del self._heavy
                    self._heavy = None
                    gc.collect()
                    try:
                        import torch

                        if torch.cuda.is_available():
                            torch.cuda.empty_cache()
                    except Exception as exc:  # noqa: BLE001
                        logger.debug("torch empty_cache failed: %s", exc)
                if self._gguf is not None and unloaded:
                    logger.info("heavy_pass: restoring hot residents %s", unloaded)
                    for name in unloaded:
                        self._gguf._get(name)  # noqa: SLF001
