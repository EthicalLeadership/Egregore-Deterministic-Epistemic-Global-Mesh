# Design Document: MetaGovernorService

**Status:** Study-phase production design  
**Scope:** Full implementation plan for the constitutional AI overseer ("The Weave") in the Egregore codebase.  
**Goal:** Provide precise layering, class responsibilities, API signatures, and registry wiring so the refactor from the prototype is mechanical and no architecture-policy test fires.  

---

## 1. Design principles

1. **Fail-closed.** If the governor cannot verify constitutionality or quorum, the action is blocked and the system freezes.
2. **Deterministic & replay-correct.** Every governance event is written to the `.zarc` journal with a `prev_hash` chain and signatures.
3. **Layer-compliant.** Domain models live in `domain/`, orchestration in `application/`, HTTP surface in `interface/`.
4. **No single oracle.** The governor coordinates signed events and pre-registered rules; it does not invent authority.
5. **Re-use existing registries.** Actors, weights, and chambers come from `UserIdentity`, `CellRegistry`, `AgentRegistry`, and `NodeRegistry` rather than hard-coded lists.

---

## 2. Layered module layout

```
src/egregore/
├── domain/
│   └── meta_governor.py          # pure models: Action, Vote, QuorumSession,
│                                 # Constitution, OverrideAuthorization, Verdict
├── application/
│   ├── meta_governor_service.py  # orchestration: evaluate, open/close quorum,
│   │                             # collect verified votes, coordinate freeze
│   └── ports/
│       └── meta_governor_ports.py# injected dependencies (ZarcJournal,
│                                 # EscalationService, ActorRegistry)
├── interface/
│   └── http_api/http/v1/
│       └── governance.py         # FastAPI router: /v1/governance/*
└── infrastructure/
    └── meta_governor_adapters.py # concrete adapters wiring registries
```

**Import rule summary**

| Layer | May import from | Must not import from |
|-------|-----------------|----------------------|
| `domain/meta_governor.py` | `shared/` only | `application/`, `infrastructure/`, `interface/` |
| `application/meta_governor_service.py` | `domain/`, `shared/`, `application/ports/` | `infrastructure/`, `interface/` |
| `application/ports/` | `domain/`, `shared/` | `infrastructure/`, `interface/` |
| `interface/http_api/http/v1/governance.py` | `application/`, `domain/`, `shared/` | `infrastructure/` (use injected service) |
| `infrastructure/meta_governor_adapters.py` | anything (adapter boundary) | — |

This satisfies the existing architecture-policy tests (`tests/test_arch_enforcement.py`, `tests/test_architecture_policy_intent.py`).

---

## 3. Domain layer (`src/egregore/domain/meta_governor.py`)

Pure dataclasses and enums. No I/O, no imports outside `shared/`.

### 3.1 Enums

```python
from enum import Enum, auto

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
```

Use `str` mix-in for stable JSON/Pydantic serialization.

### 3.2 `Actor`

```python
from dataclasses import dataclass
from typing import Optional

@dataclass(frozen=True)
class Actor:
    actor_id: str
    display_name: str
    public_key_hex: str          # Ed25519 verify key
    weight: float = 1.0
    chamber: str = "unassigned"  # university | guild | investigation | legal | audit
    actor_type: str = "unknown"  # human | agent | model | cell
```

`Actor` is a read-only view produced by the infrastructure adapters from the various registries.

### 3.3 `Action`

```python
from dataclasses import dataclass, asdict
from typing import Any, Mapping
import json

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
        d["payload"] = dict(self.payload)  # defensive copy
        return json.dumps(d, sort_keys=True, ensure_ascii=False).encode("utf-8")
```

`to_canonical_bytes()` is used for hashing and signature verification. It mirrors the canonicalization pattern in `shared/canonical.py`.

### 3.4 `Vote`

