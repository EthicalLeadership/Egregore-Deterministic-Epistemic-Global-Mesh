#!/usr/bin/env python3
"""
Egregor drop‑in replacement for the ollama Python module.
Uses Egregor's existing LocalModelClient (torch + transformers).
"""
import os
import json
from pathlib import Path

# Import Egregor's model client
from egregore.infrastructure.local_model_client import LocalModelClient

# Configuration – read model path from env or use default
MODEL_PATH = os.environ.get("EGREGOR_MODEL_PATH", "/opt/egregor/models/command-r")

# Lazy singleton – load model only when first used
_model_client = None

def _get_client():
    global _model_client
    if _model_client is None:
        # Instantiate the Egregor model client (adjust if __init__ needs more args)
        _model_client = LocalModelClient(model_path=MODEL_PATH)
    return _model_client

def generate(model=None, prompt="", max_tokens=128, temperature=0.7, top_p=0.9, stream=False, **kwargs):
    """
    Mimic ollama.generate() – returns a dict with 'response' key.
    """
    client = _get_client()
    # The client's generate returns a string directly
    response_text = client.generate(prompt=prompt, model=model)  # model might be ignored
    # If you need to pass max_tokens/temperature, you might need to adapt – check client signature.
    # For now, we return the response.
    return {"response": response_text}

def chat(messages, model=None, stream=False, options=None, **kwargs):
    """
    Mimic ollama.chat() – converts messages to a prompt.
    """
    # Build a prompt from messages (simple format)
    prompt = ""
    for msg in messages:
        role = msg.get("role", "user")
        content = msg.get("content", "")
        prompt += f"{role.capitalize()}: {content}\n"
    prompt += "Assistant:"
    return generate(prompt=prompt, model=model, **kwargs)

def list_models():
    """
    Mimic ollama.list() – lists models in the model directory.
    """
    models_dir = Path(MODEL_PATH).parent
    models = []
    if models_dir.exists():
        for p in models_dir.glob("*"):
            if p.is_dir() or p.suffix in [".gguf", ".safetensors", ".bin"]:
                size = p.stat().st_size if p.is_file() else 0
                models.append({"name": p.name, "size": size})
    return {"models": models}

def pull(model_name, **kwargs):
    """
    Mimic ollama.pull() – downloads a model from Hugging Face.
    You may skip this if you already have the model.
    """
    try:
        from huggingface_hub import snapshot_download
        target = Path(MODEL_PATH).parent / model_name
        target.mkdir(parents=True, exist_ok=True)
        snapshot_download(repo_id=model_name, local_dir=str(target), local_dir_use_symlinks=False)
        return {"status": "success", "model": model_name, "location": str(target)}
    except ImportError:
        return {"error": "huggingface_hub not installed; cannot pull."}

# Expose functions at module level (so `from ollama import generate` works)
__all__ = ["generate", "chat", "list_models", "pull"]
