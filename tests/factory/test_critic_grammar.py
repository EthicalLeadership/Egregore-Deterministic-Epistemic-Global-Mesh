"""Tests for the critic verdict grammar, repair tier, and citation gating."""

from __future__ import annotations

from typing import Any

import pytest

from egregore.factory.critic_grammar import VERDICT_GBNF
from egregore.factory.qc_gate import EgregoreCritic, run_deterministic_checks


# ---------------------------------------------------------------------------
# Grammar: compilation + constrained generation
# ---------------------------------------------------------------------------
def test_grammar_compiles():
    from llama_cpp import LlamaGrammar

    g = LlamaGrammar.from_string(VERDICT_GBNF, verbose=False)
    assert g is not None


def test_grammar_constrains_generation():
    """With the grammar, a model can ONLY emit the verdict schema."""
    from llama_cpp import Llama, LlamaGrammar

    llm = Llama(
        model_path="/mnt/blackstar/vol-hdd-a/models/gguf/general/qwen2.5-1.5b-instruct-q4_k_m.gguf",
        n_ctx=1024, n_gpu_layers=0, verbose=False,
    )
    grammar = LlamaGrammar.from_string(VERDICT_GBNF, verbose=False)
    import json

    for _ in range(3):
        out = llm.create_chat_completion(
            messages=[{"role": "user", "content": "Judge this output: 'print(1)'. Verdict JSON."}],
            max_tokens=128, temperature=0.0, seed=42, grammar=grammar,
        )
        text = out["choices"][0]["message"]["content"]
        parsed = json.loads(text)  # MUST parse — grammar guarantees schema
        assert parsed["verdict"] in ("PASS", "FAIL")
        assert isinstance(parsed["confidence"], (int, float))
        assert isinstance(parsed["violations"], list)
    del llm


def test_grammar_reaches_backend(gguf_backend):
    """ChatRequest.grammar is forwarded to llama.cpp."""
    from egregore.domain.inference_models import ChatMessage, ChatRequest

    req = ChatRequest(
        model="qwen-1.5b",
        messages=[ChatMessage(role="user", content="hi")],
        max_tokens=8,
        grammar=VERDICT_GBNF,
    )
    gguf_backend.chat(req)
    llm = gguf_backend._instances["qwen-1.5b"]
    assert llm.last_kwargs.get("grammar") is not None


@pytest.fixture
def gguf_backend(monkeypatch: pytest.MonkeyPatch):
    from egregore.infrastructure.gguf_backend import GgufBackend

    class FakeLlama:
        def __init__(self) -> None:
            self.last_kwargs: dict[str, Any] = {}

        def create_chat_completion(self, **kwargs: Any) -> dict[str, Any]:
            self.last_kwargs = kwargs
            return {
                "choices": [{"message": {"role": "assistant", "content": "x"}, "finish_reason": "stop"}],
                "usage": {"prompt_tokens": 1, "completion_tokens": 1},
            }

    backend = GgufBackend(models={"qwen-1.5b": "/fake.gguf"})
    monkeypatch.setattr(backend, "_load", lambda model: FakeLlama())
    return backend


# ---------------------------------------------------------------------------
# Repair tier
# ---------------------------------------------------------------------------
class _Host:
    def __init__(self, reply: str):
        self.reply = reply

    def execute(self, **kwargs: Any):
        return self.reply, 10, "stub"


def _critic(reply: str) -> EgregoreCritic:
    return EgregoreCritic(_Host(reply), model_id="qwen_1.5b", confidence_threshold=0.6)


def test_repair_prose_around_json():
    v = _critic('Here is my verdict: {"verdict": "PASS", "confidence": 0.9, "violations": []} hope this helps').critique(
        output="x", constraints=[], max_tokens=64, timeout_ms=5000,
    )
    assert v.verdict == "PASS"


def test_repair_trailing_commas():
    v = _critic('{"verdict": "FAIL", "confidence": 0.8, "violations": [{"constraint_id": "x", "evidence": "y", "severity": "hard",}],}').critique(
        output="x", constraints=[], max_tokens=64, timeout_ms=5000,
    )
    assert v.verdict == "FAIL"
    assert v.violations[0].constraint_id == "x"


def test_repair_single_quotes():
    v = _critic("{'verdict': 'PASS', 'confidence': 0.95, 'violations': []}").critique(
        output="x", constraints=[], max_tokens=64, timeout_ms=5000,
    )
    assert v.verdict == "PASS"


def test_repair_failure_still_malformed():
    v = _critic("the output seems fine to me honestly").critique(
        output="x", constraints=[], max_tokens=64, timeout_ms=5000,
    )
    assert v.verdict == "FAIL"
    assert v.violations[0].constraint_id == "malformed_verdict"


# ---------------------------------------------------------------------------
# Citation-presence gating
# ---------------------------------------------------------------------------
POLICY = {"max_output_chars": 5000, "forbidden_patterns": [], "required_output_fields": []}


def test_citation_missing_fails():
    policy = {**POLICY, "required_evidence_ids": ["00ac30ae6debf438", "031d215ad7f03c64"], "min_citations": 2}
    v = run_deterministic_checks("This narrative mentions nothing.", policy=policy)
    assert any(x.constraint_id == "citation_missing" for x in v)


def test_citation_partial_coverage_fails():
    policy = {**POLICY, "required_evidence_ids": ["00ac30ae6debf438", "031d215ad7f03c64"], "min_citations": 2}
    v = run_deterministic_checks("Finding 00ac30ae6debf438 was key.", policy=policy)
    assert any(x.constraint_id == "citation_missing" for x in v)


def test_citation_sufficient_passes():
    policy = {**POLICY, "required_evidence_ids": ["00ac30ae6debf438", "031d215ad7f03c64"], "min_citations": 2}
    v = run_deterministic_checks("Findings 00ac30ae6debf438 and 031d215ad7f03c64 were key.", policy=policy)
    assert not any(x.constraint_id == "citation_missing" for x in v)


def test_citation_not_required_when_no_ids():
    v = run_deterministic_checks("Anything.", policy=POLICY)
    assert not any(x.constraint_id == "citation_missing" for x in v)
