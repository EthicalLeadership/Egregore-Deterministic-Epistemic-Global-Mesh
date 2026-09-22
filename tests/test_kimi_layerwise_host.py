"""Offline tests for the layer-wise Kimi K2 host.

These tests exercise the deterministic, weight-light parts of the host
(``kimi_host`` / ``kimi_resident``) without loading the 1T checkpoint:
Yarn RoPE, RMSNorm, the noaux-TC MoE gate, and the token-binding paged-expert
reduction. They assert parity against the reference ``modeling_deepseek.py``
formulas re-implemented inline.
"""

from __future__ import annotations

import math
import sys
from pathlib import Path

import pytest

torch = pytest.importorskip("torch")
pytest.importorskip("safetensors")

# The host modules live at the repository root (not under src/).
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from kimi_host import (  # noqa: E402
    KimiLayerWiseHost,
    YarnRotaryEmbedding,
    yarn_find_correction_range,
    yarn_get_mscale,
)
from kimi_resident import KimiConfig, rms_norm  # noqa: E402


class _FakeResident:
    """Minimal resident stub: only ``config`` / ``routers`` / ``pager`` are used."""

    def __init__(self, cfg: KimiConfig) -> None:
        self.config = cfg
        self.routers: dict[int, tuple[torch.Tensor, torch.Tensor]] = {}
        self.pager = None
        self.tensors: dict[str, torch.Tensor] = {}


class _ScalingPager:
    """Deterministic expert: expert e maps x -> (e + 1) * x."""

    def expert_forward(self, layer: int, expert_id: int, x: torch.Tensor) -> torch.Tensor:
        return x * float(expert_id + 1)


def _small_cfg() -> KimiConfig:
    return KimiConfig(
        hidden_size=8,
        n_routed_experts=6,
        num_experts_per_tok=2,
        n_group=1,
        topk_group=1,
        routed_scaling_factor=2.827,
        norm_topk_prob=True,
    )


# --------------------------------------------------------------------------- #
# Config
# --------------------------------------------------------------------------- #
def test_config_q_head_dim_and_group_defaults() -> None:
    cfg = KimiConfig()
    assert cfg.q_head_dim == 192
    assert cfg.n_group == 1
    assert cfg.topk_group == 1


def test_host_softmax_scale_matches_yarn_reference() -> None:
    cfg = KimiConfig()
    host = KimiLayerWiseHost(_FakeResident(cfg), device="cpu")
    mscale = yarn_get_mscale(cfg.rope_scaling["factor"], cfg.rope_scaling["mscale_all_dim"])
    expected = (192 ** -0.5) * mscale * mscale
    assert host.softmax_scale == pytest.approx(expected)


# --------------------------------------------------------------------------- #
# RMSNorm
# --------------------------------------------------------------------------- #
def test_rms_norm_matches_reference() -> None:
    x = torch.tensor([[3.0, 4.0]])
    w = torch.tensor([1.0, 2.0])
    out = rms_norm(x, w, 1e-6)
    variance = x.pow(2).mean(-1, keepdim=True)
    expected = w * (x * torch.rsqrt(variance + 1e-6))
    assert torch.allclose(out, expected, atol=1e-6)


# --------------------------------------------------------------------------- #
# Yarn RoPE
# --------------------------------------------------------------------------- #
def test_yarn_mscale() -> None:
    assert yarn_get_mscale(1.0, 1.0) == 1.0
    assert yarn_get_mscale(32.0, 1.0) == pytest.approx(0.1 * math.log(32.0) + 1.0)


def test_yarn_correction_range_is_clamped() -> None:
    low, high = yarn_find_correction_range(1.0, 1.0, 64, 50000.0, 4096)
    assert 0 <= low <= high <= 63


