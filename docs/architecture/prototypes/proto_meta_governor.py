#!/usr/bin/env python3
"""
MetaGovernorService — Standalone Study Prototype
Based on: docs/architecture/meta_governor_service_design.md

Run: python docs/architecture/prototypes/proto_meta_governor.py
"""

from __future__ import annotations

import hashlib
import json
import os
import time
from dataclasses import dataclass, field, asdict
from enum import Enum
from typing import Any, Mapping, Optional, Protocol

try:
    from nacl.signing import SigningKey, VerifyKey
    HAS_NACL = True
except Exception:
    HAS_NACL = False


class Verdict(str, Enum):
    ALLOWED = "allowed"
    VETOED = "vetoed"
    REQUIRES_QUORUM = "requires_quorum"
    REQUIRES_OVERRIDE = "requires_override"


class EscalationLevel(str, Enum):
    NORMAL = "normal"
    ELEVATED = "elevated"
    CRITICAL = "critical"
    OVERRIDE = "override"


class VoteStatus(str, Enum):
    PENDING = "pending"
    VERIFIED = "verified"
    REJECTED = "rejected"


@dataclass(frozen=True)
class Actor:
    actor_id: str
    display_name: str
    public_key_hex: str
    weight: float = 1.0
    chamber: str = "unassigned"
    actor_type: str = "unknown"


@dataclass(frozen=True)
class Action:
    action_id: str
    action_type: str
    payload: Mapping[str, Any]
    proposed_by_actor_id: str
    timestamp_ns: int
    escalation_level: EscalationLevel = EscalationLevel.NORMAL

    def to_canonical_bytes(self) -> bytes:
        d = asdict(self)
        d["escalation_level"] = self.escalation_level.value
        d["payload"] = dict(self.payload)
        return json.dumps(d, sort_keys=True, ensure_ascii=False).encode("utf-8")


@dataclass(frozen=True)
class Vote:
    actor_id: str
    action_id: str
    approve: bool
    timestamp_ns: int
    signature_hex: str
    status: VoteStatus = VoteStatus.PENDING

    def payload_bytes(self, action: Action) -> bytes:
        d = {
            "action": action.action_id,
            "action_hash": hashlib.sha256(action.to_canonical_bytes()).hexdigest(),
            "approve": self.approve,
            "timestamp_ns": self.timestamp_ns,
        }
        return json.dumps(d, sort_keys=True).encode("utf-8")


@dataclass
class QuorumSession:
    action_id: str
    opened_at_ns: int
    threshold: float
    chamber_requirement: Optional[Mapping[str, int]] = None
    votes: list[Vote] = field(default_factory=list)
    closed_at_ns: Optional[int] = None
    final_verdict: Optional[Verdict] = None

    def approving_weight(self, actors: Mapping[str, Actor]) -> float:
        seen = set()
        total = 0.0
        for vote in self.votes:
            if vote.actor_id in seen or vote.status != VoteStatus.VERIFIED:
                continue
            seen.add(vote.actor_id)
            actor = actors.get(vote.actor_id)
            if actor and vote.approve:
                total += actor.weight
        return total

    def total_possible_weight(self, actors: Mapping[str, Actor]) -> float:
        return sum(a.weight for a in actors.values())

    def chamber_counts(self, actors: Mapping[str, Actor]) -> Mapping[str, int]:
        counts: dict[str, int] = {}
        seen = set()
        for vote in self.votes:
            if vote.actor_id in seen or vote.status != VoteStatus.VERIFIED or not vote.approve:
                continue
            seen.add(vote.actor_id)
            actor = actors.get(vote.actor_id)
            if actor:
                counts[actor.chamber] = counts.get(actor.chamber, 0) + 1
        return counts


@dataclass(frozen=True)
class Constitution:
    quorum_threshold: float = 0.67
    override_threshold: float = 0.90
    veto_rules: list[Mapping[str, Any]] = field(default_factory=list)

    def evaluate(self, action: Action) -> Verdict:
        for rule in self.veto_rules:
            if any(p in action.action_type for p in rule.get("action_type_patterns", [])):
                return Verdict.VETOED
        if action.escalation_level == EscalationLevel.OVERRIDE:
            return Verdict.REQUIRES_OVERRIDE
        if action.escalation_level in (EscalationLevel.CRITICAL, EscalationLevel.ELEVATED):
            return Verdict.REQUIRES_QUORUM
        return Verdict.ALLOWED


class ActorRegistryPort(Protocol):
    def get_actors(self) -> Mapping[str, Actor]: ...


class ZarcJournalPort(Protocol):
    def append(self, event_type: str, payload: Mapping[str, object]) -> str: ...


class EscalationServicePort(Protocol):
    def freeze(self, reason: str, action_id: str) -> None: ...
    def unfreeze(self, reason: str, action_id: str) -> None: ...


