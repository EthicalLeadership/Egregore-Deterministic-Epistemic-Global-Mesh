"""Layer-wise Kimi K2 (DeepSeek-V3 style) inference host.

This is the model host that ANCHORUM routes through instead of the full
Transformers loader. It keeps the attention/norm/router/shared-expert weights
resident (``ResidentWeights``) and pages the routed experts on demand
(``ExpertPager``), running the reference MLA attention and noaux-TC MoE gating
exactly as in ``modeling_deepseek.py`` in fp32 for determinism.
"""

import math
from typing import Any

import torch
import torch.nn.functional as F

from kimi_resident import KimiConfig, ResidentWeights, rms_norm


# --------------------------------------------------------------------------- #
# Yarn RoPE
# --------------------------------------------------------------------------- #
def yarn_get_mscale(scale: float = 1, mscale: float = 1) -> float:
    if scale <= 1:
        return 1.0
    return 0.1 * mscale * math.log(scale) + 1.0


def yarn_find_correction_dim(
    num_rotations: float,
    dim: int,
    base: float = 10000,
    max_position_embeddings: int = 2048,
) -> float:
    return (dim * math.log(max_position_embeddings / (num_rotations * 2 * math.pi))) / (
        2 * math.log(base)
    )


def yarn_find_correction_range(
    low_rot: float,
    high_rot: float,
    dim: int,
    base: float = 10000,
    max_position_embeddings: int = 2048,
) -> tuple[int, int]:
    low = math.floor(
        yarn_find_correction_dim(low_rot, dim, base, max_position_embeddings)
    )
    high = math.ceil(
        yarn_find_correction_dim(high_rot, dim, base, max_position_embeddings)
    )
    return max(low, 0), min(high, dim - 1)


def yarn_linear_ramp_mask(minimum: float, maximum: float, dim: int) -> torch.Tensor:
    if minimum == maximum:
        maximum += 0.001
    linear_func = (torch.arange(dim, dtype=torch.float32) - minimum) / (maximum - minimum)
    return torch.clamp(linear_func, 0, 1)


class YarnRotaryEmbedding:
    """Yarn-scaled rotary embedding for the Kimi K2 MLA qk_rope_head_dim."""

    def __init__(
        self,
        dim: int,
        max_position_embeddings: int = 2048,
        base: float = 10000,
        scaling_factor: float = 1.0,
        original_max_position_embeddings: int = 4096,
        beta_fast: float = 32,
        beta_slow: float = 1,
        mscale: float = 1,
        mscale_all_dim: float = 0,
    ) -> None:
        self.dim = dim
        self.max_position_embeddings = max_position_embeddings
        self.base = base
        self.scaling_factor = scaling_factor
        self.original_max_position_embeddings = original_max_position_embeddings
        self.beta_fast = beta_fast
        self.beta_slow = beta_slow
        self.mscale = mscale
        self.mscale_all_dim = mscale_all_dim
        self.cos_cached = None
        self.sin_cached = None

    def _set_cos_sin_cache(
        self, seq_len: int, device: Any, dtype: torch.dtype = torch.float32
    ) -> None:
        dim = self.dim
        freq_extra = 1.0 / (
            self.base
            ** (torch.arange(0, dim, 2, dtype=torch.float32, device=device) / dim)
        )
        freq_inter = 1.0 / (
            self.scaling_factor
            * self.base
            ** (torch.arange(0, dim, 2, dtype=torch.float32, device=device) / dim)
        )

        low, high = yarn_find_correction_range(
            self.beta_fast,
            self.beta_slow,
            dim,
            self.base,
            self.original_max_position_embeddings,
        )
        inv_freq_mask = 1.0 - yarn_linear_ramp_mask(low, high, dim // 2).to(
            device=device, dtype=torch.float32
        )
        inv_freq = freq_inter * (1 - inv_freq_mask) + freq_extra * inv_freq_mask

        t = torch.arange(seq_len, device=device, dtype=torch.float32)
        freqs = torch.outer(t, inv_freq)

        _mscale = float(
            yarn_get_mscale(self.scaling_factor, self.mscale)
            / yarn_get_mscale(self.scaling_factor, self.mscale_all_dim)
        )

        emb = torch.cat((freqs, freqs), dim=-1)
        self.cos_cached = (emb.cos() * _mscale).to(dtype)
        self.sin_cached = (emb.sin() * _mscale).to(dtype)

    def forward(
        self, seq_len: int, device: Any = None, dtype: torch.dtype = torch.float32
    ) -> tuple[torch.Tensor, torch.Tensor]:
        if self.cos_cached is None or seq_len > self.cos_cached.shape[0]:
            if device is None:
                device = (
                    self.cos_cached.device
                    if self.cos_cached is not None
                    else torch.device("cpu")
                )
            self._set_cos_sin_cache(seq_len, device, dtype)
        return self.cos_cached[:seq_len].to(dtype), self.sin_cached[:seq_len].to(dtype)


