# Design Document: TriCameralCouncil

**Status:** Study-phase production design  
**Scope:** Implement a chamber-level deliberation layer over Egregore's existing cells, dispatching representative cells from each chamber and arbitrating their outputs through the RFE engine.  
**Goal:** Provide precise layering, class responsibilities, API signatures, and registry wiring so cross-chamber decisions are deterministic, signed, and replay-correct.  

---

## 1. Design principles

1. **Chambers are routing policies, not new institutions.** Use the existing cell taxonomy (`university`, `guild`, `investigation`) as chamber membership.
2. **Deliberation is RFE arbitration.** Each chamber produces an evidence stream; the council fuses them with a chamber-aware conflict policy.
3. **Fail-closed on disagreement.** If fewer than two chambers agree, the action is vetoed.
4. **Everything to `.zarc`.** The request, chamber streams, fusion report, and final verdict are persisted.
5. **Layer-compliant.** Domain models are pure; application orchestrates; interface exposes HTTP; infrastructure adapters wire registries.

---

## 2. Existing substrate

- **Cell taxonomy:** `cells/*/spec.yaml` declares `type` and `taxonomy` (`university/...`, `guild/...`, `investigation/...`).
- **Cell execution:** `CellExecutor.run(cell_id, request)` returns a `CellResult`.
- **Ombudsman routing:** `OmbudsmanRouter` dispatches by taxonomy and fuses streams.
- **RFE fusion:** `reproducible_fusion(manifest)` returns a report + decision log.
- **Provenance:** `.zarc` journal persists signed events.

---

## 3. Layered module layout

```
src/egregore/
├── domain/
│   └── tri_cameral.py            # Chamber, DeliberationRequest,
│                                 # ChamberStream, DeliberationResult
├── application/
│   ├── tri_cameral_council.py    # TriCameralCouncil orchestration
│   └── ports/
│       └── tri_cameral_ports.py  # CellExecutorPort, RFEEnginePort,
│                                 # ZarcJournalPort, CellRegistryPort
├── interface/
│   └── http_api/http/v1/
│       └── governance.py         # /v1/governance/tricameral/*
└── infrastructure/
    └── tri_cameral_adapters.py   # adapters for CellExecutor, RFE engine, registries
```

**Import rule summary**

| Layer | May import from | Must not import from |
|-------|-----------------|----------------------|
| `domain/tri_cameral.py` | `shared/` only | `application/`, `infrastructure/`, `interface/` |
| `application/tri_cameral_council.py` | `domain/`, `shared/`, `application/ports/` | `infrastructure/`, `interface/` |
| `application/ports/tri_cameral_ports.py` | `domain/`, `shared/` | `infrastructure/`, `interface/` |
| `interface/http_api/http/v1/governance.py` | `application/`, `domain/`, `shared/` | `infrastructure/` |
| `infrastructure/tri_cameral_adapters.py` | any layer | — |

---

## 4. Domain layer (`src/egregore/domain/tri_cameral.py`)

### 4.1 Enums

```python
from enum import Enum

class Chamber(str, Enum):
    UNIVERSITY = "university"
    GUILD = "guild"
    INVESTIGATION = "investigation"
    LEGAL = "legal"
    AUDIT = "audit"

class DeliberationVerdict(str, Enum):
    APPROVED = "approved"
    REJECTED = "rejected"
    DEADLOCK = "deadlock"
    ERROR = "error"
```

### 4.2 `DeliberationRequest`

```python
from dataclasses import dataclass
from typing import Any, Mapping

@dataclass(frozen=True)
class DeliberationRequest:
    request_id: str
    input_text: str
    payload: Mapping[str, Any]
    proposed_by_actor_id: str
    timestamp_ns: int
    required_chambers: int = 2
```

### 4.3 `ChamberStream`

```python
from dataclasses import dataclass
from typing import Optional

@dataclass(frozen=True)
class ChamberStream:
    chamber: Chamber
    cell_id: str
    stream_id: str
    content: Mapping[str, Any]
    confidence: float
    timestamp_ns: int
    error: Optional[str] = None
```

