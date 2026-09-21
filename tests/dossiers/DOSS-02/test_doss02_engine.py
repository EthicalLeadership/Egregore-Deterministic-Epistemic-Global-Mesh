"""Tests for DOSS-02: Cognitive Core Engine."""

from __future__ import annotations

import pytest

from egregore.dossiers.DOSS_02_cognitive_core.engine import (
    ChatRequest,
    CognitiveCoreEngine,
)


class MockBackend:
    def __init__(self, name: str):
        self.name = name
        self._healthy = True

    def infer(self, prompt: str) -> str:
        return f"[{self.name}] {prompt}"

    def health(self) -> bool:
        return self._healthy


def test_engine_routes_claude_to_anthropic():
    backends = {
        "anthropic": MockBackend("anthropic"),
        "deepseek": MockBackend("deepseek"),
        "local": MockBackend("local"),
    }
    engine = CognitiveCoreEngine(backends=backends)

    req = ChatRequest(
        model="claude-3-opus", messages=[{"role": "user", "content": "hello"}]
    )
    resp = engine.execute(req)
    assert "anthropic" in resp.content


def test_engine_routes_deepseek_to_deepseek():
    backends = {
        "anthropic": MockBackend("anthropic"),
        "deepseek": MockBackend("deepseek"),
        "local": MockBackend("local"),
    }
    engine = CognitiveCoreEngine(backends=backends)

    req = ChatRequest(
        model="deepseek-coder", messages=[{"role": "user", "content": "code"}]
    )
    resp = engine.execute(req)
    assert "deepseek" in resp.content


def test_engine_routes_qwen_to_local():
    backends = {
        "local": MockBackend("local"),
    }
    engine = CognitiveCoreEngine(backends=backends)

    req = ChatRequest(
        model="qwen-2.5-7b", messages=[{"role": "user", "content": "test"}]
    )
    resp = engine.execute(req)
    assert "local" in resp.content


def test_engine_tracks_tokens_used():
    backends = {
        "local": MockBackend("local"),
    }
    engine = CognitiveCoreEngine(backends=backends)

    req = ChatRequest(
        model="llama-3-8b", messages=[{"role": "user", "content": "hello world"}]
    )
    resp = engine.execute(req)
    # tokens ≈ len(prompt)/4 + len(result)/4 = 11/4 + ~20/4 ≈ 3 + 5 = 8
    assert resp.tokens_used > 0


def test_engine_empty_messages():
    backends = {
        "local": MockBackend("local"),
    }
    engine = CognitiveCoreEngine(backends=backends)

    req = ChatRequest(model="llama-3-8b", messages=[])
    resp = engine.execute(req)
    assert resp.content == "[local] "
    assert resp.tokens_used > 0


def test_engine_health_checks_all_backends():
    backends = {
        "anthropic": MockBackend("anthropic"),
        "deepseek": MockBackend("deepseek"),
        "local": MockBackend("local"),
    }
    backends["deepseek"]._healthy = False
    engine = CognitiveCoreEngine(backends=backends)

    health = engine.health()
    assert health["anthropic"] is True
    assert health["deepseek"] is False
    assert health["local"] is True


def test_engine_raises_on_missing_backend():
    engine = CognitiveCoreEngine(backends={})
    req = ChatRequest(
        model="unknown-model", messages=[{"role": "user", "content": "test"}]
    )
    with pytest.raises(ValueError, match="No backend available"):
        engine.execute(req)
