"""Resident weight host for the Kimi K2 (DeepSeek-V3 style) layer-wise model.

``ResidentWeights`` loads and keeps in memory every weight that is needed on
every step: the token embedding, per-layer MLA attention projections + layer
norms, the MoE router (gate) for layers ``>= first_k_dense_replace``, the
shared expert MLP, the dense layer-0 MLP, the final RMSNorm and the lm_head.
Only the routed experts are kept on disk and paged in by ``ExpertPager``.

All resident linear weights are dequantized from fp8 to fp32 on load so the
host runs deterministically in fp32 (matching the reference model's upcast
behaviour); pass ``dtype="bfloat16"`` to ``ResidentWeights`` to keep compute
in bf16 instead.
"""

import json
import logging
import os
from dataclasses import dataclass, field
from typing import Any

import torch
from safetensors import safe_open

from kimi_pager import ExpertPager, dequant_block_fp8

log = logging.getLogger(__name__)

BLOCK = 128

_FP8_DTYPES = (torch.uint8, torch.float8_e4m3fn, torch.float8_e5m2)


def _human_bytes(n: float) -> str:
    units = ("B", "KiB", "MiB", "GiB", "TiB")
    for unit in units:
        if n < 1024 or unit == units[-1]:
            return f"{int(n)} B" if unit == "B" else f"{n:.1f} {unit}"
        n /= 1024
    return f"{n:.1f} TiB"


@dataclass
class KimiConfig:
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
    intermediate_size: int = 18432
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
    rope_scaling: dict = field(default_factory=lambda: {
        "type": "yarn", "factor": 32.0,
        "original_max_position_embeddings": 4096,
        "beta_fast": 32.0, "beta_slow": 1.0,
        "mscale": 1.0, "mscale_all_dim": 1.0,
    })
    bos_token_id: int = 163584
    eos_token_id: int = 163585
    tie_word_embeddings: bool = False
    rms_norm_unit_offset: bool = False

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
            rms_norm_unit_offset=raw.get("rms_norm_unit_offset", False),
        )


def rms_norm(x, weight, eps, unit_offset=False):
    input_dtype = x.dtype
    x = x.to(torch.float32)
    variance = x.pow(2).mean(-1, keepdim=True)
    x = x * torch.rsqrt(variance + eps)
    x = x.to(input_dtype)
    if weight is None:
        return x
    scale = (1.0 + weight) if unit_offset else weight
    return scale * x