class InMemoryActorRegistry:
    def __init__(self, actors: Mapping[str, Actor]):
        self._actors = dict(actors)

    def get_actors(self) -> Mapping[str, Actor]:
        return self._actors


class InMemoryZarcJournal:
    def __init__(self):
        self.events: list[dict[str, Any]] = []

    def append(self, event_type: str, payload: Mapping[str, object]) -> str:
        event = {"event_type": event_type, "payload": dict(payload)}
        self.events.append(event)
        return hashlib.sha256(json.dumps(event, sort_keys=True).encode()).hexdigest()


class InMemoryEscalationService:
    def __init__(self):
        self.log: list[dict[str, Any]] = []
        self.frozen = False

    def freeze(self, reason: str, action_id: str) -> None:
        self.frozen = True
        self.log.append({"action": "freeze", "reason": reason, "action_id": action_id})
        print(f"[ESCALATION] FREEZE: {reason} ({action_id})")

    def unfreeze(self, reason: str, action_id: str) -> None:
        self.frozen = False
        self.log.append({"action": "unfreeze", "reason": reason, "action_id": action_id})
        print(f"[ESCALATION] UNFREEZE: {reason} ({action_id})")


class MetaGovernorService:
    def __init__(
        self,
        constitution: Constitution,
        actor_registry: ActorRegistryPort,
        zarc_journal: ZarcJournalPort,
        escalation_service: EscalationServicePort,
    ):
        self.constitution = constitution
        self.actor_registry = actor_registry
        self.zarc_journal = zarc_journal
        self.escalation_service = escalation_service
        self._sessions: dict[str, QuorumSession] = {}
        self._action_cache: dict[str, Action] = {}
        self._frozen = False

    def evaluate(self, action: Action) -> Verdict:
        self._action_cache[action.action_id] = action
        verdict = self.constitution.evaluate(action)
        self.zarc_journal.append(
            "meta_governor_action_evaluated",
            {"action_id": action.action_id, "action_type": action.action_type, "verdict": verdict.value, "timestamp_ns": time.time_ns()},
        )
        print(f"[META] {action.action_id} evaluated: {verdict.value}")
        if verdict == Verdict.VETOED:
            self._freeze("Constitutional veto", action.action_id)
        return verdict

    def open_quorum(self, action: Action) -> QuorumSession:
        self._action_cache[action.action_id] = action
        threshold = (
            self.constitution.override_threshold
            if action.escalation_level == EscalationLevel.OVERRIDE
            else self.constitution.quorum_threshold
        )
        session = QuorumSession(
            action_id=action.action_id,
            opened_at_ns=time.time_ns(),
            threshold=threshold,
            chamber_requirement={"university": 1, "guild": 1, "investigation": 1},
        )
        self._sessions[action.action_id] = session
        print(f"[META] Quorum opened for {action.action_id} (threshold={threshold})")
        return session

    def cast_vote(self, vote: Vote) -> bool:
        actors = self.actor_registry.get_actors()
        actor = actors.get(vote.actor_id)
        if actor is None:
            return False
        session = self._sessions.get(vote.action_id)
        if session is None or session.closed_at_ns is not None:
            return False
        if any(v.actor_id == vote.actor_id for v in session.votes):
            return False
        action = self._action_cache.get(vote.action_id)
        if action is None:
            return False
        if not self._verify_signature(vote, actor, action):
            return False
        object.__setattr__(vote, "status", VoteStatus.VERIFIED)
        session.votes.append(vote)
        print(f"[META] VERIFIED vote from {actor.display_name}: {'APPROVE' if vote.approve else 'REJECT'}")
        return True

    def close_quorum(self, action_id: str) -> Optional[Verdict]:
        session = self._sessions.get(action_id)
        if session is None or session.closed_at_ns is not None:
            return None
        actors = self.actor_registry.get_actors()
        session.closed_at_ns = time.time_ns()
        total = session.approving_weight(actors)
        possible = session.total_possible_weight(actors)
        ratio = total / possible if possible > 0 else 0.0
        chamber_ok = True
        if session.chamber_requirement:
            counts = session.chamber_counts(actors)
            for chamber, minimum in session.chamber_requirement.items():
                if counts.get(chamber, 0) < minimum:
                    chamber_ok = False
        if ratio >= session.threshold and chamber_ok:
            session.final_verdict = Verdict.ALLOWED
            self._unfreeze("Quorum authorized", action_id)
        else:
            session.final_verdict = Verdict.VETOED
            self._freeze("Quorum failed", action_id)
        print(f"[META] Quorum closed: ratio={ratio:.2%}, chambers_ok={chamber_ok}, verdict={session.final_verdict.value}")
        return session.final_verdict

    def _verify_signature(self, vote: Vote, actor: Actor, action: Action) -> bool:
        if HAS_NACL:
            try:
                vk = VerifyKey(bytes.fromhex(actor.public_key_hex))
                vk.verify(vote.payload_bytes(action), bytes.fromhex(vote.signature_hex))
                return True
            except Exception:
                return False
        payload = f"{vote.actor_id}:{vote.action_id}:{vote.approve}:{vote.timestamp_ns}"
        expected = hashlib.sha256((actor.public_key_hex + payload).encode()).hexdigest()[:64]
        return vote.signature_hex == expected

    def _freeze(self, reason: str, action_id: str) -> None:
        self._frozen = True
        self.escalation_service.freeze(reason, action_id)

    def _unfreeze(self, reason: str, action_id: str) -> None:
        self._frozen = False
        self.escalation_service.unfreeze(reason, action_id)

    @property
    def is_frozen(self) -> bool:
        return self._frozen


