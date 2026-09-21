"""Model management — pull, list, delete, verify."""

from __future__ import annotations

import builtins
from typing import Any

from egregore.interface.llm_ports import ILlmClient


class ModelManager:
    """Manage LLM models via Egregore's inference backends."""

    def __init__(self, llm_client: ILlmClient):
        self.client = llm_client

    def list(self) -> builtins.list[dict[str, Any]]:
        """List all locally available models."""
        return self.client.list_models()

    def pull(self, name: str) -> dict[str, Any]:
        """Pull a model from the registry."""
        self.client.pull_model(name)
        return {"status": "pulled", "model": name}

    def delete(self, name: str) -> dict[str, Any]:
        """Delete a local model."""
        self.client.delete_model(name)
        return {"status": "deleted", "model": name}

    def exists(self, name: str) -> bool:
        """Check if model is available locally."""
        return self.client.model_exists(name)

    def recommend_for_hardware(self, vram_mb: int) -> builtins.list[str]:
        """
        Recommend models based on available VRAM.

        Pioneer 1: RTX 3060 12GB
        """
        recommendations = {
            12000: [  # 12GB VRAM
                "llama3.1:70b",  # With CPU offload
                "deepseek-r1:32b",
                "qwen2.5:72b",  # With CPU offload
                "llama3.1:8b",
                "qwen2.5:14b",
            ],
            6000: [  # 6GB VRAM
                "llama3.1:8b",
                "qwen2.5:7b",
                "deepseek-r1:14b",
                "phi4:14b",
            ],
            4000: [  # 4GB VRAM
                "llama3.2:3b",
                "qwen2.5:3b",
                "phi3:3.8b",
            ],
        }
        # Find best match
        for threshold in sorted(recommendations.keys(), reverse=True):
            if vram_mb >= threshold:
                return recommendations[threshold]
        return ["llama3.2:1b"]
