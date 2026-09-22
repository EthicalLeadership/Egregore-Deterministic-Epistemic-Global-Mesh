"""On-demand expert paging for the Kimi K2 (DeepSeek-V3 style) layer-wise host.

Holds an LRU cache of routed-expert MLP weights. Expert weights are stored
fp8 (e4m3) block-quantized on disk (128x128 blocks); we dequantize on page-in
and keep the fp32 result in the cache. Only the routed experts (the bulk of the
model) are paged; attention, norms, routers, embeddings and the lm_head are
handled by ``kimi_resident.ResidentWeights``.
"""

import json
import os
from collections import OrderedDict
from typing import cast

import torch
from safetensors import safe_open

BLOCK = 128


def dequant_block_fp8(
    weight_fp8: torch.Tensor, scale_inv: torch.Tensor
) -> torch.Tensor:
    """Dequantize a block-wise fp8 (e4m3) weight with its 128x128 scale_inv.

    ``weight_fp8`` is stored in a safetensors shard as raw uint8 bytes that
    must be reinterpreted as ``torch.float8_e4m3fn``. ``scale_inv`` has shape
    ``(ceil(rows/BLOCK), ceil(cols/BLOCK))``.

    Implementation note: the scale is expanded by a strided view + broadcast
    rather than ``repeat_interleave``. The two produce identical results, but
    the broadcast avoids materializing a full ``rows x cols`` fp32 scale
    matrix. For an 8192x8192 weight that is ~268 MB of transient allocation
    per projection, three projections per expert load — the difference
    between a working cache and an OOM on the same hardware.

    If ``rows`` or ``cols`` is not a multiple of ``BLOCK``, the weight is
    zero-padded to the next block boundary, dequantized, then sliced back.
    The padded region is multiplied by a scale and then discarded, so the
    returned tensor is exactly ``(rows, cols)``.
    """
    if weight_fp8.dtype == torch.uint8:
        weight_fp8 = weight_fp8.view(torch.float8_e4m3fn)
    weight_fp32 = weight_fp8.float()
    scale_fp32 = scale_inv.float()

    rows, cols = weight_fp32.shape
    rb, cb = scale_fp32.shape
    pr, pc = rb * BLOCK, cb * BLOCK

    if (pr, pc) == (rows, cols):
        w4 = weight_fp32.view(rb, BLOCK, cb, BLOCK)
    else:
        padded = torch.zeros(
            (pr, pc), dtype=weight_fp32.dtype, device=weight_fp32.device
        )
        padded[:rows, :cols] = weight_fp32
        w4 = padded.view(rb, BLOCK, cb, BLOCK)

    result = w4 * scale_fp32.view(rb, 1, cb, 1)
    return result.reshape(pr, pc)[:rows, :cols]


class ExpertPager:
    """LRU pager for routed-expert MLP weights (gate/up/down projections).

    Keys are ``(layer_idx, expert_id)``. Each cached entry is a dict of the
    three fp32 projection weights ready for ``x @ W.T``.
    """

    def __init__(
        self, base_path: str, cache_size: int = 16, device: str = "cpu"
    ) -> None:
        if cache_size < 1:
            raise ValueError(f"cache_size must be >= 1, got {cache_size}")
        self.base = base_path
        self.cache_size = cache_size
        self.device = device
        with open(
            os.path.join(base_path, "model.safetensors.index.json")
        ) as f:
            self.index = json.load(f)["weight_map"]
        self.cache: OrderedDict[
            tuple[int, int], dict[str, torch.Tensor]
        ] = OrderedDict()

    def _load_tensor(self, tensor_name: str) -> torch.Tensor:
        shard = self.index[tensor_name]
        with safe_open(
            os.path.join(self.base, shard), framework="pt", device="cpu"
        ) as f:
            return cast(torch.Tensor, f.get_tensor(tensor_name))

    def load_expert(
        self, layer: int, expert_id: int
    ) -> dict[str, torch.Tensor]:
        key = (layer, expert_id)
        if key in self.cache:
            self.cache.move_to_end(key)
            return self.cache[key]

        prefix = f"model.layers.{layer}.mlp.experts.{expert_id}"
        tensors: dict[str, torch.Tensor] = {}
        for proj in ("gate_proj", "up_proj", "down_proj"):
            w = self._load_tensor(f"{prefix}.{proj}.weight")
            s = self._load_tensor(f"{prefix}.{proj}.weight_scale_inv")
            if self.device != "cpu":
                # Dequantize on the target device. The fp8 bytes move first
                # (cheap), then the fp32 intermediate is allocated on the
                # accelerator rather than in host RAM. Peak host allocation
                # per expert drops by roughly a factor of 5.
                w = w.to(self.device)
                s = s.to(self.device)
            tensors[proj] = dequant_block_fp8(w, s)

        self.cache[key] = tensors
        if len(self.cache) > self.cache_size:
            self.cache.popitem(last=False)
        return tensors

    def expert_forward(
        self, layer: int, expert_id: int, hidden_states: torch.Tensor
    ) -> torch.Tensor:
        expert = self.load_expert(layer, expert_id)
        gate_out = torch.matmul(hidden_states, expert["gate_proj"].T)
        gate_out = torch.nn.functional.silu(gate_out)
        up_out = torch.matmul(hidden_states, expert["up_proj"].T)
        h = gate_out * up_out
        out = torch.matmul(h, expert["down_proj"].T)
        return out

    def clear(self) -> None:
        self.cache.clear()
