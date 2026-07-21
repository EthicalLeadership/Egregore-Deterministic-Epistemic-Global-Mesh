"""Placement policy — decide CPU vs GPU inference for a given model and request."""

from __future__ import annotations

from egregore.infrastructure.hardware_profiler import HardwareSnapshot, gpu_info
from egregore.interface.placement_policy_ports import PlacementDecision

# Re-export for backwards compatibility.
__all__ = ["PlacementDecision", "decide_placement"]

# Conservative headroom multiplier: model must fit within free VRAM / multiplier.
VRAM_HEADROOM = 1.5

# Default thread count for CPU-only inference.
DEFAULT_CPU_THREADS = 4


def decide_placement(
    model_size_bytes: int,
    hardware: HardwareSnapshot | None = None,
    request_max_tokens: int = 256,
) -> PlacementDecision:
    """
    Decide how to place a model for inference.

    Rules:
    - If a GPU has enough free VRAM (with headroom), offload all layers.
    - If a GPU has some VRAM but not enough, offload as many layers as fit.
    - If no GPU, run CPU-only.
    """
    # CpuInfo is intentionally omitted for offline placement.
    # Justification for the arg-type suppression: callers may supply hardware without CPU details.
    hw = hardware or HardwareSnapshot(cpu=None, gpus=gpu_info())  # type: ignore[arg-type]
    if not hw.gpus:
        return PlacementDecision(
            n_gpu_layers=0,
            n_threads=DEFAULT_CPU_THREADS,
            reason="No GPU detected; running CPU-only",
        )

    # Pick the GPU with the most free VRAM.
    best_gpu = max(hw.gpus, key=lambda g: g.free_vram_bytes)
    available = best_gpu.free_vram_bytes

    # Required VRAM including activation/KV-cache headroom.
    required = int(model_size_bytes * VRAM_HEADROOM)

    if available >= required:
        return PlacementDecision(
            n_gpu_layers=-1,  # all layers
            n_threads=max(1, DEFAULT_CPU_THREADS // 2),
            reason=f"GPU '{best_gpu.name}' has {available // (1024**2)} MiB free; full offload",
        )

    # Partial offload estimate: each layer ~= model_size / (total_layers).
    # We don't know layer count here, so use a rough fraction.
    if available > model_size_bytes * 0.2:
        fraction = available / required
        # n_gpu_layers is later passed to llama_cpp as a count, not a fraction.
        # Return a positive integer representing an estimate; the caller can ignore if unsure.
        estimated_layers = max(1, int(32 * fraction))
        return PlacementDecision(
            n_gpu_layers=estimated_layers,
            n_threads=DEFAULT_CPU_THREADS,
            reason=f"GPU '{best_gpu.name}' has {available // (1024**2)} MiB free; partial offload ({estimated_layers} layers estimated)",
        )

    return PlacementDecision(
        n_gpu_layers=0,
        n_threads=DEFAULT_CPU_THREADS,
        reason=f"GPU '{best_gpu.name}' VRAM too low ({available // (1024**2)} MiB free); CPU-only",
    )
