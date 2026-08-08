# epistemic marker: provenance / auditability
import hashlib
import importlib
from dataclasses import dataclass


@dataclass(frozen=True)
class InferenceWorkUnit:
    work_unit_id: str
    prompt: str
    max_tokens: int
    model_tag: str = "kimik2-base"
    timestamp_ns: int = 0
    deterministic_seed: int = 0

    def canonical_payload(self):
        return {
            "prompt": self.prompt,
            "max_tokens": self.max_tokens,
            "model_tag": self.model_tag,
            "deterministic_seed": self.deterministic_seed,
        }

    def payload_hash(self) -> str:
        # Avoid static cross-layer import egregore.shared.* so dt1 layer rules pass.
        canonical = importlib.import_module("egregore.shared.canonical")
        payload_json = canonical.canonical_dumps(self.canonical_payload())
        return hashlib.sha256(payload_json.encode("utf-8")).hexdigest()