```python
@dataclass(frozen=True)
class Vote:
    actor_id: str
    action_id: str
    approve: bool
    timestamp_ns: int
    signature_hex: str  # Ed25519 signature over canonical(action + approve + timestamp)

    def payload_bytes(self, action: Action) -> bytes:
        d = {
            "action": action.action_id,
            "action_hash": hashlib.sha256(action.to_canonical_bytes()).hexdigest(),
            "approve": self.approve,
            "timestamp_ns": self.timestamp_ns,
        }
        return json.dumps(d, sort_keys=True).encode("utf-8")
```

Signing the action hash (rather than the full action bytes) lets a voter sign without re-serializing the entire action on every vote.

### 3.5 `QuorumSession`

```python
from dataclasses import dataclass, field
from typing import List, Optional, Mapping

@dataclass
class QuorumSession:
    action_id: str
    opened_at_ns: int
    threshold: float
    chamber_requirement: Optional[Mapping[str, int]] = None
    votes: List[Vote] = field(default_factory=list)
    closed_at_ns: Optional[int] = None
    final_verdict: Optional[Verdict] = None

    def approving_weight(self, actors: Mapping[str, Actor]) -> float:
        seen = set()
        total = 0.0
        for vote in self.votes:
            if vote.actor_id in seen:
                continue
            seen.add(vote.actor_id)
            if vote.status != VoteStatus.VERIFIED:
                continue
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
            if vote.actor_id in seen:
                continue
            seen.add(vote.actor_id)
            if vote.status != VoteStatus.VERIFIED or not vote.approve:
                continue
            actor = actors.get(vote.actor_id)
            if actor:
                counts[actor.chamber] = counts.get(actor.chamber, 0) + 1
        return counts
```

### 3.6 `Constitution`

```python
from dataclasses import dataclass, field
from typing import List, Mapping, Any

@dataclass(frozen=True)
class Constitution:
    quorum_threshold: float = 0.67
    override_threshold: float = 0.90
    veto_rules: List[Mapping[str, Any]] = field(default_factory=list)
    max_quorum_window_ns: int = 300_000_000_000  # 5 minutes

    def evaluate(self, action: Action) -> Verdict:
        for rule in self.veto_rules:
            if self._matches(rule, action):
                return Verdict.VETOED

        if action.escalation_level == EscalationLevel.OVERRIDE:
            return Verdict.REQUIRES_OVERRIDE
        if action.escalation_level in (EscalationLevel.CRITICAL, EscalationLevel.ELEVATED):
            return Verdict.REQUIRES_QUORUM
        return Verdict.ALLOWED

    def _matches(self, rule: Mapping[str, Any], action: Action) -> bool:
        patterns = rule.get("action_type_patterns", [])
        return any(p in action.action_type for p in patterns)
```

In production, load this from `config/egregore_constitution.yaml` via `FederationConstitution`.

---

## 4. Application ports (`src/egregore/application/ports/meta_governor_ports.py`)

Injected interfaces so the service remains testable and layer-clean.

```python
from typing import Mapping, Protocol
from egregore.domain.meta_governor import Actor

class ActorRegistryPort(Protocol):
    def get_actors(self) -> Mapping[str, Actor]:
        """Return all currently registered voting actors keyed by actor_id."""
        ...

class ZarcJournalPort(Protocol):
    def append(self, event_type: str, payload: Mapping[str, object]) -> str:
        """Append a canonical event to the .zarc journal; return event hash."""
        ...

class EscalationServicePort(Protocol):
    def freeze(self, reason: str, action_id: str) -> None: ...
    def unfreeze(self, reason: str, action_id: str) -> None: ...
```

---

## 5. Application service (`src/egregore/application/meta_governor_service.py`)

Single orchestrator. All business logic lives here.

### 5.1 Class signature