def rotate_half(x: torch.Tensor) -> torch.Tensor:
    x1 = x[..., : x.shape[-1] // 2]
    x2 = x[..., x.shape[-1] // 2 :]
    return torch.cat((-x2, x1), dim=-1)


def apply_rotary_pos_emb(
    q: torch.Tensor,
    k: torch.Tensor,
    cos: torch.Tensor,
    sin: torch.Tensor,
    position_ids: torch.Tensor,
    unsqueeze_dim: int = 1,
) -> tuple[torch.Tensor, torch.Tensor]:
    cos = cos[position_ids].unsqueeze(unsqueeze_dim)
    sin = sin[position_ids].unsqueeze(unsqueeze_dim)

    b, h, s, d = q.shape
    q = q.view(b, h, s, d // 2, 2).transpose(4, 3).reshape(b, h, s, d)

    b, h, s, d = k.shape
    k = k.view(b, h, s, d // 2, 2).transpose(4, 3).reshape(b, h, s, d)

    q_embed = (q * cos) + (rotate_half(q) * sin)
    k_embed = (k * cos) + (rotate_half(k) * sin)
    return q_embed, k_embed
# --------------------------------------------------------------------------- #
# Layer-wise host
# --------------------------------------------------------------------------- #
class KimiLayerWiseHost:
    """Layer-wise Kimi K2 host: MLA attention + paged MoE, fp32 faithful to the
    reference ``modeling_deepseek.py``."""

    def __init__(
        self,
        resident: ResidentWeights,
        device: str = "cpu",
        dtype: "torch.dtype | str" = torch.float32,
        max_seq_len: int = 8192,
    ) -> None:
        self.resident = resident
        self.cfg = resident.config
        self.device = device
        self.dtype = dtype if isinstance(dtype, torch.dtype) else getattr(torch, dtype)
        self.max_seq_len = max_seq_len
        self._current_layer = 0

        # MLA softmax scale, adjusted by yarn mscale^2 (matches reference).
        self.softmax_scale = self.cfg.q_head_dim ** (-0.5)
        mscale_all_dim = self.cfg.rope_scaling.get("mscale_all_dim", 0)
        if mscale_all_dim:
            mscale = yarn_get_mscale(self.cfg.rope_scaling["factor"], mscale_all_dim)
            self.softmax_scale = self.softmax_scale * mscale * mscale

        rs = self.cfg.rope_scaling
        self.rotary = YarnRotaryEmbedding(
            dim=self.cfg.qk_rope_head_dim,
            max_position_embeddings=rs.get("original_max_position_embeddings", 4096),
            base=self.cfg.rope_theta,
            scaling_factor=rs.get("factor", 1.0),
            original_max_position_embeddings=rs.get("original_max_position_embeddings", 4096),
            beta_fast=rs.get("beta_fast", 1.0),
            beta_slow=rs.get("beta_slow", 1.0),
            mscale=rs.get("mscale", 1.0),
            mscale_all_dim=rs.get("mscale_all_dim", 0),
        )

    # -------------------------------------------------------------- helpers
    def embed(self, input_ids: torch.Tensor) -> torch.Tensor:
        w = self.resident.tensors["embed"]
        return w[input_ids].to(self.dtype)

    def _new_state(self) -> dict[str, Any]:
        return {"layers": [None] * self.cfg.num_hidden_layers, "seq_len": 0}

    def _causal_mask(
        self, bsz: int, q_len: int, kv_len: int, device: Any
    ) -> torch.Tensor:
        if q_len == 1:
            return torch.zeros(bsz, 1, 1, kv_len, device=device, dtype=torch.float32)
        causal = torch.tril(torch.ones(q_len, kv_len, device=device, dtype=torch.bool))
        mask = torch.where(causal, 0.0, float("-inf")).to(torch.float32)
        return mask.unsqueeze(0).unsqueeze(0).expand(bsz, 1, q_len, kv_len)

    def _lm_forward(self, hidden: torch.Tensor) -> torch.Tensor:
        lm = self.resident.tensors["lm_head"]
        return hidden @ lm.T

    def _position_ids(
        self, bsz: int, q_len: int, offset: int, device: Any
    ) -> torch.Tensor:
        pos = torch.arange(offset, offset + q_len, device=device).unsqueeze(0).expand(bsz, q_len)
        return pos

    # ------------------------------------------------------------- attention
    def _attention(
        self,
        layer: int,
        x: torch.Tensor,
        cache: "dict[str, torch.Tensor] | None",
        position_ids: torch.Tensor,
        mask: torch.Tensor,
    ) -> tuple[torch.Tensor, dict[str, torch.Tensor]]:
        att = self.resident.attn[layer]
        cfg = self.cfg
        bsz, q_len, _ = x.shape
        nh = cfg.num_attention_heads

        q = x @ att["q_a_proj"].T
        q = rms_norm(q, att["q_a_layernorm"], cfg.rms_norm_eps)
        q = q @ att["q_b_proj"].T
        q = q.view(bsz, q_len, nh, cfg.q_head_dim).transpose(1, 2)
        q_nope, q_pe = q.split([cfg.qk_nope_head_dim, cfg.qk_rope_head_dim], dim=-1)

        ckv = x @ att["kv_a_proj_with_mqa"].T
        ckv, k_pe = ckv.split([cfg.kv_lora_rank, cfg.qk_rope_head_dim], dim=-1)
        k_pe = k_pe.view(bsz, q_len, 1, cfg.qk_rope_head_dim).transpose(1, 2)

        kv = rms_norm(ckv, att["kv_a_layernorm"], cfg.rms_norm_eps) @ att["kv_b_proj"].T
        kv = kv.view(bsz, q_len, nh, cfg.qk_nope_head_dim + cfg.v_head_dim).transpose(1, 2)
        k_nope, v = kv.split([cfg.qk_nope_head_dim, cfg.v_head_dim], dim=-1)

        kv_len = q_len + (cache["k"].shape[2] if cache is not None else 0)
        cos, sin = self.rotary.forward(kv_len, device=x.device, dtype=torch.float32)

        q_pe, k_pe = apply_rotary_pos_emb(q_pe, k_pe, cos, sin, position_ids)
        k_pe = k_pe.expand(bsz, nh, q_len, cfg.qk_rope_head_dim)

        q = torch.cat([q_nope, q_pe], dim=-1)  # [bsz, nh, q_len, 192]
        k = torch.cat([k_nope, k_pe], dim=-1)  # [bsz, nh, q_len, 192]

        if cache is not None:
            k = torch.cat([cache["k"], k], dim=2)
            v = torch.cat([cache["v"], v], dim=2)
        cache = {"k": k, "v": v}

        attn = (q @ k.transpose(2, 3)) * self.softmax_scale
        attn = attn + mask
        attn = F.softmax(attn, dim=-1, dtype=torch.float32).to(q.dtype)
        out = attn @ v  # [bsz, nh, q_len, v_head_dim]

        out = out.transpose(1, 2).contiguous().reshape(bsz, q_len, nh * cfg.v_head_dim)
        out = out @ att["o_proj"].T
        return out, cache
# ------------------------------------------------------------------ MLP
    def _dense_mlp(self, x: torch.Tensor) -> torch.Tensor:
        mlp = self.resident.dense_mlp
        g = F.silu(x @ mlp["gate_proj"].T)
        u = x @ mlp["up_proj"].T
        return (g * u) @ mlp["down_proj"].T

    def _moe_gate(self, flat: torch.Tensor) -> tuple[torch.Tensor, torch.Tensor]:
        """noaux-TC gate: sigmoid scoring + group-aware top-k (n_group=1 here)."""
        gate_w, gate_b = self.resident.routers[self._current_layer]
        cfg = self.cfg
        n = flat.shape[0]
        logits = flat @ gate_w.T
        scores = logits.sigmoid()
        scores_for_choice = scores + gate_b.unsqueeze(0)

        group_scores = (
            scores_for_choice.view(n, cfg.n_group, -1).topk(2, dim=-1)[0].sum(dim=-1)
        )
        group_idx = torch.topk(group_scores, k=cfg.topk_group, dim=-1, sorted=False)[1]
        group_mask = torch.zeros_like(group_scores)
        group_mask.scatter_(1, group_idx, 1)
        per_group = cfg.n_routed_experts // cfg.n_group
        score_mask = (
            group_mask.unsqueeze(-1).expand(n, cfg.n_group, per_group).reshape(n, -1)
        )
        tmp_scores = scores_for_choice.masked_fill(~score_mask.bool(), 0.0)
        topk_idx = torch.topk(tmp_scores, k=cfg.num_experts_per_tok, dim=-1, sorted=False)[1]
        topk_weight = scores.gather(1, topk_idx)

        if cfg.num_experts_per_tok > 1 and cfg.norm_topk_prob:
            denom = topk_weight.sum(dim=-1, keepdim=True) + 1e-20
            topk_weight = topk_weight / denom
        topk_weight = topk_weight * cfg.routed_scaling_factor
        return topk_idx, topk_weight

    def _moe_infer(
        self,
        layer: int,
        flat: torch.Tensor,
        topk_idx: torch.Tensor,
        topk_weight: torch.Tensor,
    ) -> torch.Tensor:
        """Token-binding routed expert compute, faithful to reference moe_infer."""
        cfg = self.cfg
        n, k = topk_idx.shape
        hidden = cfg.hidden_size
        flat_topk = topk_idx.view(-1)

        cnts = topk_idx.new_zeros((n, cfg.n_routed_experts))
        cnts.scatter_(1, topk_idx, 1)
        tokens_per_expert = cnts.sum(dim=0)

        idxs = flat_topk.argsort()
        sorted_x = flat[idxs // k]

        outs = []
        start = 0
        for e in range(cfg.n_routed_experts):
            nt = int(tokens_per_expert[e].item())
            if nt == 0:
                continue
            tok = sorted_x[start : start + nt]
            out = self.resident.pager.expert_forward(layer, e, tok)
            outs.append(out)
            start += nt

        routed = torch.cat(outs, dim=0) if outs else sorted_x.new_empty(0, hidden)
        new_x = torch.empty_like(routed)
        new_x[idxs] = routed
        final = (new_x.view(n, k, hidden) * topk_weight.unsqueeze(-1)).sum(dim=1)
        return final

    def _moe_mlp(self, layer: int, x: torch.Tensor) -> torch.Tensor:
        self._current_layer = layer
        cfg = self.cfg
        orig_shape = x.shape
        flat = x.view(-1, cfg.hidden_size)
        topk_idx, topk_weight = self._moe_gate(flat)
        routed = self._moe_infer(layer, flat, topk_idx, topk_weight)

        shared = self.resident.shared[layer]
        sh = F.silu(x @ shared["gate_proj"].T) * (x @ shared["up_proj"].T)
        sh = sh @ shared["down_proj"].T
        return (routed.view(orig_shape) + sh).to(self.dtype)

    def _mlp(self, layer: int, x: torch.Tensor) -> torch.Tensor:
        if layer < self.cfg.first_k_dense_replace:
            return self._dense_mlp(x).to(self.dtype)
        return self._moe_mlp(layer, x).to(self.dtype)

    # ---------------------------------------------------------- layer/forward
    def _layer_forward(
        self,
        layer: int,
        x: torch.Tensor,
        mask: torch.Tensor,
        cache: "dict[str, torch.Tensor] | None",
        position_ids: torch.Tensor,
    ) -> tuple[torch.Tensor, dict[str, torch.Tensor]]:
        cfg = self.cfg
        input_ln, post_ln = self.resident.norms[layer]
        x_norm = rms_norm(x, input_ln, cfg.rms_norm_eps)
        attn_out, cache = self._attention(layer, x_norm, cache, position_ids, mask)
        x = x + attn_out
        x_norm2 = rms_norm(x, post_ln, cfg.rms_norm_eps)
        mlp_out = self._mlp(layer, x_norm2)
        x = x + mlp_out
        return x, cache

    def forward(
        self, input_ids: torch.Tensor, state: "dict[str, Any] | None" = None
    ) -> tuple[torch.Tensor, dict[str, Any]]:
        cfg = self.cfg
        bsz, q_len = input_ids.shape
        if state is None:
            state = self._new_state()
            offset = 0
            mask = self._causal_mask(bsz, q_len, q_len, self.device)
        else:
            offset = state["seq_len"]
            kv_len = offset + q_len
            mask = self._causal_mask(bsz, q_len, kv_len, self.device)

        position_ids = self._position_ids(bsz, q_len, offset, self.device)
        hidden = self.embed(input_ids)

        for L in range(cfg.num_hidden_layers):
            hidden, state["layers"][L] = self._layer_forward(
                L, hidden, mask, state["layers"][L], position_ids
            )

        hidden = rms_norm(hidden, self.resident.tensors["final_norm"], cfg.rms_norm_eps)
        logits = self._lm_forward(hidden)
        state["seq_len"] += q_len
        return logits, state
# --------------------------------------------------------------- generate
    def _sample_next(
        self,
        logits: torch.Tensor,
        do_sample: bool,
        temperature: float,
        top_p: "float | None",
        seed: "int | None",
    ) -> torch.Tensor:
        next_logit = logits[:, -1, :]
        if not do_sample or temperature <= 0:
            return torch.argmax(next_logit, dim=-1)
        if seed is not None:
            torch.manual_seed(seed)
        scaled = next_logit / temperature
        probs = F.softmax(scaled, dim=-1)
        if top_p is not None and top_p < 1.0:
            sorted_probs, sorted_idx = probs.sort(dim=-1, descending=True)
            cumsum = sorted_probs.cumsum(dim=-1)
            cutoff = cumsum > top_p
            cutoff[:, 0] = False
            probs = probs.masked_fill(cutoff, 0.0)
            probs = probs / probs.sum(dim=-1, keepdim=True)
        return torch.multinomial(probs, num_samples=1).squeeze(-1)

    @torch.no_grad()
    def generate(
        self,
        input_ids: torch.Tensor,
        max_new_tokens: int = 128,
        do_sample: bool = False,
        temperature: float = 0.0,
        top_p: "float | None" = None,
        seed: "int | None" = None,
        stop_token_ids: "set[int] | None" = None,
        state: "dict[str, Any] | None" = None,
    ) -> torch.Tensor:
        cfg = self.cfg
        device = self.device
        input_ids = input_ids.to(device)

        stop = set(stop_token_ids) if stop_token_ids else set()
        if cfg.eos_token_id is not None:
            stop.add(cfg.eos_token_id)

        generated = input_ids.clone()
        logits, state = self.forward(generated, state)
        n_new = 0
        while n_new < max_new_tokens:
            next_id = self._sample_next(logits, do_sample, temperature, top_p, seed)
            generated = torch.cat([generated, next_id.unsqueeze(-1)], dim=-1)
            n_new += 1
            if int(next_id[0].item()) in stop:
                break
            logits, state = self.forward(next_id.unsqueeze(-1), state)
        return generated


def load_kimi_host(
    base_path: str,
    device: str = "cpu",
    dtype: str = "float32",
    pager_cache_size: int = 16,
    max_seq_len: int = 8192,
) -> KimiLayerWiseHost:
    """Load a ``KimiLayerWiseHost`` from a local Kimi K2 checkpoint directory.

    This is the entry point ANCHORUM uses to run the layer-wise host in place of
    the full Transformers loader.
    """
    resident = ResidentWeights(
        base_path,
        device=device,
        dtype=dtype,
        pager_cache_size=pager_cache_size,
        max_seq_len=max_seq_len,
    )
    return KimiLayerWiseHost(
        resident, device=device, dtype=dtype, max_seq_len=max_seq_len
    )