### 4.4 `DeliberationResult`

```python
from dataclasses import dataclass, field
from typing import List, Mapping, Any, Optional

@dataclass(frozen=True)
class DeliberationResult:
    request_id: str
    verdict: DeliberationVerdict
    report_hash: Optional[str]
    decision_log_hash: Optional[str]
    chamber_streams: List[ChamberStream] = field(default_factory=list)
    chamber_votes: Mapping[str, bool] = field(default_factory=dict)
    fusion_report: Optional[Mapping[str, Any]] = None
    timestamp_ns: int = 0
```

---

## 5. Application ports (`src/egregore/application/ports/tri_cameral_ports.py`)

```python
from typing import Mapping, Protocol, Sequence
from egregore.domain.tri_cameral import Chamber, ChamberStream, DeliberationRequest

class CellRegistryPort(Protocol):
    def find_by_chamber(self, chamber: Chamber) -> Sequence[str]:
        """Return cell_ids belonging to a chamber, ordered by preference."""
        ...

class CellExecutorPort(Protocol):
    def run(self, cell_id: str, request: Mapping[str, Any]) -> Mapping[str, Any]:
        """Execute a cell and return a dict (e.g., CellResult.model_dump())."""
        ...

class RFEEnginePort(Protocol):
    def fuse(self, manifest: Mapping[str, Any]) -> Mapping[str, Any]:
        """Run reproducible_fusion and return the fusion result dict."""
        ...

class ZarcJournalPort(Protocol):
    def append(self, event_type: str, payload: Mapping[str, object]) -> str: ...
```

---

## 6. Application service (`src/egregore/application/tri_cameral_council.py`)

