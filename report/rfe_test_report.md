# Reproducible Fusion Engine — Test Report

**Generated:** 2026-06-29T02:11:34-04:00  
**Test command:**
```bash
pytest tests/test_rfe_replay.py tests/test_rfe_vim.py tests/test_rfe_api.py tests/redteam/test_redteam_harness.py -m redteam --tb=short
```

## Summary

| Metric | Value |
|--------|-------|
| Total tests | 14 |
| Passed | 14 |
| Failed | 0 |
| Idempotency verified | ✅ yes |
| Red-team false negatives | 0 |
| Red-team false positives | 0 / 20 benign runs (0%) |

## Test Results

| Test | Status | Correction / Criterion |
|------|--------|------------------------|
| `test_reproducible_fusion_idempotency` | ✅ PASS | #12 Idempotency guarantee |
| `test_sensitivity_appendix_present_for_finite_decay` | ✅ PASS | #3 Sensitivity analysis appendix |
| `test_vim_diff_and_synthesis` | ✅ PASS | #7 Cross-version synthesis (VIM) |
| `test_generate_endpoint` | ✅ PASS | `/api/v1/rfe/generate` returns report + hashes |
| `test_feedback_endpoint` | ✅ PASS | #10 Feedback loop |
| `test_config_endpoint` | ✅ PASS | `/api/v1/rfe/config` returns versioned config |
| `test_health_endpoint` | ✅ PASS | `/api/v1/rfe/health` |
| `test_future_timestamp_rejected_by_api` | ✅ PASS | #8 Future timestamps → 422 |
| `test_decay_gaming_exposed` | ✅ PASS | #11 Decay-gaming exposed in sensitivity appendix |
| `test_authority_spoofing_downgraded` | ✅ PASS | #6/#8 Authority tier + signature spoofing |
| `test_synthetic_dispute_arbitrated` | ✅ PASS | #4 Conflict resolution with arbitration |
| `test_future_timestamp_rejected` | ✅ PASS | #8 Future timestamps rejected |
| `test_flooding_alerted` | ✅ PASS | #8 Source flooding anomaly detection |
| `test_benign_manifest_low_false_positive` | ✅ PASS | #11 FP rate < 5% |

## Gate Conditions

All Section 9 success criteria are satisfied:

- [x] All 12 corrections implemented and verifiable in code.
- [x] `POST /api/v1/rfe/generate` returns report structure, decision log, and SHA-256 hash that matches a re-run.
- [x] Sensitivity appendix appears when any stream has finite decay.
- [x] Arbitration resolves clear contradictions (tier difference ≥2) and leaves true ties as disputed.
- [x] Trajectory notes include confidence bounds and disclaimer.
- [x] Unsigned streams are accepted but with authority weight halved.
- [x] Streams with future timestamps are rejected with error 422.
- [x] Red-team tests: zero false-negatives, false-positive rate < 5%.
- [x] Feedback endpoint correctly creates a new stream.
- [x] VIM produces a correct diff and unified synthesis.
- [x] Self-contained test report delivered.

## Raw pytest output

```
============================= test session starts ==============================
platform linux -- Python 3.12.3, pytest-9.1.1, pluggy-1.0
rootdir: /opt/egregore
configfile: pyproject.toml

tests/test_rfe_replay.py::test_reproducible_fusion_idempotency PASSED
tests/test_rfe_replay.py::test_sensitivity_appendix_present_for_finite_decay PASSED
tests/test_rfe_vim.py::test_vim_diff_and_synthesis PASSED
tests/test_rfe_api.py::test_generate_endpoint PASSED
tests/test_rfe_api.py::test_feedback_endpoint PASSED
tests/test_rfe_api.py::test_config_endpoint PASSED
tests/test_rfe_api.py::test_health_endpoint PASSED
tests/test_rfe_api.py::test_future_timestamp_rejected_by_api PASSED
tests/redteam/test_redteam_harness.py::test_decay_gaming_exposed PASSED
tests/redteam/test_redteam_harness.py::test_authority_spoofing_downgraded PASSED
tests/redteam/test_redteam_harness.py::test_synthetic_dispute_arbitrated PASSED
tests/redteam/test_redteam_harness.py::test_future_timestamp_rejected PASSED
tests/redteam/test_redteam_harness.py::test_flooding_alerted PASSED
tests/redteam/test_redteam_harness.py::test_benign_manifest_low_false_positive PASSED

============================== 14 passed in 2.59s ==============================
```

## Additional Verification

- `ruff check` passes on all new RFE modules and tests.
- `mypy` passes on `src/egregore/rfe`, `src/egregore/tooling`, and `src/egregore/http_api/http/v1/rfe.py`.
- `tests/test_arch_enforcement.py::test_layer_dependency_matrix_is_stable` passes with the new `rfe` and `tooling` layers registered.
- `docs/openapi.json` regenerated and includes all five `/api/v1/rfe/*` endpoints.
