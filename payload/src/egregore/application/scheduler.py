"""JobScheduler - Crust agency: SLA-aware priority queue manager."""

from dataclasses import dataclass, field

from egregore.domain.scheduler_models import Job, QueueSnapshot
from egregore.interface.job_router_ports import IScheduler
from egregore.shared.ports import IJobStore


@dataclass
class InMemoryJobStore:
    _jobs: dict[str, Job] = field(default_factory=dict)
    _tenant_index: dict[str, set] = field(default_factory=dict)

    def insert(self, job: Job) -> bool:
        if job.job_id in self._jobs:
            return False
        self._jobs[job.job_id] = job
        self._tenant_index.setdefault(job.tenant_id, set()).add(job.job_id)
        return True

    def fetch_pending(self, tenant_id: str, limit: int) -> list[Job]:
        ids = self._tenant_index.get(tenant_id, set())
        pending = [
            self._jobs[jid] for jid in ids if self._jobs[jid].status == "PENDING"
        ]
        pending.sort(key=lambda j: (j.urgency_score, j.job_id))
        return pending[:limit]

    def update_status(self, job_id: str, status: str, node_id: str = "") -> bool:
        if job_id not in self._jobs:
            return False
        old = self._jobs[job_id]
        from dataclasses import replace

        new = replace(
            old, status=status, assigned_node_id=node_id or old.assigned_node_id
        )
        self._jobs[job_id] = new
        return True

    def count_by_status(self, tenant_id: str) -> dict[str, int]:
        ids = self._tenant_index.get(tenant_id, set())
        counts: dict[str, int] = {}
        for jid in ids:
            status = self._jobs[jid].status
            counts[status] = counts.get(status, 0) + 1
        return counts

    def oldest_pending(self, tenant_id: str) -> int:
        ids = self._tenant_index.get(tenant_id, set())
        oldest = 0
        for jid in ids:
            job = self._jobs[jid]
            if job.status == "PENDING" and (oldest == 0 or job.created_at_ns < oldest):
                oldest = job.created_at_ns
        return oldest


class JobScheduler(IScheduler):
    def __init__(self, store: IJobStore, max_queue_depth: int = 1000):
        self._store = store
        self._max_depth = max_queue_depth
        self._total_submitted = 0
        self._total_drained = 0

    def submit(self, job: Job) -> bool:
        counts = self._store.count_by_status(job.tenant_id)
        total = sum(counts.values())
        if total >= self._max_depth:
            return False
        success = self._store.insert(job)
        if success:
            self._total_submitted += 1
        return success

    def drain(self, tick: int, max_jobs: int) -> list[Job]:
        raise NotImplementedError("Use drain_tenant(tick, tenant_id, max_jobs)")

    def drain_tenant(self, tick: int, tenant_id: str, max_jobs: int) -> list[Job]:
        pending = self._store.fetch_pending(tenant_id, limit=max_jobs)
        for job in pending:
            self._store.update_status(job.job_id, "SCHEDULED")
            self._total_drained += 1
        return pending

    def get_queue_depth(self, tenant_id: str) -> dict:
        counts = self._store.count_by_status(tenant_id)
        return {
            "total": sum(counts.values()),
            "pending": counts.get("PENDING", 0),
            "scheduled": counts.get("SCHEDULED", 0),
            "executing": counts.get("EXECUTING", 0),
            "completed": counts.get("COMPLETED", 0),
            "failed": counts.get("FAILED", 0),
        }

    def snapshot(self, tick: int, tenant_id: str) -> QueueSnapshot:
        counts = self._store.count_by_status(tenant_id)
        oldest = self._store.oldest_pending(tenant_id)
        total_wait = 0
        from egregore.shared.canonical import canonical_dumps

        payload = {
            "tick": tick,
            "tenant_id": tenant_id,
            "depth_by_priority": counts,
            "oldest_pending_ns": oldest,
        }
        import hashlib

        snapshot_hash = hashlib.sha256(
            canonical_dumps(payload).encode("utf-8")
        ).hexdigest()
        return QueueSnapshot(
            tick=tick,
            tenant_id=tenant_id,
            depth_by_priority=counts,
            oldest_pending_ns=oldest,
            total_wait_ms=total_wait,
            snapshot_hash=snapshot_hash,
        )