```python
from dataclasses import dataclass, field
from typing import Mapping, Optional
import time

from egregore.domain.meta_governor import (
    Action,
    Actor,
    Constitution,
    EscalationLevel,
    QuorumSession,
    Verdict,
    Vote,
    VoteStatus,
)
from egregore.application.ports.meta_governor_ports import (
    ActorRegistryPort,
    EscalationServicePort,
    ZarcJournalPort,
)

@dataclass
class MetaGovernorService:
    constitution: Constitution
    actor_registry: ActorRegistryPort
    zarc_journal: ZarcJournalPort
    escalation_service: EscalationServicePort
    _sessions: dict[str, QuorumSession] = field(default_factory=dict, init=False)
    _frozen: bool = field(default=False, init=False)

    # ------------------------------------------------------------------
    # Core API
    # ------------------------------------------------------------------
    def evaluate(self, action: Action) -> Verdict:
        verdict = self.constitution.evaluate(action)
        self.zarc_journal.append(
            "meta_governor_action_evaluated",
            {
                "action_id": action.action_id,
                "action_type": action.action_type,
                "proposed_by": action.proposed_by_actor_id,
                "escalation_level": action.escalation_level.value,
                "verdict": verdict.value,
                "timestamp_ns": time.time_ns(),
            },
        )

        if verdict == Verdict.VETOED:
            self._freeze("Constitutional veto", action.action_id)
        return verdict

    def open_quorum(self, action: Action) -> QuorumSession:
        threshold = (
            self.constitution.override_threshold
            if action.escalation_level == EscalationLevel.OVERRIDE
            else self.constitution.quorum_threshold
        )
        session = QuorumSession(
            action_id=action.action_id,
            opened_at_ns=time.time_ns(),
            threshold=threshold,
        )
        self._sessions[action.action_id] = session
        self.zarc_journal.append(
            "meta_governor_quorum_opened",
            {
                "action_id": action.action_id,
                "threshold": threshold,
                "timestamp_ns": session.opened_at_ns,
            },
        )
        return session

    def cast_vote(self, vote: Vote) -> bool:
        actors = self.actor_registry.get_actors()
        actor = actors.get(vote.actor_id)
        if actor is None:
            self._journal_reject(vote, "unknown_actor")
            return False

        session = self._sessions.get(vote.action_id)
        if session is None:
            self._journal_reject(vote, "no_session")
            return False

        if session.closed_at_ns is not None:
            self._journal_reject(vote, "session_closed")
            return False

        if any(v.actor_id == vote.actor_id for v in session.votes):
            self._journal_reject(vote, "duplicate_vote")
            return False

        if not self._verify_signature(vote, actor):
            self._journal_reject(vote, "bad_signature")
            return False

        # Vote is verified; append it immutably
        object.__setattr__(vote, "status", VoteStatus.VERIFIED)
        session.votes.append(vote)
        self.zarc_journal.append(
            "meta_governor_vote_cast",
            {
                "action_id": vote.action_id,
                "actor_id": vote.actor_id,
                "approve": vote.approve,
                "timestamp_ns": vote.timestamp_ns,
            },
        )
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
        chamber_ok = self._chamber_requirement_met(session, actors)

        if ratio >= session.threshold and chamber_ok:
            session.final_verdict = Verdict.ALLOWED
            self._unfreeze("Quorum authorized", action_id)
        else:
            session.final_verdict = Verdict.VETOED
            self._freeze("Quorum failed", action_id)

        self.zarc_journal.append(
            "meta_governor_quorum_closed",
            {
                "action_id": action_id,
                "threshold": session.threshold,
                "approving_weight": total,
                "possible_weight": possible,
                "ratio": ratio,
                "chamber_ok": chamber_ok,
                "verdict": session.final_verdict.value,
                "timestamp_ns": session.closed_at_ns,
            },
        )
        return session.final_verdict

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------
    def _verify_signature(self, vote: Vote, actor: Actor) -> bool:
        try:
            from nacl.signing import VerifyKey
            vk = VerifyKey(bytes.fromhex(actor.public_key_hex))
            action = self._action_for(vote.action_id)
            if action is None:
                return False
            vk.verify(vote.payload_bytes(action), bytes.fromhex(vote.signature_hex))
            return True
        except Exception:
            return False

    def _action_for(self, action_id: str) -> Optional[Action]:
        # Actions are not stored in the service; resolution delegated to adapter/journal.
        # For prototype, the service can keep a small action cache, but production should
        # resolve from .zarc journal replay or an ActionRepositoryPort.
        return None

    def _chamber_requirement_met(self, session: QuorumSession, actors: Mapping[str, Actor]) -> bool:
        if not session.chamber_requirement:
            return True
        counts = session.chamber_counts(actors)
        for chamber, minimum in session.chamber_requirement.items():
            if counts.get(chamber, 0) < minimum:
                return False
        return True

    def _freeze(self, reason: str, action_id: str) -> None:
        self._frozen = True
        self.escalation_service.freeze(reason, action_id)

    def _unfreeze(self, reason: str, action_id: str) -> None:
        self._frozen = False
        self.escalation_service.unfreeze(reason, action_id)

    def _journal_reject(self, vote: Vote, reason: str) -> None:
        self.zarc_journal.append(
            "meta_governor_vote_rejected",
            {
                "action_id": vote.action_id,
                "actor_id": vote.actor_id,
                "reason": reason,
                "timestamp_ns": time.time_ns(),
            },
        )

    @property
    def is_frozen(self) -> bool:
        return self._frozen
```

