"""Native Coder backend — loads fine-tuned DeepSeek-Coder directly in-process.

No Ollama. No external APIs. Egregore IS the inference engine.
"""

from __future__ import annotations

import logging
import os
import time
from pathlib import Path
from typing import Any

from egregore.domain.inference_models import (
    ChatMessage,
    ChatRequest,
    ChatResponse,
    InferenceMode,
)
from egregore.interface.llm_ports import ILlmClient

logger = logging.getLogger(__name__)

MODEL_PATH = os.environ.get(
    "EGREGORE_CODER_MODEL_PATH",
    "/mnt/blackstar/egregor_inventory/my_coder_fixed_hf",
)

# Minimum free GPU memory (bytes) before we warn but still attempt load.
_GPU_WARN_THRESHOLD_BYTES = 7 * 1024**3


def _is_enabled() -> bool:
    """Return True unless the user explicitly disabled the native Coder backend."""
    value = os.environ.get("EGREGORE_CODER_BACKEND_ENABLED", "true").lower()
    return value not in ("0", "false", "no", "off")


class CoderBackend(ILlmClient):
    """Native backend that loads the fine-tuned Coder model directly.

    Uses 8-bit quantization via bitsandbytes to fit in RTX 3060 12GB.
    Model is loaded once at init and kept in GPU memory.
    """

    def __init__(self, model_path: str | None = None, enabled: bool | None = None):
        self.model_path = model_path or MODEL_PATH
        self._enabled = _is_enabled() if enabled is None else enabled
        self._tokenizer = None
        self._model = None
        self._device = None

        if not self._enabled:
            logger.info("CoderBackend disabled by EGREGORE_CODER_BACKEND_ENABLED")
            return

        if not Path(self.model_path).exists():
            logger.warning(
                "CoderBackend model path does not exist: %s. "
                "Backend will report unhealthy until the model is present.",
                self.model_path,
            )
            return

        self._load_model()
        self._maybe_warmup()

    def _load_model(self) -> None:
        """Load model and tokenizer into GPU memory."""
        try:
            import torch
            from transformers import (
                AutoModelForCausalLM,
                AutoTokenizer,
                BitsAndBytesConfig,
            )
        except ImportError as exc:
            raise RuntimeError(
                "CoderBackend requires transformers, torch, bitsandbytes and accelerate. "
                "Install: pip install -e '.[llm-native]'"
            ) from exc

        logger.info("Loading Coder model from %s...", self.model_path)

        # Optional memory sanity check.
        if torch.cuda.is_available():
            try:
                free, total = torch.cuda.mem_get_info()
                logger.info(
                    "GPU memory: %.1f GB free / %.1f GB total",
                    free / 1024**3,
                    total / 1024**3,
                )
                if free < _GPU_WARN_THRESHOLD_BYTES:
                    logger.warning(
                        "Less than 7 GB GPU memory free; model load may OOM."
                    )
            except Exception:
                pass

        self._tokenizer = AutoTokenizer.from_pretrained(
            self.model_path, trust_remote_code=True
        )

        bnb_config = BitsAndBytesConfig(
            load_in_8bit=True,
            bnb_8bit_compute_dtype=torch.float16,
        )

        self._model = AutoModelForCausalLM.from_pretrained(
            self.model_path,
            quantization_config=bnb_config,
            device_map="auto",
            trust_remote_code=True,
        )
        self._model.eval()
        self._device = self._model.device

        logger.info("Coder model loaded on %s", self._device)

    def _maybe_warmup(self) -> None:
        """Run a minimal generation if EGREGORE_CODER_WARMUP is enabled."""
        if os.environ.get("EGREGORE_CODER_WARMUP", "false").lower() not in (
            "1",
            "true",
            "yes",
            "on",
        ):
            return

        try:
            logger.info("Running CoderBackend warmup generation...")
            request = ChatRequest(
                model="coder-ft-v2",
                messages=[ChatMessage(role="user", content="hi")],
                max_tokens=8,
            )
            response = self.chat(request)
            logger.info(
                "Warmup complete (%d tokens generated)",
                response.usage.get("completion_tokens", 0),
            )
        except Exception as exc:
            logger.warning("CoderBackend warmup failed: %s", exc)

    # ------------------------------------------------------------------
    # ILlmClient protocol
    # ------------------------------------------------------------------
    def chat(self, request: ChatRequest) -> ChatResponse:
        """Run chat inference."""
        if not self.health():
            raise RuntimeError(
                "CoderBackend is not healthy. "
                "Check EGREGORE_CODER_MODEL_PATH and GPU availability."
            )

        import torch

        messages = [{"role": m.role, "content": m.content} for m in request.messages]
        prompt = self._tokenizer.apply_chat_template(
            messages, tokenize=False, add_generation_prompt=True
        )

        inputs = self._tokenizer(prompt, return_tensors="pt").to(self._device)

        temperature = 0.0
        do_sample = False
        if request.mode != InferenceMode.DETERMINISTIC:
            temperature = 0.7
            do_sample = True

        generation_kwargs: dict[str, Any] = {
            "max_new_tokens": request.max_tokens,
            "temperature": temperature,
            "do_sample": do_sample,
            "pad_token_id": self._tokenizer.pad_token_id,
            "eos_token_id": self._tokenizer.eos_token_id,
        }

        with torch.no_grad():
            outputs = self._model.generate(**inputs, **generation_kwargs)

        generated = outputs[0][inputs.input_ids.shape[1] :]
        content = self._tokenizer.decode(generated, skip_special_tokens=True)
        prompt_tokens = inputs.input_ids.shape[1]
        completion_tokens = len(generated)

        return ChatResponse(
            message=ChatMessage(role="assistant", content=content),
            model=request.model,
            created_at_ns=time.time_ns(),
            usage={
                "prompt_tokens": prompt_tokens,
                "completion_tokens": completion_tokens,
                "total_tokens": prompt_tokens + completion_tokens,
            },
            finish_reason="stop",
        )

    def generate(self, prompt: str, model: str | None = None) -> str:
        """Single-turn text generation."""
        request = ChatRequest(
            model=model or "my-coder-ft",
            messages=[ChatMessage(role="user", content=prompt)],
            max_tokens=2048,
        )
        return self.chat(request).message.content

    def stream_chat(self, request: ChatRequest) -> Any:
        """Streaming fallback.

        NOTE: True token-by-token streaming is not implemented. The full response
        is returned as a single chunk. Non-streaming clients are unaffected.
        """
        logger.warning(
            "CoderBackend.stream_chat called but true streaming is not implemented; "
            "returning full response as one chunk."
        )
        yield self.chat(request).message.content

    def list_models(self) -> list[dict[str, Any]]:
        """Return the model we host."""
        if not self.health():
            return []
        return [{"name": "coder-ft-v2", "backend": "egregore"}]

    def health(self) -> bool:
        """True if the backend is enabled and the model is loaded."""
        return self._enabled and self._model is not None and self._tokenizer is not None

    def model_exists(self, name: str) -> bool:
        if not self.health():
            return False
        return name in ("coder-ft-v2", "my-coder-ft", "coder-ft", "deepseek-coder-ft")

    def pull_model(self, name: str) -> None:
        raise NotImplementedError("CoderBackend does not support pulling.")

    def delete_model(self, name: str) -> None:
        raise NotImplementedError("CoderBackend does not support deletion.")