def test_yarn_rotary_shapes_and_finiteness() -> None:
    rope = YarnRotaryEmbedding(
        dim=64,
        base=50000.0,
        scaling_factor=32.0,
        original_max_position_embeddings=4096,
        beta_fast=1.0,
        beta_slow=1.0,
        mscale=1.0,
        mscale_all_dim=1.0,
    )
    cos, sin = rope.forward(16)
    assert cos.shape == (16, 64)
    assert sin.shape == (16, 64)
    assert torch.isfinite(cos).all() and torch.isfinite(sin).all()
    # Position 0 has zero rotation.
    assert torch.allclose(cos[0], torch.ones(64), atol=1e-6)
    assert torch.allclose(sin[0], torch.zeros(64), atol=1e-6)


# --------------------------------------------------------------------------- #
# MoE gate (noaux-TC)
# --------------------------------------------------------------------------- #
def test_moe_gate_matches_reference_formula() -> None:
    cfg = _small_cfg()
    torch.manual_seed(0)
    gate_w = torch.randn(cfg.n_routed_experts, cfg.hidden_size)
    gate_b = torch.randn(cfg.n_routed_experts)
    resident = _FakeResident(cfg)
    resident.routers[1] = (gate_w, gate_b)
    host = KimiLayerWiseHost(resident, device="cpu")
    host._current_layer = 1

    flat = torch.randn(4, cfg.hidden_size)
    idx, weight = host._moe_gate(flat)

    # Reference (modeling_deepseek.MoEGate) for n_group=topk_group=1.
    scores = (flat @ gate_w.T).sigmoid()
    scores_for_choice = scores + gate_b.unsqueeze(0)
    ref_idx = torch.topk(scores_for_choice, k=cfg.num_experts_per_tok, dim=-1, sorted=False)[1]
    ref_w = scores.gather(1, ref_idx)
    ref_w = ref_w / (ref_w.sum(dim=-1, keepdim=True) + 1e-20)
    ref_w = ref_w * cfg.routed_scaling_factor

    assert idx.shape == (4, cfg.num_experts_per_tok)
    assert set(idx[0].tolist()) == set(ref_idx[0].tolist())
    assert torch.allclose(
        weight.sort(dim=-1).values, ref_w.sort(dim=-1).values, atol=1e-6
    )


# --------------------------------------------------------------------------- #
# Token-binding paged-expert reduction
# --------------------------------------------------------------------------- #
def test_moe_infer_reduction_matches_reference() -> None:
    cfg = _small_cfg()
    torch.manual_seed(1)
    gate_w = torch.randn(cfg.n_routed_experts, cfg.hidden_size)
    gate_b = torch.zeros(cfg.n_routed_experts)
    resident = _FakeResident(cfg)
    resident.routers[1] = (gate_w, gate_b)
    resident.pager = _ScalingPager()
    host = KimiLayerWiseHost(resident, device="cpu")
    host._current_layer = 1

    flat = torch.randn(5, cfg.hidden_size)
    idx, weight = host._moe_gate(flat)
    out = host._moe_infer(1, flat, idx, weight)

    # Reference: per-token weighted sum over selected experts (pager = (e+1)*x).
    ref = torch.zeros_like(flat)
    for t in range(flat.shape[0]):
        for j in range(cfg.num_experts_per_tok):
            e = int(idx[t, j].item())
            ref[t] += weight[t, j] * (float(e + 1) * flat[t])
    assert out.shape == flat.shape
    assert torch.allclose(out, ref, atol=1e-5)


# --------------------------------------------------------------------------- #
# Causal mask
# --------------------------------------------------------------------------- #
def test_causal_mask_prefill_and_decode() -> None:
    cfg = _small_cfg()
    host = KimiLayerWiseHost(_FakeResident(cfg), device="cpu")

    mask = host._causal_mask(1, 3, 3, "cpu")
    assert mask.shape == (1, 1, 3, 3)
    assert torch.isneginf(mask[0, 0, 0, 1])  # token 0 cannot see token 1
    assert mask[0, 0, 2, 0] == 0.0  # token 2 sees token 0

    decode_mask = host._causal_mask(1, 1, 4, "cpu")
    assert decode_mask.shape == (1, 1, 1, 4)
    assert torch.all(decode_mask == 0.0)  # decode attends to all cached tokens