```python
from dataclasses import dataclass
from typing import Mapping, Sequence
import time
import uuid

from egregore.domain.tri_cameral import (
    Chamber,
    ChamberStream,
    DeliberationRequest,
    DeliberationResult,
    DeliberationVerdict,
)
from egregore.application.ports.tri_cameral_ports import (
    CellExecutorPort,
    CellRegistryPort,
    RFEEnginePort,
    ZarcJournalPort,
)

@dataclass
class TriCameralCouncil:
    cell_registry: CellRegistryPort
    cell_executor: CellExecutorPort
    rfe_engine: RFEEnginePort
    zarc_journal: ZarcJournalPort
    chambers: Sequence[Chamber] = (Chamber.UNIVERSITY, Chamber.GUILD, Chamber.INVESTIGATION)

    def deliberate(self, request: DeliberationRequest) -> DeliberationResult:
        now = time.time_ns()
        streams: list[ChamberStream] = []

        for chamber in self.chambers:
            candidates = self.cell_registry.find_by_chamber(chamber)
            if not candidates:
                continue
            cell_id = candidates[0]
            try:
                cell_result = self.cell_executor.run(
                    cell_id,
                    {"input": request.input_text, **request.payload},
                )
                streams.append(ChamberStream(
                    chamber=chamber,
                    cell_id=cell_id,
                    stream_id=f"{request.request_id}-{chamber.value}",
                    content=cell_result.get("final_output", {}),
                    confidence=float(cell_result.get("confidence", 0.5)),
                    timestamp_ns=now,
                ))
            except Exception as exc:
                streams.append(ChamberStream(
                    chamber=chamber,
                    cell_id=cell_id,
                    stream_id=f"{request.request_id}-{chamber.value}",
                    content={},
                    confidence=0.0,
                    timestamp_ns=now,
                    error=str(exc),
                ))

        # Build RFE manifest from chamber streams
        manifest = self._build_manifest(request, streams)

        if not manifest["streams"]:
            return DeliberationResult(
                request_id=request.request_id,
                verdict=DeliberationVerdict.ERROR,
                report_hash=None,
                decision_log_hash=None,
                chamber_streams=streams,
                timestamp_ns=now,
            )

        fusion = self.rfe_engine.fuse(manifest)

        # Determine chamber votes from fusion conclusions and individual streams
        chamber_votes = self._derive_chamber_votes(streams, fusion)
        approving = sum(1 for v in chamber_votes.values() if v)

        if approving >= request.required_chambers:
            verdict = DeliberationVerdict.APPROVED
        elif approving == 0:
            verdict = DeliberationVerdict.REJECTED
        else:
            verdict = DeliberationVerdict.DEADLOCK

        result = DeliberationResult(
            request_id=request.request_id,
            verdict=verdict,
            report_hash=fusion.get("report_hash"),
            decision_log_hash=fusion.get("decision_log_hash"),
            chamber_streams=streams,
            chamber_votes=chamber_votes,
            fusion_report=fusion.get("report"),
            timestamp_ns=now,
        )

        self.zarc_journal.append(
            "tricameral_deliberation_completed",
            {
                "request_id": request.request_id,
                "verdict": verdict.value,
                "report_hash": result.report_hash,
                "decision_log_hash": result.decision_log_hash,
                "chamber_votes": chamber_votes,
                "timestamp_ns": now,
            },
        )

        return result

    def _build_manifest(
        self,
        request: DeliberationRequest,
        streams: Sequence[ChamberStream],
    ) -> Mapping[str, Any]:
        from datetime import UTC, datetime
        return {
            "case_id": f"tricameral-{request.request_id}",
            "timestamp": datetime.now(UTC).isoformat(),
            "streams": [
                {
                    "stream_id": s.stream_id,
                    "type": f"chamber_{s.chamber.value}",
                    "source_tier": 3,
                    "content": s.content,
                    "confidence": s.confidence,
                    "provenance_hash": hashlib.sha256(
                        json.dumps(s.content, sort_keys=True).encode()
                    ).hexdigest(),
                    "timestamp": datetime.now(UTC).isoformat(),
                }
                for s in streams if not s.error
            ],
            "constraints": {"output_format": "json", "language": "en"},
        }

    def _derive_chamber_votes(
        self,
        streams: Sequence[ChamberStream],
        fusion: Mapping[str, Any],
    ) -> Mapping[str, bool]:
        votes: dict[str, bool] = {}
        conclusions = [c.lower() for c in fusion.get("report", {}).get("baseline_conclusions", [])]
        approve_phrases = {"approve", "approved", "yes", "allow", "pass"}

        for stream in streams:
            if stream.error:
                votes[stream.chamber.value] = False
                continue
            # Simple heuristic: chamber approves if its content contains approval language
            # or if the fused conclusions are approving.
            content_str = json.dumps(stream.content, sort_keys=True).lower()
            votes[stream.chamber.value] = (
                any(p in content_str for p in approve_phrases)
                or any(p in " ".join(conclusions) for p in approve_phrases)
            )
        return votes
```

---

## 7. HTTP interface

Add to `src/egregore/http_api/http/v1/governance.py`:

```python
from egregore.application.tri_cameral_council import TriCameralCouncil
from egregore.domain.tri_cameral import DeliberationRequest

class DeliberateRequest(BaseModel):
    request_id: str | None = None
    input_text: str
    payload: dict[str, Any] = Field(default_factory=dict)
    required_chambers: int = 2

def get_tri_cameral_council() -> TriCameralCouncil:
    from egregore.interface.bootstrap import app_state
    return app_state.tri_cameral_council

@router.post("/tricameral/deliberate")
def deliberate(
    req: DeliberateRequest,
    identity=Depends(get_user_identity),
    council: TriCameralCouncil = Depends(get_tri_cameral_council),
):
    request = DeliberationRequest(
        request_id=req.request_id or f"delib-{uuid.uuid4().hex[:16]}",
        input_text=req.input_text,
        payload=req.payload,
        proposed_by_actor_id=identity.user_id,
        timestamp_ns=time.time_ns(),
        required_chambers=req.required_chambers,
    )
    result = council.deliberate(request)
    return {
        "request_id": result.request_id,
        "verdict": result.verdict.value,
        "report_hash": result.report_hash,
        "decision_log_hash": result.decision_log_hash,
        "chamber_votes": result.chamber_votes,
        "chambers": [
            {"chamber": s.chamber.value, "cell_id": s.cell_id, "confidence": s.confidence}
            for s in result.chamber_streams
        ],
    }
```

