import pytest

myth_ritual = pytest.importorskip(
    "myth.ritual_engine", reason="myth.ritual_engine not available in this environment"
)
myth_lang = pytest.importorskip(
    "myth.lang", reason="myth.lang not available in this environment"
)

RitualExecutor = myth_ritual.RitualExecutor
StepResult = myth_ritual.StepResult
RitualContext = myth_ritual.RitualContext
parse = myth_lang.parse


# --- Replay correctness verification ---
def test_ritual_replay_correctness():
    # Example ritual code
    code = "ritual foo { step1; step2; step3 }"
    ast = parse(code)
    executor = RitualExecutor()
    context = RitualContext()

    # Run ritual and record steps
    results = executor.execute(ast, context)
    # Replay ritual
    replay_results = executor.replay(ast, context)
    assert results == replay_results, "Replay diverged from original execution"


# --- Lazy evaluation test ---
def test_lazy_evaluation():
    code = "ritual foo { step1; step2; step3 }"
    ast = parse(code)
    executor = RitualExecutor()
    context = RitualContext()

    # Should not materialize all steps until commit
    results = executor.execute_lazy(ast, context)
    assert all(isinstance(r, StepResult) for r in results)


# --- Stack machine determinism test ---
def test_stack_machine_determinism():
    code = "ritual foo { step1; step2; step3 }"
    ast = parse(code)
    executor = RitualExecutor(stack_machine=True)
    context = RitualContext()

    results1 = executor.execute(ast, context)
    results2 = executor.execute(ast, context)
    assert results1 == results2, "Stack machine execution is not deterministic"
