# Reproducible Fusion Engine — Conflict Arbitration Specification

## Objective
When two evidence streams (A and B) contradict, the RFE must either select one as preferred or declare a genuine dispute. The decision must be deterministic, auditable, and satisfy formal properties.

## Composite Score Function
For stream `i`, the composite score `S_i` is:

`S_i = w_impact * I_i + w_fresh * F_i + w_auth * A_i + w_corroboration * C_i`

Where:
- `I_i` = severity_impact (0–1)
- `F_i` = freshness (1 at time 0, decaying by the stream’s decay function; if foundational, F_i = 1)
- `A_i` = authority weight from source tier table (0.3–1.0)
- `C_i` = corroboration score (number of independent streams supporting similar claims, normalized to 0–1)

Weights are configurable but versioned.

## Arbitration Rule
Let Δ = |S_A - S_B|.
- If Δ > `arbitration_threshold` (default 0.15): the stream with higher S wins. The loser is placed in “Overruled Evidence” with computed scores and reason.
- If Δ ≤ `dead_band` (default 0.05): genuine dispute. Both streams appear in “Disputed Findings” with a warning. No automated decision.
- If `dead_band` < Δ ≤ `arbitration_threshold`: conflict is escalated to a “Needs Human Review” queue, and both streams are placed in “Disputed” with a recommendation for the higher-scored stream.

## Formal Properties
1. **Monotonicity:** Adding a new stream that supports A (does not contradict B) cannot cause A’s selection to flip to B. Proof: C_A increases, other scores unchanged, so S_A ≥ previous, Δ unchanged or widened.
2. **Consistency:** Identical conflict scenarios produce identical resolution.
3. **Tie‑breaking determinism:** If S_A = S_B exactly, the stream with higher authority tier wins; if tier equal, higher freshness; if still equal, lexicographic order of `stream_id`.

## Edge Cases
- If confidence is undefined, it is treated as 0.5 before capping.
- If decay method is “unbounded”, F_i = 1 permanently.
- If a stream lacks a `relevance_tag`, it is placed in a “Misc” section with minimal weight.

## Implementation
See `src/egregore/rfe/arbitration.py`. The function `arbitrate(stream_a, stream_b, config)` returns a resolution dict.

## Test Vectors
Provided in `tests/test_arbitration.py` and included in the red-team harness.
