"""Governance rule-set loader — fail-closed, version-pinned, hash-audited.

Loads governance rule files (JSON or YAML) into ``GovernanceRuleSet``
objects implementing ``IPolicyLogic``
(``application/policy_versioning.py``), so they plug directly into
``VersionedPolicyExecutor`` / ``IPolicyVersionRegistry``.

File format::

    version: "1.0.0"                 # required, unique per registry
    rules:
      - id: "qc-confidence-floor"    # required, unique within the file
        when:                        # governance DSL expression (required)
          all:
            - {field: "qc.confidence", ge: 0.85}
            - {field: "tenant", ne: "restricted"}
        then:
          verdict: allow             # allow | deny | require_escalation
          reason: "QC floor satisfied"

Evaluation semantics:
- every rule's ``when`` is evaluated; matching rules collect their verdicts;
- **deny wins**, then require_escalation, then allow;
- **no matching rule → deny** (fail-closed default);
- malformed file, duplicate rule id, or unknown verdict → loader raises
  ``GovernancePolicyError`` and nothing is registered (fail-closed).
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from egregore.domain.governance_dsl import GovernanceDslError, evaluate, parse_expr
from egregore.shared.canonical import canonical_dumps, sha256_hex

VERDICTS = frozenset({"allow", "deny", "require_escalation"})


class GovernancePolicyError(Exception):
    """Fail-closed loader error."""


@dataclass(frozen=True)
class GovernanceRule:
    rule_id: str
    when: Any  # compiled governance DSL expression
    verdict: str
    reason: str


@dataclass(frozen=True)
class GovernanceRuleSet:
    """Versioned governance policy. Implements ``IPolicyLogic``."""

    version: str
    rules: tuple[GovernanceRule, ...]
    policy_hash: str

    def validate(self, command: Any) -> None:
        if not isinstance(command, Mapping):
            raise ValueError(
                f"Governance command must be a mapping, got {type(command).__name__}"
            )

    def compute(self, command: Any) -> Mapping[str, Any]:
        """Evaluate all rules against the command context (pure)."""
        self.validate(command)
        matched: list[GovernanceRule] = []
        for rule in self.rules:
            if evaluate(rule.when, command):
                matched.append(rule)

        matched_ids = tuple(rule.rule_id for rule in matched)
        if not matched:
            return {
                "verdict": "deny",
                "matched_rule_ids": (),
                "reason": "no matching rule (fail-closed default)",
            }

        # deny wins, then require_escalation, then allow
        for verdict in ("deny", "require_escalation", "allow"):
            winners = [rule for rule in matched if rule.verdict == verdict]
            if winners:
                return {
                    "verdict": verdict,
                    "matched_rule_ids": matched_ids,
                    "reason": winners[0].reason,
                }
        raise GovernancePolicyError("unreachable: verdict resolution failed")


def parse_rule_set(document: Any) -> GovernanceRuleSet:
    """Parse and validate a rule-set document (already-decoded mapping)."""
    if not isinstance(document, Mapping):
        raise GovernancePolicyError("Rule-set document must be a mapping")

    version = document.get("version")
    if not isinstance(version, str) or not version:
        raise GovernancePolicyError("Rule-set requires a non-empty 'version' string")

    raw_rules = document.get("rules")
    if not isinstance(raw_rules, list) or not raw_rules:
        raise GovernancePolicyError("Rule-set requires a non-empty 'rules' list")

    rules: list[GovernanceRule] = []
    seen_ids: set[str] = set()
    for index, raw in enumerate(raw_rules):
        if not isinstance(raw, Mapping):
            raise GovernancePolicyError(f"Rule #{index} must be a mapping")
        rule_id = raw.get("id")
        if not isinstance(rule_id, str) or not rule_id:
            raise GovernancePolicyError(f"Rule #{index} requires a non-empty 'id'")
        if rule_id in seen_ids:
            raise GovernancePolicyError(f"Duplicate rule id: {rule_id!r}")
        seen_ids.add(rule_id)

        if "when" not in raw:
            raise GovernancePolicyError(f"Rule {rule_id!r} requires a 'when' expression")
        try:
            when = parse_expr(raw["when"])
        except GovernanceDslError as exc:
            raise GovernancePolicyError(
                f"Rule {rule_id!r} has an invalid 'when' expression: {exc}"
            ) from exc

        then = raw.get("then")
        if not isinstance(then, Mapping):
            raise GovernancePolicyError(f"Rule {rule_id!r} requires a 'then' mapping")
        verdict = then.get("verdict")
        if verdict not in VERDICTS:
            raise GovernancePolicyError(
                f"Rule {rule_id!r} has invalid verdict {verdict!r}; "
                f"must be one of {sorted(VERDICTS)}"
            )
        reason = then.get("reason", "")
        if not isinstance(reason, str):
            raise GovernancePolicyError(f"Rule {rule_id!r} reason must be a string")

        rules.append(
            GovernanceRule(rule_id=rule_id, when=when, verdict=verdict, reason=reason)
        )

    policy_hash = sha256_hex(canonical_dumps(document, default=str).encode("utf-8"))
    return GovernanceRuleSet(version=version, rules=tuple(rules), policy_hash=policy_hash)


def load_rule_set(path: str | Path) -> GovernanceRuleSet:
    """Load a rule set from a JSON or YAML file. Fail-closed."""
    path = Path(path)
    if not path.exists():
        raise GovernancePolicyError(f"Rule-set file not found: {path}")
    text = path.read_text(encoding="utf-8")

    suffix = path.suffix.lower()
    try:
        if suffix == ".json":
            from egregore.shared.canonical import canonical_loads

            document = canonical_loads(text)
        elif suffix in (".yaml", ".yml"):
            import yaml

            document = yaml.safe_load(text)
        else:
            raise GovernancePolicyError(
                f"Unsupported rule-set extension {suffix!r}; use .json/.yaml/.yml"
            )
    except GovernancePolicyError:
        raise
    except Exception as exc:
        raise GovernancePolicyError(f"Cannot parse rule-set file {path}: {exc}") from exc

    return parse_rule_set(document)


def register_rule_set(registry: Any, rule_set: GovernanceRuleSet) -> None:
    """Register a rule set into an ``IPolicyVersionRegistry`` and pin it current.

    The registry's ``lookup(version)`` then returns the rule set as
    ``IPolicyLogic``; ``VersionedPolicyExecutor`` supplies version pinning,
    ``applied_rules``, and ``decision_trace_hash``.
    """
    registry.register(rule_set.version, rule_set)
    registry.set_current(rule_set.version)
