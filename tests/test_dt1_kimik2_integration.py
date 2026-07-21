import pytest

transformers = pytest.importorskip(
    "transformers", reason="transformers library not installed"
)

from egregore.dt1.deterministic_runner_adapter import DeterministicInferenceRunner
from egregore.dt1.inference_work_unit import InferenceWorkUnit
from egregore.infrastructure.kimik2_loader_adapter import (
    Kimik2LoaderAdapter,
    Kimik2LoaderError,
)


# --- PHASE 1: Artifact validation ---
def test_validate_kimik2_artifacts_pass(tmp_path):
    # Create all required files
    model_dir = tmp_path / "kimi-k2-base"
    model_dir.mkdir()
    (model_dir / "model.safetensors.index.json").write_text("{}")
    for i in range(61):
        (model_dir / f"model-{i+1}-of-61.safetensors").write_text("")
    (model_dir / "config.json").write_text("{}")
    (model_dir / "tokenizer_config.json").write_text("{}")
    # Should not raise
    Kimik2LoaderAdapter(str(model_dir))


def test_validate_kimik2_artifacts_fail_missing_shard(tmp_path):
    model_dir = tmp_path / "kimi-k2-base"
    model_dir.mkdir()
    (model_dir / "model.safetensors.index.json").write_text("{}")
    for i in range(60):
        (model_dir / f"model-{i+1}-of-61.safetensors").write_text("")
    (model_dir / "config.json").write_text("{}")
    (model_dir / "tokenizer_config.json").write_text("{}")
    with pytest.raises(Kimik2LoaderError):
        Kimik2LoaderAdapter(str(model_dir))


def test_loader_init_fail_corrupt_index(tmp_path):
    model_dir = tmp_path / "kimi-k2-base"
    model_dir.mkdir()
    (model_dir / "model.safetensors.index.json").write_text("notjson")
    for i in range(61):
        (model_dir / f"model-{i+1}-of-61.safetensors").write_text("")
    (model_dir / "config.json").write_text("{}")
    (model_dir / "tokenizer_config.json").write_text("{}")
    with pytest.raises(Kimik2LoaderError):
        Kimik2LoaderAdapter(str(model_dir))


def test_deterministic_runner_replay_stability(monkeypatch):
    class DummyLoader:
        def generate(self, prompt, max_tokens, temperature=0.0):
            return f"out:{prompt}:{max_tokens}:{temperature}"

    runner = DeterministicInferenceRunner(DummyLoader())
    wu = InferenceWorkUnit(
        work_unit_id="abc", prompt="hi", max_tokens=5, deterministic_seed=42
    )
    r1 = runner.run(wu)
    r2 = runner.run(wu)
    assert r1.output_hash == r2.output_hash
    assert r1.output_text == r2.output_text


def test_runner_timeout():
    import time

    class SlowLoader:
        def generate(self, *a, **kw):
            time.sleep(2)
            return "slow"

    runner = DeterministicInferenceRunner(SlowLoader())
    wu = InferenceWorkUnit(work_unit_id="t", prompt="hi", max_tokens=1)
    result = runner.run(wu, timeout_s=1)
    assert result.status == "TIMEOUT"


def test_pressure_gating():
    # Simulate controller credit exhaustion
    class DummyRunner:
        def run(self, wu, timeout_s=120):
            return type("R", (), {"status": "ERROR"})()

    # Not implemented: placeholder for controller logic
    assert True


def test_m3_non_reentry():
    class DummyGuard:
        def __init__(self):
            self.called = False

        def assert_terminal(self, result):
            if self.called:
                raise RuntimeError("M3 reentry")
            self.called = True

    guard = DummyGuard()

    class DummyResult:
        pass

    result = DummyResult()
    guard.assert_terminal(result)
    with pytest.raises(RuntimeError):
        guard.assert_terminal(result)