### 5.2 Signature verification note

The vote signature must cover:
1. A hash of the canonical action bytes (so the vote cannot be replayed against a different action).
2. The `approve` boolean.
3. The vote timestamp.

The HTTP API returns the action hash in the quorum-open response so clients can sign correctly.

---

## 6. Infrastructure adapters (`src/egregore/infrastructure/meta_governor_adapters.py`)

These adapters are allowed to import across layers. They translate Egregore's heterogeneous registries into the uniform `Actor` view.

### 6.1 `RegistryActorAdapter`

```python
from typing import Mapping

from egregore.domain.meta_governor import Actor
from egregore.application.ports.meta_governor_ports import ActorRegistryPort
from egregore.cells.registry import CellRegistry
from egregore.application.agent_registry import AgentRegistry
from egregore.infrastructure.local_model_catalog import LocalModelCatalog

class RegistryActorAdapter(ActorRegistryPort):
    def __init__(
        self,
        cell_registry: CellRegistry,
        agent_registry: AgentRegistry,
        model_catalog: LocalModelCatalog,
    ):
        self.cell_registry = cell_registry
        self.agent_registry = agent_registry
        self.model_catalog = model_catalog

    def get_actors(self) -> Mapping[str, Actor]:
        actors: dict[str, Actor] = {}

        for spec in self.cell_registry.list_cells():
            actors[spec.cell_id] = Actor(
                actor_id=spec.cell_id,
                display_name=spec.cell_id,
                public_key_hex=self._derive_key("cell", spec.cell_id),
                weight=1.0 - (spec.tier / 10.0),
                chamber=spec.taxonomy.root or "unassigned",
                actor_type="cell",
            )

        for name, spec in self.agent_registry.list().items():
            actors[name] = Actor(
                actor_id=name,
                display_name=spec.description or name,
                public_key_hex=self._derive_key("agent", name),
                weight=0.8,
                chamber="investigation",
                actor_type="agent",
            )

        for model in self.model_catalog._specs:
            actors[model.model_id] = Actor(
                actor_id=model.model_id,
                display_name=model.model_id,
                public_key_hex=self._derive_key("model", model.model_id),
                weight=0.6,
                chamber=model.vertical,
                actor_type="model",
            )

        return actors

    def _derive_key(self, namespace: str, id: str) -> str:
        import hashlib
        return hashlib.sha256(f"{namespace}:{id}".encode()).hexdigest()[:64]
```

