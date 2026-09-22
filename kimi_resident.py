"""Resident weight host for the Kimi K2 (DeepSeek-V3 style) layer-wise model.

``ResidentWeights`` loads and keeps in memory every weight that is needed on
every step: the token embedding, per-layer MLA attention projections + layer
norms, the MoE router (gate) for layers 1..60, the shared expert MLP, the
dense layer-0 MLP, the final RMSNorm and the lm_head. Only the routed experts
are kept on disk and paged in by ``ExpertPager``.

All resident linear weights are dequantized from fp8 to fp32 on load so the
host runs deterministically in fp32 (matching the reference model's upcast
behaviour); pass ``dtype="bfloat16"`` to ``load_kimi_config``/the host to keep
compute in bf16 instead.
"""

import json
import math
import os
from dataclasses import dataclass, field
from typing import Any

import torch
from safetensors import safe_open

from kimi_pager import ExpertPager, dequant_block_fp8

BLOCK = 128


@dataclass
class KimiConfig:
    """Parsed subset of the Kimi K2 ``config.json`` used by the host."""

    vocab_size: int = 163840
    hidden_size: int = 7168
    num_hidden_layers: int = 61
    num_attention_heads: int = 64
    num_key_value_heads: int = 64
    q_lora_rank: int = 1536
    kv_lora_rank: int = 512
    qk_nope_head_dim: int = 128
    qk_rope_head_dim: int = 64
    v_head_dim: int = 128
    intermediate_size: int = 18432  # dense MLP (layer 0)
    moe_intermediate_size: int = 2048
    n_routed_experts: int = 384
    n_shared_experts: int = 1
    num_experts_per_tok: int = 8
    n_group: int = 1
    topk_group: int = 1
    first_k_dense_replace: int = 1
    norm_topk_prob: bool = True
    routed_scaling_factor: float = 2.827
    rms_norm_eps: float = 1e-6
    rope_theta: float = 50000.0
    rope_scaling: dict[str, Any] = field(
        default_factory=lambda: {
            "type": "yarn",
            "factor": 32.0,
            "original_max_position_embeddings": 4096,
            "beta_fast": 32.0,
            "beta_slow": 1.0,
            "mscale": 1.0,
            "mscale_all_dim": 1.0,
        }
    )
    bos_token_id: int = 163584
    eos_token_id: int = 163585
    tie_word_embeddings: bool = False

    @property
    def q_head_dim(self) -> int:
        return self.qk_nope_head_dim + self.qk_rope_head_dim

    @property
    def n_experts_per_layer(self) -> int:
        return self.n_routed_experts

    @classmethod
    def load(cls, base_path: str) -> "KimiConfig":
        with open(os.path.join(base_path, "config.json")) as f:
            raw = json.load(f)
        return cls(
            vocab_size=raw["vocab_size"],
            hidden_size=raw["hidden_size"],
            num_hidden_layers=raw["num_hidden_layers"],
            num_attention_heads=raw["num_attention_heads"],
            num_key_value_heads=raw["num_key_value_heads"],
            q_lora_rank=raw["q_lora_rank"],
            kv_lora_rank=raw["kv_lora_rank"],
            qk_nope_head_dim=raw["qk_nope_head_dim"],
            qk_rope_head_dim=raw["qk_rope_head_dim"],
            v_head_dim=raw["v_head_dim"],
            intermediate_size=raw["intermediate_size"],
            moe_intermediate_size=raw["moe_intermediate_size"],
            n_routed_experts=raw["n_routed_experts"],
            n_shared_experts=raw["n_shared_experts"],
            num_experts_per_tok=raw["num_experts_per_tok"],
            n_group=raw["n_group"],
            topk_group=raw["topk_group"],
            first_k_dense_replace=raw["first_k_dense_replace"],
            norm_topk_prob=raw["norm_topk_prob"],
            routed_scaling_factor=raw["routed_scaling_factor"],
            rms_norm_eps=raw["rms_norm_eps"],
            rope_theta=raw["rope_theta"],
            rope_scaling=raw["rope_scaling"],
            bos_token_id=raw["bos_token_id"],
            eos_token_id=raw["eos_token_id"],
            tie_word_embeddings=raw.get("tie_word_embeddings", False),
        )


def rms_norm(x: torch.Tensor, weight: "torch.Tensor | None", eps: float) -> torch.Tensor:
    """Reference DeepseekV3RMSNorm: upcast to fp32, normalize, scale, cast back."""
    input_dtype = x.dtype
    x = x.to(torch.float32)
    variance = x.pow(2).mean(-1, keepdim=True)
    x = x * torch.rsqrt(variance + eps)
    return (weight * x.to(input_dtype) if weight is not None else x.to(input_dtype))


