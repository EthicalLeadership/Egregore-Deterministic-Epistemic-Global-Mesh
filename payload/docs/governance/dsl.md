# Governance DSL

The governance DSL is a deterministic **expression-tree** language for
writing governance rules as data (JSON or YAML) instead of code. It is
evaluated by `src/egregore/domain/governance_dsl.py` (pure: no I/O, no
wall-clock) and loaded by
`src/egregore/application/governance_policy_loader.py`, which produces
`GovernanceRuleSet` objects implementing `IPolicyLogic` — so rule sets
plug directly into `VersionedPolicyExecutor` /
`IPolicyVersionRegistry` with version pinning, `applied_rules`, and
`decision_trace_hash`.

## Expression grammar

```
expr  := {"all": [expr, ...]}        # conjunction (non-empty)
       | {"any": [expr, ...]}        # disjunction (non-empty)
       | {"not": expr}               # negation
       | {"field": "<dotted.path>", <op>: <literal>}

op    := eq | ne | gt | ge | lt | le | in | contains | matches
```

- `field` is a dotted path into the evaluation context (`"qc.confidence"`
  resolves `context["qc"]["confidence"]`).
- Literals: strings, numbers, booleans, null, or lists of literals.
- `in` — field value is a member of the list literal.
- `contains` — list/string field contains the literal.
- `matches` — regex `fullmatch` of the pattern against a string field.

## Fail-closed rules

1. **Parse**: unknown operator, malformed node, or non-literal value →
   `GovernanceDslError`; the rule set is rejected and nothing is
   registered.
2. **Evaluate**: missing context field or strict type mismatch →
   `GovernanceDslError`. Comparisons never coerce: numbers with numbers,
   strings with strings, bools only with bools.
3. **Verdicts**: all rules are evaluated; **deny wins**, then
   `require_escalation`, then `allow`. **No matching rule → deny.**

## Rule-set file format

```yaml
version: "1.0.0"
rules:
  - id: "qc-confidence-floor"
    when:
      all:
        - {field: "qc.confidence", ge: 0.85}
        - {field: "tenant", ne: "restricted"}
    then:
      verdict: allow            # allow | deny | require_escalation
      reason: "QC floor satisfied"
```

See `config/governance_rules.example.json` for a worked example.

## Loading

```python
from egregore.application.governance_policy_loader import (
    load_rule_set, register_rule_set,
)
from egregore.application.policy_versioning import (
    InMemoryPolicyVersionRegistry, VersionedPolicyExecutor,
)

rule_set = load_rule_set("config/governance_rules.example.json")
rule_set.policy_hash            # SHA-256 of the canonical source document

registry = InMemoryPolicyVersionRegistry()
register_rule_set(registry, rule_set)

executor = VersionedPolicyExecutor(registry=registry)
result = executor.execute(
    command={"tenant": "acme", "qc": {"confidence": 0.9}},
    engine_version="1.0.0",
    policy_version=rule_set.version,
)
result.policy_result["verdict"]          # "allow" | "deny" | "require_escalation"
result.policy_result["matched_rule_ids"] # tuple of matched rule ids
```

## Determinism guarantees

- `evaluate()` is pure: same AST + same context → same boolean, always.
- `compute()` returns the same mapping for the same command.
- `policy_hash` is stable across processes for the same source document
  (canonical JSON: sorted keys, no whitespace, non-finite floats rejected).