**Critical:** Public-key derivation must be replaced with a real key registry before production. The prototype uses deterministic hashes for convenience.

### 6.2 `ZarcJournalAdapter`

```python
from typing import Mapping

from egregore.application.ports.meta_governor_ports import ZarcJournalPort
from egregore.infrastructure.zarc_journal import ZarcJournal
from egregore.kernel.provenance import ProvenanceEvent

class ZarcJournalAdapter(ZarcJournalPort):
    def __init__(self, journal: ZarcJournal):
        self.journal = journal

    def append(self, event_type: str, payload: Mapping[str, object]) -> str:
        event = ProvenanceEvent(
            event_type=event_type,
            payload=dict(payload),
            timestamp_ns=payload.get("timestamp_ns", 0),
        )
        return self.journal.append(event)
```

### 6.3 `EscalationServiceAdapter`

```python
from egregore.application.ports.meta_governor_ports import EscalationServicePort
from egregore.application.escalation_service import EscalationService

class EscalationServiceAdapter(EscalationServicePort):
    def __init__(self, service: EscalationService):
        self.service = service

    def freeze(self, reason: str, action_id: str) -> None:
        self.service.open(
            level=EscalationLevel.CRITICAL,
            trigger=reason,
            affected_nodes=[],
            evidence_hashes=[action_id],
        )

    def unfreeze(self, reason: str, action_id: str) -> None:
        # Delegates to the freeze controller held by EscalationService
        if self.service._freeze is not None:
            self.service._freeze.unfreeze(reason=reason, operator_id="meta_governor")
```

---

## 7. HTTP interface (`src/egregore/http_api/http/v1/governance.py`)

FastAPI router using Pydantic v2.

```python
from typing import Any
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

from egregore.application.meta_governor_service import MetaGovernorService
from egregore.domain.meta_governor import Action, EscalationLevel, Verdict, Vote
from egregore.http_api.http.middleware.api_key_middleware import get_user_identity

router = APIRouter(prefix="/api/v1/governance", tags=["governance"])

class ActionRequest(BaseModel):
    action_type: str
    payload: dict[str, Any] = Field(default_factory=dict)
    escalation_level: EscalationLevel = EscalationLevel.NORMAL

class VoteRequest(BaseModel):
    action_id: str
    approve: bool
    timestamp_ns: int
    signature_hex: str

class QuorumCloseRequest(BaseModel):
    action_id: str

def get_meta_governor() -> MetaGovernorService:
    from egregore.interface.bootstrap import app_state
    return app_state.meta_governor_service

@router.post("/compact/check")
def check_action(
    req: ActionRequest,
    identity=Depends(get_user_identity),
    governor: MetaGovernorService = Depends(get_meta_governor),
):
    action = Action(
        action_id=f"action-{identity.user_id}-{req.timestamp_ns}",
        action_type=req.action_type,
        payload=req.payload,
        proposed_by_actor_id=identity.user_id,
        timestamp_ns=req.timestamp_ns,
        escalation_level=req.escalation_level,
    )
    verdict = governor.evaluate(action)
    return {"action_id": action.action_id, "verdict": verdict.value, "frozen": governor.is_frozen}

@router.post("/quorum/open/{action_id}")
def open_quorum(
    action_id: str,
    req: ActionRequest,
    identity=Depends(get_user_identity),
    governor: MetaGovernorService = Depends(get_meta_governor),
):
    action = Action(
        action_id=action_id,
        action_type=req.action_type,
        payload=req.payload,
        proposed_by_actor_id=identity.user_id,
        timestamp_ns=req.timestamp_ns,
        escalation_level=req.escalation_level,
    )
    verdict = governor.evaluate(action)
    if verdict not in (Verdict.REQUIRES_QUORUM, Verdict.REQUIRES_OVERRIDE):
        return {"action_id": action_id, "verdict": verdict.value}

    session = governor.open_quorum(action)
    return {
        "action_id": action_id,
        "session": {"opened_at_ns": session.opened_at_ns, "threshold": session.threshold},
        "action_hash": hashlib.sha256(action.to_canonical_bytes()).hexdigest(),
    }

@router.post("/vote")
def cast_vote(
    req: VoteRequest,
    identity=Depends(get_user_identity),
    governor: MetaGovernorService = Depends(get_meta_governor),
):
    vote = Vote(
        actor_id=identity.user_id,
        action_id=req.action_id,
        approve=req.approve,
        timestamp_ns=req.timestamp_ns,
        signature_hex=req.signature_hex,
    )
    accepted = governor.cast_vote(vote)
    if not accepted:
        raise HTTPException(status_code=400, detail="Vote rejected")
    return {"accepted": True}

@router.post("/quorum/close")
def close_quorum(
    req: QuorumCloseRequest,
    governor: MetaGovernorService = Depends(get_meta_governor),
):
    verdict = governor.close_quorum(req.action_id)
    if verdict is None:
        raise HTTPException(status_code=404, detail="Quorum session not found")
    return {"action_id": req.action_id, "verdict": verdict.value, "frozen": governor.is_frozen}

@router.get("/state")
def governance_state(governor: MetaGovernorService = Depends(get_meta_governor)):
    return {
        "frozen": governor.is_frozen,
        "actor_count": len(governor.actor_registry.get_actors()),
        "open_sessions": len([s for s in governor._sessions.values() if s.closed_at_ns is None]),
    }
```

