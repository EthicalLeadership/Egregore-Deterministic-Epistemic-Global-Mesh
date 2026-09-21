import timeit

import pytest

myth_lang = pytest.importorskip(
    "myth.lang", reason="myth.lang not available in this environment"
)
parse = myth_lang.parse


# --- Benchmark parse time on 1MB input ---
def test_parse_benchmark() -> None:
    # Generate a large, valid ritual input (1MB)
    step = "step; "
    n_steps = 1024 * 1024 // len(step)
    code = "ritual foo { " + step * n_steps + "}"
    # Only benchmark parse, not I/O
    duration = timeit.timeit(lambda: parse(code), number=1)
    print(f"Parse time for 1MB input: {duration:.3f}s")
    # Target: <2s for 1MB input (adjust as needed)
    assert duration < 2.0
