"""Layer-wise Kimi K2 loader adapter implementing ``IKimik2Loader``.

Drop-in replacement for ``Kimik2LoaderAdapter``: instead of loading the full
model with ``AutoModelForCausalLM.from_pretrained`` it runs Kimi K2 through the
layer-wise host (``kimi_host.KimiLayerWiseHost``, backed by
``ResidentWeights`` + ``ExpertPager``).

Selection: construct this adapter wherever ``Kimik2LoaderAdapter`` is used, or
call :func:`build_kimik2_loader` which honours
``EGREGORE_KIMIK2_USE_LAYERWISE=1``.
"""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path

from egregore.interface.semantics_ports import IKimik2Loader, Kimik2LoaderError

# Root-level host modules (kimi_host / kimi_resident / kimi_pager / anchorum_kimi)
# live at the repository root. Import them via the same explicit path-insertion
# pattern used by egregore.interface.anchorum_router.
_REPO_ROOT = str(Path(__file__).resolve().parents[3])
if _REPO_ROOT not in sys.path:
    sys.path.insert(0, _REPO_ROOT)

DEFAULT_MODEL_DIR = (
    "/media/kark/MODELS_2TB3/ExecutiveIntelligenceservices.-main"
    "/models/kimi-k2-base"
)


class Kimik2LayerWiseLoaderAdapter:
    """``IKimik2Loader`` backed by the layer-wise Kimi K2 host.

    Structurally conforms to ``egregore.interface.semantics_ports.IKimik2Loader``
    (runtime_checkable Protocol); we do not inherit from it explicitly.
    """

    def __init__(self, model_dir: str | None = None) -> None:
        self.model_dir = model_dir or os.environ.get(
            "EGREGORE_KIMIK2_MODEL_PATH", DEFAULT_MODEL_DIR
        )
        self._validate_artifacts()
        self._lm = None

        # CI/tests may provide dummy shard files (empty placeholders); skip the
        # heavy host/tokenizer load in that case (mirrors Kimik2LoaderAdapter).
        if os.environ.get("KIMIK2_TEST_MODE") == "1" or not self._has_nonempty_shards():
            return

        try:
            from transformers import AutoTokenizer  # type: ignore[import-untyped]

            from anchorum_kimi import load_anchorum_lm

            tokenizer = AutoTokenizer.from_pretrained(
                self.model_dir, trust_remote_code=True
            )
            self._lm = load_anchorum_lm(
                base_path=self.model_dir, tokenizer=tokenizer
            )
        except Exception as exc:  # noqa: BLE001
            raise Kimik2LoaderError(
                f"Layer-wise host load failed: {exc}"
            ) from exc

    # -------------------------------------------------------------- artifacts
    def _has_nonempty_shards(self) -> bool:
        """Return True only if every shard exists and is nonempty.

        Any zero-byte shard means the model directory is in dummy mode
        (CI fixtures) or the download is incomplete. Both cases must
        skip the heavy load; only the fully-populated case proceeds.
        """
        for i in range(61):
            shard_path = os.path.join(
                self.model_dir, f"model-{i + 1}-of-61.safetensors"
            )
            try:
                if os.path.getsize(shard_path) == 0:
                    return False
            except OSError:
                return False
        return True
    def _validate_artifacts(self) -> None:
        index_path = os.path.join(self.model_dir, "model.safetensors.index.json")
        if not os.path.isfile(index_path):
            raise Kimik2LoaderError("Missing model.safetensors.index.json")
        try:
            with open(index_path) as f:
                json.load(f)
        except Exception as exc:
            raise Kimik2LoaderError(
                f"Corrupt model.safetensors.index.json: {exc}"
            ) from exc
        for i in range(61):
            shard = f"model-{i + 1}-of-61.safetensors"
            if not os.path.isfile(os.path.join(self.model_dir, shard)):
                raise Kimik2LoaderError(f"Missing shard: {shard}")
        for fname in ("config.json", "tokenizer_config.json"):
            if not os.path.isfile(os.path.join(self.model_dir, fname)):
                raise Kimik2LoaderError(f"Missing {fname}")

    # -------------------------------------------------------------- inference
    def generate(self, prompt: str, max_tokens: int, temperature: float = 0.0) -> str:
        if temperature != 0.0:
            raise Kimik2LoaderError("Temperature must be 0.0 for determinism.")
        if self._lm is None:
            raise Kimik2LoaderError(
                "Layer-wise host not loaded (dummy artifacts detected or "
                "KIMIK2_TEST_MODE=1)."
            )
        try:
            return self._lm.generate(prompt, max_tokens, temperature=0.0)
        except Kimik2LoaderError:
            raise
        except Exception as exc:  # noqa: BLE001
            raise Kimik2LoaderError(f"Inference failed: {exc}") from exc


def build_kimik2_loader(model_dir: str | None = None) -> IKimik2Loader:
    """Return the host-backed loader, or the Transformers loader if disabled.

    ``EGREGORE_KIMIK2_USE_LAYERWISE=1`` (default) selects the layer-wise host;
    set it to ``0`` to fall back to ``Kimik2LoaderAdapter``.
    """
    if os.environ.get("EGREGORE_KIMIK2_USE_LAYERWISE", "1") != "0":
        return Kimik2LayerWiseLoaderAdapter(model_dir)

    from egregore.infrastructure.kimik2_loader_adapter import Kimik2LoaderAdapter

    return Kimik2LoaderAdapter(model_dir)