class ResidentWeights:
    def __init__(self, base_path, device="cpu", dtype=torch.float32,
                 pager_cache_size=16, max_seq_len=8192):
        self.base = base_path
        self.device = device
        self.dtype = dtype if isinstance(dtype, torch.dtype) else getattr(torch, dtype)
        self.config = KimiConfig.load(base_path)
        self.max_seq_len = max_seq_len
        self.pager = ExpertPager(base_path, cache_size=pager_cache_size, device=device)

        with open(os.path.join(base_path, "model.safetensors.index.json")) as f:
            self.index = json.load(f)["weight_map"]

        self.tensors = {}
        self.attn = {}
        self.norms = {}
        self.routers = {}
        self.shared = {}
        self.dense_mlp = {}

        self.load_embeddings()
        self.load_layers()
        self.load_final_norm()
        self.load_lm_head()

        log.info("ResidentWeights ready: %d layers, dtype=%s, device=%s",
                 self.config.num_hidden_layers, self.dtype, self.device)

    def _has(self, name):
        return name in self.index

    def _to(self, t):
        return t.to(device=self.device, dtype=self.dtype)

    def _load_raw(self, name):
        shard = self.index[name]
        with safe_open(os.path.join(self.base, shard), framework="pt", device="cpu") as f:
            return f.get_tensor(name)

    def _load_linear(self, prefix):
        w = self._load_raw(f"{prefix}.weight")
        scale_name = f"{prefix}.weight_scale_inv"
        if self._has(scale_name):
            s = self._load_raw(scale_name)
            w = dequant_block_fp8(w, s)
        elif w.dtype in _FP8_DTYPES:
            raise ValueError(f"{prefix}.weight is {w.dtype} but has no weight_scale_inv")
        else:
            w = w.float()
        return self._to(w)

    def _load_scale(self, name):
        return self._to(self._load_raw(name).float())

    def load_embeddings(self):
        self.tensors["embed"] = self._to(self._load_raw("model.embed_tokens.weight").float())

    def load_final_norm(self):
        self.tensors["final_norm"] = self._load_scale("model.norm.weight")

    def load_lm_head(self):
        if self.config.tie_word_embeddings:
            self.tensors["lm_head"] = self.tensors["embed"]
            return
        if self._has("lm_head.weight"):
            self.tensors["lm_head"] = self._load_linear("lm_head")
            return
        log.warning("lm_head.weight missing; falling back to embedding table")
        self.tensors["lm_head"] = self.tensors["embed"]

    def load_layers(self):
        for L in range(self.config.num_hidden_layers):
            self.load_attention_layer(L)
            self.load_mlp_layer(L)

    def load_attention_layer(self, L):
        cfg = self.config
        base = f"model.layers.{L}.self_attn"
        att = {
            "q_a_proj": self._load_linear(f"{base}.q_a_proj"),
            "q_a_layernorm": self._load_scale(f"{base}.q_a_layernorm.weight"),
            "q_b_proj": self._load_linear(f"{base}.q_b_proj"),
            "kv_a_proj_with_mqa": self._load_linear(f"{base}.kv_a_proj_with_mqa"),
            "kv_a_layernorm": self._load_scale(f"{base}.kv_a_layernorm.weight"),
            "kv_b_proj": self._load_linear(f"{base}.kv_b_proj"),
            "o_proj": self._load_linear(f"{base}.o_proj"),
        }
        expected = {
            "q_a_proj": (cfg.q_lora_rank, cfg.hidden_size),
            "q_b_proj": (cfg.num_attention_heads * cfg.q_head_dim, cfg.q_lora_rank),
            "kv_a_proj_with_mqa": (cfg.kv_lora_rank + cfg.qk_rope_head_dim, cfg.hidden_size),
            "kv_b_proj": (cfg.num_attention_heads * (cfg.qk_nope_head_dim + cfg.v_head_dim), cfg.kv_lora_rank),
            "o_proj": (cfg.hidden_size, cfg.num_attention_heads * cfg.v_head_dim),
        }
        for name, want in expected.items():
            got = tuple(att[name].shape)
            if got != want:
                raise ValueError(f"layer {L} self_attn.{name}: expected {want}, got {got}")
        self.attn[L] = att
        self.norms[L] = (
            self._load_scale(f"model.layers.{L}.input_layernorm.weight"),
            self._load_scale(f"model.layers.{L}.post_attention_layernorm.weight"),
        )

    def load_mlp_layer(self, L):
        cfg = self.config
        mlp_base = f"model.layers.{L}.mlp"
        if L < cfg.first_k_dense_replace:
            self.dense_mlp[L] = {
                "gate_proj": self._load_linear(f"{mlp_base}.gate_proj"),
                "up_proj": self._load_linear(f"{mlp_base}.up_proj"),
                "down_proj": self._load_linear(f"{mlp_base}.down_proj"),
            }
            return
        gate_w = self._load_linear(f"{mlp_base}.gate")
        gate_b = self._to(self._load_raw(f"{mlp_base}.gate.e_score_correction_bias").float())
        self.routers[L] = (gate_w, gate_b)
        self.shared[L] = {
            proj: self._load_linear(f"{mlp_base}.shared_experts.{proj}")
            for proj in ("gate_proj", "up_proj", "down_proj")
        }

    def residency_report(self):
        embed = self.tensors.get("embed")
        lm_head = self.tensors.get("lm_head")
        final_norm = self.tensors.get("final_norm")

        def _b(t):
            return t.numel() * t.element_size()

        buckets = {
            "embed": [embed] if embed is not None else [],
            "lm_head": [lm_head] if lm_head is not None and lm_head is not embed else [],
            "final_norm": [final_norm] if final_norm is not None else [],
            "attn": [], "norms": [], "routers": [], "shared": [], "dense_mlp": [],
        }
        for att in self.attn.values():
            buckets["attn"].extend(att.values())
        for n1, n2 in self.norms.values():
            buckets["norms"].extend((n1, n2))
        for w, b in self.routers.values():
            buckets["routers"].extend((w, b))
        for s in self.shared.values():
            buckets["shared"].extend(s.values())
        for d in self.dense_mlp.values():
            buckets["dense_mlp"].extend(d.values())

        by_cat = {name: {"tensors": len(ts),
                          "bytes": sum(_b(t) for t in ts),
                          "human": _human_bytes(sum(_b(t) for t in ts))}
                  for name, ts in buckets.items()}
        total_bytes = sum(c["bytes"] for c in by_cat.values())
        return {
            "device": self.device,
            "dtype": str(self.dtype).replace("torch.", ""),
            "layers_loaded": {
                "attn": len(self.attn), "norms": len(self.norms),
                "routers": len(self.routers), "shared": len(self.shared),
                "dense_mlp": len(self.dense_mlp),
            },
            "total_tensors": sum(c["tensors"] for c in by_cat.values()),
            "total_bytes": total_bytes,
            "total_human": _human_bytes(total_bytes),
            "by_category": by_cat,
        }

    def free_layer(self, L):
        freed = 0

        def _sum(v):
            if v is None:
                return 0
            if isinstance(v, torch.Tensor):
                return v.numel() * v.element_size()
            if isinstance(v, tuple):
                return sum(_sum(x) for x in v)
            if isinstance(v, dict):
                return sum(_sum(x) for x in v.values())
            return 0

        for c in (self.attn, self.norms, self.routers, self.shared, self.dense_mlp):
            if L in c:
                freed += _sum(c.pop(L))
        return freed

    def free_all_layers(self):
        ids = (set(self.attn) | set(self.norms) | set(self.routers)
               | set(self.shared) | set(self.dense_mlp))
        return sum(self.free_layer(L) for L in ids)
