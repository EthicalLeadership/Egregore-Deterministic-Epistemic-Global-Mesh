#!/usr/bin/env python3
"""
Egregor drop‑in replacement for the ollama Python module.
Uses Egregor's existing LocalModelClient (torch + transformers).
"""
import os
import sys
from pathlib import Path

# Ensure the parent of src is in sys.path if needed (should already be)
# But just in case:
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from egregore.infrastructure.local_model_client import LocalModelClient

MODEL_PATH = os.environ.get("EGREGOR_MODEL_PATH", "/opt/egregor/models/command-r")

_model_client = LocalModelClient(model_dir=MODEL_PATH)

def _get_client():
    global _model_client
    if _model_client is None:
        _model_client = LocalModelClient(model_path=MODEL_PATH)
    return _model_client

def generate(model=None, prompt="", max_tokens=128, temperature=0.7, top_p=0.9, stream=False, **kwargs):
    client = _get_client()
    # LocalModelClient.generate only takes prompt and model, but we can pass extra args if needed.
    # For now, just call with prompt.
    response_text = client.generate(prompt=prompt, model=model)
    return {"response": response_text}

def chat(messages, model=None, stream=False, options=None, **kwargs):
    prompt = ""
    for msg in messages:
        role = msg.get("role", "user")
        content = msg.get("content", "")
        prompt += f"{role.capitalize()}: {content}\n"
    prompt += "Assistant:"
    return generate(prompt=prompt, model=model, **kwargs)

def list_models():
    models_dir = Path(MODEL_PATH).parent
    models = []
    if models_dir.exists():
        for p in models_dir.glob("*"):
            if p.is_dir() or p.suffix in [".gguf", ".safetensors", ".bin"]:
                size = p.stat().st_size if p.is_file() else 0
                models.append({"name": p.name, "size": size})
    return {"models": models}

def pull(model_name, **kwargs):
    try:
        from huggingface_hub import snapshot_download
        target = Path(MODEL_PATH).parent / model_name
        target.mkdir(parents=True, exist_ok=True)
        snapshot_download(repo_id=model_name, local_dir=str(target), local_dir_use_symlinks=False)
        return {"status": "success", "model": model_name, "location": str(target)}
    except ImportError:
        return {"error": "huggingface_hub not installed; cannot pull."}

__all__ = ["generate", "chat", "list_models", "pull"]