class ResidentWeights:
    """Loads and retains all non-expert weights for the Kimi K2 host."""

    def __init__(
        self,
        base_path: str,
        device: str = "cpu",
        dtype: "torch.dtype | str" = torch.float32,
        pager_cache_size: int = 16,
        max_seq_len: int = 8192,
    ) -> None:
        self.base = base_path
        self.device = device
        self.dtype = dtype if isinstance(dtype, torch.dtype) else getattr(torch, dtype)
        self.config = KimiConfig.load(base_path)
        self.max_seq_len = max_seq_len
        self.pager = ExpertPager(base_path, cache_size=pager_cache_size, device=device)

        with open(os.path.join(base_path, "model.safetensors.index.json")) as f:
            self.index = json.load(f)["weight_map"]

        self.tensors: dict[str, torch.Tensor] = dict()
        self.attn: dict[int, dict[str, torch.Tensor]] = {}
        self.norms: dict[int, tuple[torch.Tensor, torch.Tensor]] = {}
        self.routers = {}
        self.shared = {}
        self.dense_mlp = {}

        self.load_embeddings()
        self.load_layers()
        self.load_final_norm()
        self.load_lm_head()

    # ------------------------------------------------------------------ utils
    def _has(self, name: str) -> bool:
        return name in self.index

    def _load_raw(self, name: str) -> torch.Tensor:
        shard = self.index[name]
        with safe_open(os.path.join(self.base, shard), framework="pt", device="cpu") as f:
            return f.get_tensor(name)

    def _load_linear(self, prefix: str) -> torch.Tensor:
        """Load an fp8/bf16 linear layer dequantized to fp32. Returns [out, in]."""
        w = self._load_raw(f"{prefix}.weight")
        scale_name = f"{prefix}.weight_scale_inv"
        if self._has(scale_name):
            s = self._load_raw(scale_name)
            w = dequant_block_fp8(w, s)
        else:
            if w.dtype in (torch.uint8, torch.float8_e4m3fn):
                raise ValueError(f"{prefix}.weight has no weight_scale_inv")
            w = w.float()
        return w.to(self.device, self.dtype if self.dtype == torch.float32 else self.dtype)

    def _load_scale(self, name: str) -> torch.Tensor:
        """Load a norm/embedding weight (no block quantization)."""
        w = self._load_raw(name).float()
        return w.to(self.device, self.dtype if self.dtype == torch.float32 else self.dtype)

    # ---------------------------------------------------------- outer weights
    def load_embeddings(self) -> None:
        w = self._load_raw("model.embed_tokens.weight").float()
        self.tensors["embed"] = w.to(self.device, self.dtype if self.dtype == torch.float32 else self.dtype)

    def load_final_norm(self) -> None:
        self.tensors["final_norm"] = self._load_scale("model.norm.weight")

    def load_lm_head(self) -> None:
        # tie_word_embeddings=False -> a real lm_head lives in the checkpoint.
        if self._has("lm_head.weight"):
            self.tensors["lm_head"] = self._load_linear("lm_head")
        else:
            # Fall back to tied embeddings if some checkpoint ties them.
            self.tensors["lm_head"] = self.tensors["embed"].to(self.device, self.dtype if self.dtype == torch.float32 else self.dtype)

    # ---------------------------------------------------------------- layers
    def load_layers(self) -> None:
        for L in range(self.config.num_hidden_layers):
            self.load_attention_layer(L)
            self.load_mlp_layer(L)

    def load_attention_layer(self, L: int) -> None:
        base = f"model.layers.{L}.self_attn"
        att = {}
        att["q_a_proj"] = self._load_linear(f"{base}.q_a_proj")
        att["q_a_layernorm"] = self._load_scale(f"{base}.q_a_layernorm.weight")
        att["q_b_proj"] = self._load_linear(f"{base}.q_b_proj")
        att["kv_a_proj_with_mqa"] = self._load_linear(f"{base}.kv_a_proj_with_mqa")
        att["kv_a_layernorm"] = self._load_scale(f"{base}.kv_a_layernorm.weight")
        att["kv_b_proj"] = self._load_linear(f"{base}.kv_b_proj")
        att["o_proj"] = self._load_linear(f"{base}.o_proj")
        self.attn[L] = att

        n1 = self._load_scale(f"model.layers.{L}.input_layernorm.weight")
        n2 = self._load_scale(f"model.layers.{L}.post_attention_layernorm.weight")
        self.norms[L] = (n1, n2)

    def load_mlp_layer(self, L: int) -> None:
        mlp_base = f"model.layers.{L}.mlp"
        if L < self.config.first_k_dense_replace:
            # Dense FFN: gate/up/down at config.intermediate_size.
            self.dense_mlp = {
                "gate_proj": self._load_linear(f"{mlp_base}.gate_proj"),
                "up_proj": self._load_linear(f"{mlp_base}.up_proj"),
                "down_proj": self._load_linear(f"{mlp_base}.down_proj"),
            }
            return
        # MoE layer: router + shared experts.
        gate_w = self._load_linear(f"{mlp_base}.gate")
        gate_b = self._load_raw(f"{mlp_base}.gate.e_score_correction_bias").float()
        self.routers[L] = (
            gate_w.to(self.device, self.dtype if self.dtype == torch.float32 else self.dtype),
            gate_b.to(self.device, self.dtype if self.dtype == torch.float32 else self.dtype),
        )
        shared = {}
        for proj in ("gate_proj", "up_proj", "down_proj"):
            shared[proj] = self._load_linear(f"{mlp_base}.shared_experts.{proj}")
        self.shared[L] = shared
