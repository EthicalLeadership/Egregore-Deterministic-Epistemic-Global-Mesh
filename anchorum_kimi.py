"""ANCHORUM adapter: route the deterministic LLM narrative through the Kimi
layer-wise host instead of the full Transformers loader.

This is a drop-in replacement for
``egregore.infrastructure.kimik2_loader_adapter.Kimik2LoaderAdapter``: both
implement the ``IKimik2Loader`` contract

    generate(prompt: str, max_tokens: int, temperature: float = 0.0) -> str

and raise ``Kimik2LoaderError`` on any failure or non-zero temperature.

The host keeps attention/norm/router/shared-expert weights resident
(``kimi_resident.ResidentWeights``) and pages routed experts on demand
(``kimi_pager.ExpertPager``), so the full ``AutoModelForCausalLM`` load is
never performed.
"""

from __future__ import annotations

import os
from importlib import import_module

import torch

def _resolve_loader_error() -> type[Exception]:
    """Return the package ``Kimik2LoaderError``, or a local fallback type."""
    try:
        module = import_module("egregore.interface.semantics_ports")
        error_type = getattr(module, "Kimik2LoaderError")
        if isinstance(error_type, type) and issubclass(error_type, Exception):
            return error_type
    except Exception:  # standalone/root context
        pass
    return type("Kimik2LoaderError", (Exception,), {})


Kimik2LoaderError = _resolve_loader_error()


from kimi_host import KimiLayerWiseHost, load_kimi_host

DEFAULT_MODEL_DIR = (
    "/media/kark/MODELS_2TB3/ExecutiveIntelligenceservices.-main"
    "/models/kimi-k2-base"
)


class AnchorumKimiLM:
    """Layer-wise Kimi K2 backend implementing the ``IKimik2Loader`` contract."""

    def __init__(
        self,
        base_path: str | None = None,
        device: str = "cpu",
        dtype: str = "float32",
        pager_cache_size: int = 16,
        max_seq_len: int = 8192,
        tokenizer: object | None = None,
    ) -> None:
        env_model_path = os.environ.get("EGREGORE_KIMIK2_MODEL_PATH")
        resolved_base_path = (
            base_path
            if base_path is not None
            else env_model_path if env_model_path is not None else DEFAULT_MODEL_DIR
        )
        self.base_path: str = str(resolved_base_path)
        self.device = device
        self.dtype = dtype
        self.pager_cache_size = pager_cache_size
        self.max_seq_len = max_seq_len
        self.tokenizer = tokenizer
        self._host: KimiLayerWiseHost | None = None

    # ---------------------------------------------------------------- host
    def _ensure_host(self) -> KimiLayerWiseHost:
        if self._host is None:
            self._host = load_kimi_host(
                self.base_path,
                device=self.device,
                dtype=self.dtype,
                pager_cache_size=self.pager_cache_size,
                max_seq_len=self.max_seq_len,
            )
        return self._host

    @property
    def host(self) -> KimiLayerWiseHost:
        return self._ensure_host()

    # ------------------------------------------------------------- IKimik2Loader
    def generate(self, prompt: str, max_tokens: int, temperature: float = 0.0) -> str:
        """Deterministic generation. Raises Kimik2LoaderError on any failure."""
        if temperature != 0.0:
            raise Kimik2LoaderError("Temperature must be 0.0 for determinism.")
        if self.tokenizer is None:
            raise Kimik2LoaderError("Tokenizer required for text generation.")
        try:
            host = self._ensure_host()
            ids = self.tokenizer.encode(prompt)  # type: ignore[attr-defined]
            input_ids = torch.tensor([ids], dtype=torch.long, device=host.device)
            out = host.generate(
                input_ids,
                max_new_tokens=max_tokens,
                do_sample=False,
                temperature=0.0,
            )
            decoded = self.tokenizer.decode(  # type: ignore[attr-defined]
                out[0].tolist(), skip_special_tokens=True
            )
            return str(decoded)
        except Kimik2LoaderError:
            raise
        except Exception as exc:  # noqa: BLE001
            raise Kimik2LoaderError(f"Inference failed: {exc}") from exc

    def generate_narrative(self, prompt: str, max_new_tokens: int = 256) -> str:
        """Alias used by the ANCHORUM case-narrative step."""
        return self.generate(prompt, max_new_tokens, temperature=0.0)

    # -------------------------------------------------------------- lifecycle
    def reset(self) -> None:
        """Drop the paged-expert cache (frees memory between cases)."""
        if self._host is not None:
            self._host.resident.pager.clear()


# Alias so this class can replace Kimik2LoaderAdapter with no other changes.
KimiLayerWiseLoaderAdapter = AnchorumKimiLM


def load_anchorum_lm(
    base_path: str | None = None,
    device: str = "cpu",
    dtype: str = "float32",
    pager_cache_size: int = 16,
    max_seq_len: int = 8192,
    tokenizer: object | None = None,
) -> AnchorumKimiLM:
    """Construct an :class:`AnchorumKimiLM` (host not loaded until first use)."""
    return AnchorumKimiLM(
        base_path=base_path,
        device=device,
        dtype=dtype,
        pager_cache_size=pager_cache_size,
        max_seq_len=max_seq_len,
        tokenizer=tokenizer,
    )