---

## 8. Infrastructure adapters

### 8.1 `CellRegistryChamberAdapter`

```python
from typing import Sequence
from egregore.application.ports.tri_cameral_ports import CellRegistryPort
from egregore.cells.registry import CellRegistry
from egregore.domain.tri_cameral import Chamber

class CellRegistryChamberAdapter(CellRegistryPort):
    def __init__(self, registry: CellRegistry):
        self.registry = registry

    def find_by_chamber(self, chamber: Chamber) -> Sequence[str]:
        return [
            spec.cell_id
            for spec in self.registry.list_cells()
            if spec.taxonomy.root == chamber.value
        ]
```

### 8.2 `CellExecutorAdapter`

```python
from typing import Any, Mapping
from egregore.application.ports.tri_cameral_ports import CellExecutorPort
from egregore.cells.executor import CellExecutor

class CellExecutorAdapter(CellExecutorPort):
    def __init__(self, executor: CellExecutor):
        self.executor = executor

    def run(self, cell_id: str, request: Mapping[str, Any]) -> Mapping[str, Any]:
        result = self.executor.run(cell_id, dict(request))
        return result.model_dump(by_alias=True)
```

### 8.3 `RFEEngineAdapter`

```python
from typing import Any, Mapping
from egregore.application.ports.tri_cameral_ports import RFEEnginePort
from egregore.rfe.engine import reproducible_fusion

class RFEEngineAdapter(RFEEnginePort):
    def fuse(self, manifest: Mapping[str, Any]) -> Mapping[str, Any]:
        return reproducible_fusion(manifest)
```

---

## 9. Bootstrap wiring

In `src/egregore/interface/bootstrap.py`:

```python
from egregore.application.tri_cameral_council import TriCameralCouncil
from egregore.domain.tri_cameral import Chamber
from egregore.infrastructure.tri_cameral_adapters import (
    CellRegistryChamberAdapter,
    CellExecutorAdapter,
    RFEEngineAdapter,
    ZarcJournalAdapter,
)

def _build_tri_cameral_council(app_state) -> TriCameralCouncil:
    return TriCameralCouncil(
        cell_registry=CellRegistryChamberAdapter(app_state.cell_registry),
        cell_executor=CellExecutorAdapter(app_state.cell_executor),
        rfe_engine=RFEEngineAdapter(),
        zarc_journal=ZarcJournalAdapter(app_state.zarc_journal),
        chambers=(Chamber.UNIVERSITY, Chamber.GUILD, Chamber.INVESTIGATION),
    )
```

---

## 10. Testing strategy

- **Domain tests:** `DeliberationResult` verdict logic with synthetic chamber votes.
- **Application tests:** Fake cell executor and RFE engine; verify chamber dispatch, manifest construction, verdict computation, and `.zarc` events.
- **Integration tests:** Run against real cells and RFE with a simple input; verify report hashes.
- **Architecture-policy tests:** Ensure `application/` does not import `cells/` or `rfe/` directly; all access through ports/adapters.

---

## 11. Open questions

1. Which chambers participate by default? University/Guild/Investigation only, or also Legal/Audit?
2. How is the representative cell selected within a chamber? Least loaded? Round-robin? Tier-priority?
3. What does a chamber "approve" mean? A boolean in cell output, a confidence threshold, or a fused conclusion?
4. Should the council call `MetaGovernorService.evaluate()` before deliberating?
5. Should deliberation results be binding, or advisory with a separate authorization step?
