"""EGREGORE MODEL SERVICE (EMS)

Phase 1: Registry + Proxy — replaces Ollama with sovereign infrastructure.

Architecture:
  ┌─────────────┐     ┌──────────────┐     ┌─────────────────┐
  │   Agents    │────▶│  EMS Proxy   │────▶│  llama-server   │
  │  (AutoGen)  │     │   :8000      │     │  (per model)    │
  └─────────────┘     └──────────────┘     └─────────────────┘
                             │
                             ▼
                      ┌──────────────┐
                      │ EMS Registry │
                      │  (SQLite)    │
                      └──────────────┘

The Registry tracks every model version, its GGUF path, which node hosts it,
and its current status (stopped | loading | running | error).

The Proxy exposes a single OpenAI-compatible /v1/chat/completions endpoint.
It reads the model field from the request, queries the Registry to find where
that model is running, and forwards the request. If the model is not loaded,
it can optionally trigger auto-start via the Lifecycle manager.
"""

from __future__ import annotations

__all__ = [
    "EmsRegistry",
    "EmsProxy",
    "ModelRecord",
    "ModelStatus",
    "build_registry_from_env",
    "build_proxy_from_env",
]
