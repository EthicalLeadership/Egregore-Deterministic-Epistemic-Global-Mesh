# domain/work_unit.py
class WorkUnitType(Enum): ...
class WorkUnitState(Enum): PENDING, DISPATCHED, COMPLETED, REJECTED, FAILED, ...

@dataclass(frozen=True)
class WorkUnit:
    work_unit_type: WorkUnitType
    metadata: dict[str, Any] = field(default_factory=dict)
    # ... other fields unknown

# domain/work_tree.py
class WorkTree:
    @classmethod
    def create(cls, root_work_unit: WorkUnit, timestamp_ns: int) -> WorkTree: ...
    def add_child(self, parent_id: str, work_unit: WorkUnit, timestamp_ns: int) -> WorkTree: ...
    def mark_state(self, node_id: str, state: WorkUnitState) -> WorkTree: ...
    def leaves(self) -> Sequence[WorkTreeNode]: ...
    def is_terminal(self) -> bool: ...
    tree_id: str
    root_id: str
    root: WorkTreeNode
    nodes: dict[str, WorkTreeNode]

# application/work_tree_service.py
class _Scheduler(Protocol):
    def submit(self, work_unit: WorkUnit, timestamp_ns: int) -> bool: ...

class WorkTreeService:
    def __init__(self, scheduler: _Scheduler, decomposition: IWorkDecomposition, store: IWorkTreeStore, ...) -> None: ...
    def submit_tree(self, root_work_unit: WorkUnit, timestamp_ns: int) -> WorkTree: ...

# application/capacity_orchestrator.py
class AdmissionDecision(Enum): ADMITTED, REJECTED_BACKLOG_EXCEEDED, ...
class CapacityOrchestrator:
    def submit_work_unit(self, work_unit: WorkUnit) -> AdmissionDecision: ...
    def start_epoch(self, timestamp_ns: int) -> None: ...

# domain/job_models.py
class PriorityTier(StrEnum): CRITICAL, HIGH, MEDIUM, LOW
class SLAClass(StrEnum): REALTIME, INTERACTIVE, STANDARD, BATCH, BEST_EFFORT
class ComplexityTier(StrEnum): TRIVIAL, STANDARD, COMPLEX, SOVEREIGN
class JobStatus(StrEnum): PENDING, CLASSIFIED, SCHEDULED, ROUTED, EXECUTING, COMPLETED, FAILED, FROZEN

@dataclass(frozen=True)
class SLA:
    latency_target_ms: int = 1000
    throughput_target_qps: float = 1.0
    reliability_target: float = 0.99
    class_: SLAClass = SLAClass.STANDARD

@dataclass(frozen=True)
class ResourceProfile:
    cpu_percent: float = 0.0
    memory_mb: int = 0
    vram_mb: int = 0
    disk_iops: int = 0
    network_mbps: int = 0
    def can_satisfy(self, need: "ResourceProfile") -> bool: ...

@dataclass(frozen=True)
class JobRequest:
    job_id: str
    tenant_id: str
    trace_id: str
    payload: dict[str, Any]
    requested_capabilities: list[str] = field(default_factory=list)
    priority_hint: str = "STANDARD"
    sla_deadline_ns: int = 0
    metadata: dict[str, Any] = field(default_factory=dict)

@dataclass(frozen=True)
class JobClassification:
    job_id: str
    complexity: ComplexityTier
    resource_profile: ResourceProfile
    estimated_tokens: int = 0
    target_vertical: str = ""
    requested_capabilities: list[str] = field(default_factory=list)
    deterministic_required: bool = False
    priority_tier: str = ""
    created_at_ns: int = 0

@dataclass(frozen=True)
class NodeCapability:
    node_id: str
    capabilities: list[str] = field(default_factory=list)
    resource_profile: ResourceProfile = field(default_factory=ResourceProfile)
    trust_score: float = 0.5
    last_heartbeat_ns: int = 0
    status: str = "UNKNOWN"
    public_key_fingerprint: str | None = None

@dataclass(frozen=True)
class RoutingDecision:
    job_id: str
    node_id: str
    tenant_id: str
    trace_id: str
    decision_reason: str
    estimated_latency_ms: int = 0
    fallback_chain: list[str] = field(default_factory=list)

@dataclass(frozen=True)
class Job:
    job_id: str
    tenant_id: str
    trace_id: str
    priority_tier: PriorityTier
    sla: SLA
    classification: Any
    status: str = "PENDING"
    created_at_ns: int = 0
    scheduled_at_ns: int = 0
    started_at_ns: int = 0
    completed_at_ns: int = 0
    assigned_node_id: str = ""
    metadata: dict[str, Any] = field(default_factory=dict)

# interface/job_router_ports.py
class IJobClassifier(Protocol):
    def classify(self, request: JobRequest) -> JobClassification: ...

class INodeRegistry(Protocol):
    def heartbeat(self, pulse: NodeHeartbeat) -> None: ...
    def get_available(self, capabilities: list[str]) -> list[NodeCapability]: ...
    def get_node(self, node_id: str) -> NodeCapability | None: ...
    def deprecate_stale(self, cutoff_ticks: int) -> list[str]: ...

class IJobRouter(Protocol):
    def route(self, job: Job, candidates: list[NodeCapability]) -> RoutingDecision: ...

class IResilienceRouter(Protocol):
    def fallback(self, job: Job, primary_failure: str) -> RoutingDecision | None: ...
    def log_evidence(self, evidence: SealedEvidence) -> None: ...

class IScheduler(Protocol):
    def submit(self, job: Job) -> bool: ...
    def drain(self, tick: int, max_jobs: int) -> list[Job]: ...
    def get_queue_depth(self, tenant_id: str) -> dict: ...
    def snapshot(self, tick: int, tenant_id: str) -> QueueSnapshot: ...