class KeyPair:
    def __init__(self):
        if HAS_NACL:
            self._sk = SigningKey.generate()
            self.public_key_hex = self._sk.verify_key.encode().hex()
        else:
            self.public_key_hex = hashlib.sha256(os.urandom(32)).hexdigest()[:64]

    def sign(self, message: bytes) -> str:
        if HAS_NACL:
            return self._sk.sign(message).signature.hex()
        return hashlib.sha256(self.public_key_hex.encode() + message).hexdigest()[:64]


def demo():
    print("=" * 60)
    print("MetaGovernorService Prototype")
    print(f"Crypto backend: {'PyNaCl Ed25519' if HAS_NACL else 'deterministic hash stub'}")
    print("=" * 60)
    print()

    constitution = Constitution(
        veto_rules=[
            {"action_type_patterns": ["memory_erase", "cognitive_override"]},
            {"action_type_patterns": ["self_destruct", "federation_disconnect"]},
        ],
        quorum_threshold=0.67,
        override_threshold=0.90,
    )

    keys = {aid: KeyPair() for aid in ["human_1", "human_2", "human_3", "ai_1", "ai_2"]}
    actors = {
        "human_1": Actor("human_1", "Dr. Chen", keys["human_1"].public_key_hex, 1.0, "university", "human"),
        "human_2": Actor("human_2", "Marcus", keys["human_2"].public_key_hex, 1.0, "guild", "human"),
        "human_3": Actor("human_3", "Aisha", keys["human_3"].public_key_hex, 1.0, "investigation", "human"),
        "ai_1": Actor("ai_1", "Claude", keys["ai_1"].public_key_hex, 0.8, "investigation", "agent"),
        "ai_2": Actor("ai_2", "Qwen", keys["ai_2"].public_key_hex, 0.6, "university", "model"),
    }

    governor = MetaGovernorService(
        constitution=constitution,
        actor_registry=InMemoryActorRegistry(actors),
        zarc_journal=InMemoryZarcJournal(),
        escalation_service=InMemoryEscalationService(),
    )

    print("--- Scenario 1: normal action ---")
    a1 = Action("act-001", "cell_inference", {"cell": "anchorum"}, "human_1", time.time_ns())
    print(governor.evaluate(a1).value)

    print("\n--- Scenario 2: constitutional veto ---")
    a2 = Action("act-002", "memory_erase", {"target": "claude-agent"}, "ai_1", time.time_ns())
    print(governor.evaluate(a2).value)
    print(f"Frozen: {governor.is_frozen}")

    print("\n--- Scenario 3: critical quorum (needs 2/3 chambers + 67% weight) ---")
    a3 = Action("act-003", "treaty_amend", {"treaty": "T-1"}, "human_2", time.time_ns(), EscalationLevel.CRITICAL)
    print(governor.evaluate(a3).value)
    governor.open_quorum(a3)
    now = time.time_ns()
    for aid, approve in [("human_1", True), ("human_2", True), ("human_3", True), ("ai_1", False), ("ai_2", True)]:
        vote = Vote(aid, a3.action_id, approve, now, "")
        sig = keys[aid].sign(vote.payload_bytes(a3))
        object.__setattr__(vote, "signature_hex", sig)
        governor.cast_vote(vote)
    result = governor.close_quorum(a3.action_id)
    print(f"Final: {result.value if result else 'NONE'}, Frozen: {governor.is_frozen}")

    print("\n--- Scenario 4: override (needs 90% weight) ---")
    a4 = Action("act-004", "constitution_suspend", {"article": "integrity"}, "human_1", time.time_ns(), EscalationLevel.OVERRIDE)
    print(governor.evaluate(a4).value)
    governor.open_quorum(a4)
    for aid, approve in [("human_1", True), ("human_2", True), ("human_3", True), ("ai_1", True), ("ai_2", False)]:
        vote = Vote(aid, a4.action_id, approve, now, "")
        sig = keys[aid].sign(vote.payload_bytes(a4))
        object.__setattr__(vote, "signature_hex", sig)
        governor.cast_vote(vote)
    result = governor.close_quorum(a4.action_id)
    print(f"Final: {result.value if result else 'NONE'}, Frozen: {governor.is_frozen}")

    print("\n" + "=" * 60)


if __name__ == "__main__":
    demo()