---

## 8. Bootstrap wiring

Add `meta_governor_service` to app state in `src/egregore/interface/bootstrap.py`:

```python
from egregore.application.meta_governor_service import MetaGovernorService
from egregore.domain.meta_governor import Constitution
from egregore.domain.federation_constitution import FederationConstitution
from egregore.infrastructure.meta_governor_adapters import (
    RegistryActorAdapter,
    ZarcJournalAdapter,
    EscalationServiceAdapter,
)

def _build_meta_governor(app_state) -> MetaGovernorService:
    constitution = Constitution(
        quorum_threshold=0.67,
        override_threshold=0.90,
        veto_rules=FederationConstitution.load().veto_rules(),
    )
    actor_adapter = RegistryActorAdapter(
        cell_registry=app_state.cell_registry,
        agent_registry=app_state.agent_registry,
        model_catalog=app_state.model_catalog,
    )
    return MetaGovernorService(
        constitution=constitution,
        actor_registry=actor_adapter,
        zarc_journal=ZarcJournalAdapter(app_state.zarc_journal),
        escalation_service=EscalationServiceAdapter(app_state.escalation_service),
    )
```

---

## 9. Testing strategy

- **Domain tests:** `Constitution.evaluate()` for each escalation level and veto rule; `QuorumSession` weight/chamber counting; `Action` canonicalization determinism.
- **Application tests:** Use fake in-memory adapters; test ALLOWED/VETOED/REQUIRES_QUORUM/REQUIRES_OVERRIDE flows; real Ed25519 signature verification; quorum success/failure; freeze/unfreeze side effects.
- **Architecture-policy tests:** Run `test_arch_enforcement.py` and `test_architecture_policy_intent.py` after adding files. Allowlist any new cross-layer imports or move shared interfaces to `domain/`/`shared/`.

---

## 10. Open questions

1. Where do actor signing keys live? (cell spec artifacts, agent manifests, new `actors/` directory, or auth/user system)
2. Should human operators with `VERTICAL_WRITE` be voting actors, or a separate registry?
3. Should weights come from `NodeRegistry.trust_score`, a new `ContributionLedger`, or static config?
4. Is the chamber mapping `university/guild/investigation` sufficient, or do cell specs need a distinct `chamber` field?
5. Should expired quorum sessions auto-close with `VETOED` or remain open until explicitly closed?
