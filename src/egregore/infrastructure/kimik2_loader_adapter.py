import json
import os

import torch
from transformers import AutoModelForCausalLM, AutoTokenizer

from src.egregore.interface.semantics_ports import IKimik2Loader, Kimik2LoaderError


class Kimik2LoaderAdapter(IKimik2Loader):
    def __init__(self, model_dir: str):
        self.model_dir = model_dir
        self._validate_artifacts()

        # CI/tests may provide dummy shard files (empty placeholders). In that case,
        # do not attempt tokenizer/model loading that would require sentencepiece/tiktoken.
        if os.environ.get("KIMIK2_TEST_MODE") == "1" or not self._has_nonempty_shards():
            self.tokenizer = None
            self.model = None
            return

        try:
            self.tokenizer = AutoTokenizer.from_pretrained(model_dir)
            self.model = AutoModelForCausalLM.from_pretrained(
                model_dir,
                device_map="auto",
                torch_dtype=torch.bfloat16,
            )
        except Exception as exc:
            raise Kimik2LoaderError(f"Model/tokenizer load failed: {exc}") from exc

    def _has_nonempty_shards(self) -> bool:
        shards = [f"model-{i + 1}-of-61.safetensors" for i in range(61)]
        for shard in shards:
            shard_path = os.path.join(self.model_dir, shard)
            try:
                if os.path.getsize(shard_path) > 0:
                    return True
            except OSError:
                return False
        return False

    def _validate_artifacts(self):
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
        shards = [f"model-{i + 1}-of-61.safetensors" for i in range(61)]
        for shard in shards:
            if not os.path.isfile(os.path.join(self.model_dir, shard)):
                raise Kimik2LoaderError(f"Missing shard: {shard}")
        for fname in ["config.json", "tokenizer_config.json"]:
            if not os.path.isfile(os.path.join(self.model_dir, fname)):
                raise Kimik2LoaderError(f"Missing {fname}")

    def generate(self, prompt: str, max_tokens: int, temperature: float = 0.0) -> str:
        if temperature != 0.0:
            raise Kimik2LoaderError("Temperature must be 0.0 for determinism.")
        if self.tokenizer is None or self.model is None:
            raise Kimik2LoaderError(
                "Tokenizer/model not loaded (dummy artifacts detected or KIMIK2_TEST_MODE=1)."
            )
        try:
            input_ids = self.tokenizer(prompt, return_tensors="pt").input_ids
            input_ids = input_ids.to(self.model.device)
            with torch.no_grad():
                output = self.model.generate(
                    input_ids,
                    max_new_tokens=max_tokens,
                    do_sample=False,
                    temperature=0.0,
                )
            return self.tokenizer.decode(output[0], skip_special_tokens=True)
        except Exception as exc:
            raise Kimik2LoaderError(f"Inference failed: {exc}") from exc
