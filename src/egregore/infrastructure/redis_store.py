"""
Redis-backed persistence for the Egregore orchestration suite.

Provides drop-in replacements for the in-memory node/job stores, a retry
buffer, an admission backlog, and trust/cooldown state. When Redis is
unreachable the constructors raise ``redis.ConnectionError`` so callers can
fall back to the in-memory implementations.
"""

from __future__ import annotations

import base64
import logging
import os
from dataclasses import fields, is_dataclass
from enum import Enum
from typing import TYPE_CHECKING, Any, cast

if TYPE_CHECKING:
    import redis

from egregore.domain.job_models import NodeCapability, ResourceProfile
from egregore.domain.scheduler_models import SLA, Job, PriorityTier, SLAClass
from egregore.domain.work_unit import (
    WorkUnit,
    WorkUnitDemand,
    WorkUnitState,
    WorkUnitType,
)
from egregore.shared.canonical import canonical_dumps, canonical_loads
from egregore.shared.ports import IJobStore, INodeStore

logger = logging.getLogger("egregore.redis_store")

# ---------------------------------------------------------------------------
# Configuration helpers
# ---------------------------------------------------------------------------


def redis_client_from_env(**kwargs: Any) -> redis.Redis:
    """Build a Redis client from REDIS_URL or REDIS_HOST/PORT/DB env vars."""
    import redis as _redis

    url = os.environ.get("REDIS_URL")
    if url:
        return _redis.Redis.from_url(url, **kwargs)

    host = os.environ.get("REDIS_HOST", "localhost")
    port = int(os.environ.get("REDIS_PORT", "6379"))
    db = int(os.environ.get("REDIS_DB", "0"))
    return _redis.Redis(host=host, port=port, db=db, **kwargs)


# ---------------------------------------------------------------------------
# Serialization helpers
# ---------------------------------------------------------------------------


def _to_serializable(obj: Any) -> Any:
    """Recursively convert dataclasses/enums/bytes into plain JSON values."""
    if isinstance(obj, Enum):
        return obj.name
    if isinstance(obj, bytes):
        return {"__bytes__": base64.b64encode(obj).decode("ascii")}
    if is_dataclass(obj) and not isinstance(obj, type):
        return {
            "__dataclass__": type(obj).__name__,
            **{f.name: _to_serializable(getattr(obj, f.name)) for f in fields(obj)},
        }
    if isinstance(obj, dict):
        return {k: _to_serializable(v) for k, v in obj.items()}
    if isinstance(obj, (list, tuple)):
        return [_to_serializable(v) for v in obj]
    return obj


def _from_serializable(obj: Any) -> Any:
    """Inverse of ``_to_serializable`` for the dataclasses we care about."""
    if isinstance(obj, dict):
        if "__bytes__" in obj:
            return base64.b64decode(obj["__bytes__"])
        cls_name = obj.pop("__dataclass__", None)
        if cls_name:
            return _dataclass_from_dict(cls_name, obj)
        return {k: _from_serializable(v) for k, v in obj.items()}
    if isinstance(obj, list):
        return [_from_serializable(v) for v in obj]
    return obj


def _ensure_instance(value: Any, cls: type, factory: Any) -> Any:
    return value if isinstance(value, cls) else factory(value)


def _dataclass_from_dict(cls_name: str, data: dict[str, Any]) -> Any:
    data = _from_serializable(data)
    from egregore.domain.units import DT, TU

    if cls_name == "DT":
        return _ensure_instance(data["value"], DT, DT)
    if cls_name == "TU":
        return _ensure_instance(
            {"value": data["value"], "tau_max_ns": data.get("tau_max_ns", 10_000_000)},
            TU,
            lambda d: TU(**d),
        )
    if cls_name == "ResourceProfile":
        return ResourceProfile(**data)
    if cls_name == "NodeCapability":
        data["resource_profile"] = _ensure_instance(
            data["resource_profile"], ResourceProfile, lambda d: ResourceProfile(**d)
        )
        return NodeCapability(**data)
    if cls_name == "SLA":
        data["class_"] = SLAClass(data["class_"])
        return SLA(**data)
    if cls_name == "Job":
        data["priority_tier"] = PriorityTier(data["priority_tier"])
        data["sla"] = _ensure_instance(data["sla"], SLA, lambda d: SLA(**d))
        return Job(**data)
    if cls_name == "WorkUnitDemand":
        data["dt"] = _ensure_instance(data["dt"], DT, DT)
        data["tu"] = _ensure_instance(
            data["tu"],
            TU,
            lambda v: TU(v["value"], v.get("tau_max_ns", 10_000_000)),
        )
        return WorkUnitDemand(**data)
    if cls_name == "WorkUnit":
        data["work_unit_type"] = WorkUnitType[data["work_unit_type"]]
        data["demand"] = _ensure_instance(
            data["demand"], WorkUnitDemand, lambda d: WorkUnitDemand(**d)
        )
        data["state"] = WorkUnitState[data["state"]]
        return WorkUnit(**data)
    raise ValueError(f"Unknown dataclass {cls_name}")


def _serialize(obj: Any) -> str:
    return canonical_dumps(obj, default=_to_serializable)


def _deserialize(text: str | bytes) -> Any:
    if isinstance(text, bytes):
        text = text.decode("utf-8")
    return _from_serializable(canonical_loads(text))


# ---------------------------------------------------------------------------
# Node store
# ---------------------------------------------------------------------------


class RedisNodeStore(INodeStore):
    """Redis-backed implementation of ``INodeStore``.

    Each node is stored as a JSON string under ``egregore:node:{node_id}``.
    The set ``egregore:nodes:ids`` tracks known node ids. Node records have a
    TTL that is refreshed on every heartbeat/upsert.
    """

    KEY_PREFIX = "egregore:node"
    ID_SET_KEY = "egregore:nodes:ids"

    def __init__(
        self,
        redis_client: redis.Redis,
        ttl_seconds: int = 3600,
    ) -> None:
        self._r = redis_client
        self._ttl = ttl_seconds

    def _node_key(self, node_id: str) -> str:
        return f"{self.KEY_PREFIX}:{node_id}"

    def upsert(self, node: NodeCapability) -> None:
        key = self._node_key(node.node_id)
        self._r.set(key, _serialize(node), ex=self._ttl)
        self._r.sadd(self.ID_SET_KEY, node.node_id)

    def get(self, node_id: str) -> NodeCapability | None:
        raw = self._r.get(self._node_key(node_id))
        if raw is None:
            return None
        return cast(NodeCapability, _deserialize(raw))

    def get_all(self) -> list[NodeCapability]:
        node_ids_raw = self._r.smembers(self.ID_SET_KEY)
        if not node_ids_raw:
            return []
        node_ids: list[str] = [
            nid.decode() if isinstance(nid, bytes) else nid for nid in node_ids_raw
        ]
        pipe = self._r.pipeline()
        for nid in node_ids:
            pipe.get(self._node_key(nid))
        results = pipe.execute()
        nodes: list[NodeCapability] = []
        stale: list[str] = []
        for nid, raw in zip(node_ids, results, strict=False):
            if raw is None:
                stale.append(nid)
                continue
            try:
                nodes.append(cast(NodeCapability, _deserialize(raw)))
            except Exception:
                logger.exception("Failed to deserialize node %s", nid)
                stale.append(nid)
        if stale:
            self._r.srem(self.ID_SET_KEY, *stale)
        return nodes

    def get_by_capability(self, capability: str) -> list[NodeCapability]:
        return [
            n
            for n in self.get_all()
            if capability in n.capabilities and n.status == "ACTIVE"
        ]

    def get_active(self, cutoff_ticks: int) -> list[NodeCapability]:
        return [
            n
            for n in self.get_all()
            if n.status == "ACTIVE" and n.last_heartbeat_ns >= cutoff_ticks
        ]

    def deprecate(self, node_id: str) -> bool:
        node = self.get(node_id)
        if node is None:
            return False
        from dataclasses import replace

        self.upsert(replace(node, status="OFFLINE"))
        return True


class NodeTrustStore:
    """Redis-backed trust evidence and cooldown state for ``NodeRegistry``."""

    EVIDENCE_KEY = "egregore:node:{node_id}:evidence"
    COOLDOWN_KEY = "egregore:node:{node_id}:cooldown"
    MAX_EVIDENCE = 100

    def __init__(self, redis_client: redis.Redis) -> None:
        self._r = redis_client

    def record_evidence(self, node_id: str, success: bool, duration_ms: int) -> None:
        key = self.EVIDENCE_KEY.format(node_id=node_id)
        entry = (success, duration_ms)
        # Store capped list on the left, trim to MAX_EVIDENCE.
        self._r.lpush(key, _serialize(entry))
        self._r.ltrim(key, 0, self.MAX_EVIDENCE - 1)

    def get_evidence(self, node_id: str) -> list[tuple[bool, int]]:
        key = self.EVIDENCE_KEY.format(node_id=node_id)
        raw_entries = self._r.lrange(key, 0, -1)
        evidence: list[tuple[bool, int]] = []
        for raw in raw_entries:
            try:
                evidence.append(_deserialize(raw))
            except Exception:
                logger.exception("Failed to deserialize evidence for %s", node_id)
        return evidence

    def cooldown(self, node_id: str, ttl_seconds: int = 60) -> None:
        key = self.COOLDOWN_KEY.format(node_id=node_id)
        self._r.set(key, "1", ex=ttl_seconds)

    def is_cooldown(self, node_id: str) -> bool:
        key = self.COOLDOWN_KEY.format(node_id=node_id)
        return bool(self._r.exists(key))


# ---------------------------------------------------------------------------
# Job store
# ---------------------------------------------------------------------------


class RedisJobStore(IJobStore):
    """Redis-backed implementation of ``IJobStore``.

    Jobs are stored in a Redis hash per tenant:
    ``egregore:jobs:tenant:{tenant_id}`` -> { job_id: json }
    """

    def __init__(self, redis_client: redis.Redis) -> None:
        self._r = redis_client

    def _tenant_key(self, tenant_id: str) -> str:
        return f"egregore:jobs:tenant:{tenant_id}"

    def insert(self, job: Job) -> bool:
        key = self._tenant_key(job.tenant_id)
        if self._r.hexists(key, job.job_id):
            return False
        self._r.hset(key, job.job_id, _serialize(job))
        return True

    def fetch_pending(self, tenant_id: str, limit: int) -> list[Job]:
        key = self._tenant_key(tenant_id)
        jobs: list[Job] = []
        for raw in self._r.hvals(key):
            try:
                job = _deserialize(raw)
                if job.status == "PENDING":
                    jobs.append(job)
            except Exception:
                logger.exception("Failed to deserialize job for tenant %s", tenant_id)
        jobs.sort(key=lambda j: (j.urgency_score, j.job_id))
        return jobs[:limit]

    def update_status(self, job_id: str, status: str, node_id: str = "") -> bool:
        # We do not know the tenant from job_id alone, so scan all tenant hashes.
        for tenant_key in self._r.scan_iter(match="egregore:jobs:tenant:*"):
            tenant_key = (
                tenant_key.decode() if isinstance(tenant_key, bytes) else tenant_key
            )
            raw = self._r.hget(tenant_key, job_id)
            if raw is None:
                continue
            try:
                job = _deserialize(raw)
            except Exception:
                logger.exception("Failed to deserialize job %s", job_id)
                return False
            from dataclasses import replace

            new_job = replace(
                job,
                status=status,
                assigned_node_id=node_id or job.assigned_node_id,
            )
            self._r.hset(tenant_key, job_id, _serialize(new_job))
            return True
        return False

    def count_by_status(self, tenant_id: str) -> dict[str, int]:
        key = self._tenant_key(tenant_id)
        counts: dict[str, int] = {}
        for raw in self._r.hvals(key):
            try:
                job = _deserialize(raw)
                counts[job.status] = counts.get(job.status, 0) + 1
            except Exception:
                logger.exception("Failed to deserialize job for tenant %s", tenant_id)
        return counts

    def oldest_pending(self, tenant_id: str) -> int:
        key = self._tenant_key(tenant_id)
        oldest = 0
        for raw in self._r.hvals(key):
            try:
                job = _deserialize(raw)
                if job.status == "PENDING" and (
                    oldest == 0 or job.created_at_ns < oldest
                ):
                    oldest = job.created_at_ns
            except Exception:
                logger.exception("Failed to deserialize job for tenant %s", tenant_id)
        return oldest


# ---------------------------------------------------------------------------
# Retry buffer
# ---------------------------------------------------------------------------


class RedisRetryBuffer:
    """Sorted-set retry queue keyed by next-retry timestamp (ns)."""

    KEY = "egregore:retry_queue"

    def __init__(self, redis_client: redis.Redis) -> None:
        self._r = redis_client

    def schedule(self, job_id: str, next_retry_ns: int) -> None:
        self._r.zadd(self.KEY, {job_id: next_retry_ns})

    def pop_due(self, now_ns: int, limit: int = 100) -> list[str]:
        due = self._r.zrangebyscore(self.KEY, 0, now_ns, start=0, num=limit)
        if due:
            self._r.zrem(self.KEY, *due)
        decoded: list[str] = []
        for d in due:
            if isinstance(d, bytes):
                decoded.append(d.decode())
            elif isinstance(d, str):
                decoded.append(d)
            else:
                decoded.append(str(d))
        return decoded

    def count(self) -> int:
        return int(self._r.zcard(self.KEY))


# ---------------------------------------------------------------------------
# Admission backlog
# ---------------------------------------------------------------------------


class RedisAdmissionBacklog:
    """Redis-backed admitted-work-unit backlog."""

    LIST_KEY = "egregore:admission:backlog"
    UNIT_KEY = "egregore:work_unit:{work_unit_id}"

    def __init__(self, redis_client: redis.Redis) -> None:
        self._r = redis_client

    def append(self, work_unit: WorkUnit) -> None:
        self._r.rpush(self.LIST_KEY, work_unit.work_unit_id)
        self._r.set(
            self.UNIT_KEY.format(work_unit_id=work_unit.work_unit_id),
            _serialize(work_unit),
        )

    def list_units(self) -> list[WorkUnit]:
        ids = self._r.lrange(self.LIST_KEY, 0, -1)
        units: list[WorkUnit] = []
        ids = [i.decode() if isinstance(i, bytes) else i for i in ids]
        pipe = self._r.pipeline()
        for wid in ids:
            pipe.get(self.UNIT_KEY.format(work_unit_id=wid))
        results = pipe.execute()
        for wid, raw in zip(ids, results, strict=False):
            if raw is None:
                continue
            try:
                units.append(_deserialize(raw))
            except Exception:
                logger.exception("Failed to deserialize work unit %s", wid)
        return units

    def remove(self, work_unit: WorkUnit) -> None:
        self._r.lrem(self.LIST_KEY, 0, work_unit.work_unit_id)
        self._r.delete(self.UNIT_KEY.format(work_unit_id=work_unit.work_unit_id))


# ---------------------------------------------------------------------------
# Factory with graceful fallback
# ---------------------------------------------------------------------------


def create_orchestration_stores(
    redis_client: redis.Redis | None = None,
) -> (
    tuple[
        INodeStore, IJobStore, NodeTrustStore, RedisRetryBuffer, RedisAdmissionBacklog
    ]
    | None
):
    """Return Redis-backed stores, or ``None`` if Redis is unavailable.

    Callers should fall back to the in-memory implementations when this returns
    ``None`` and log a warning.
    """
    try:
        if redis_client is None:
            redis_client = redis_client_from_env()
        redis_client.ping()
    except Exception as exc:
        logger.warning(
            "Redis unavailable, orchestration stores will use memory: %s", exc
        )
        return None

    return (
        RedisNodeStore(redis_client),
        RedisJobStore(redis_client),
        NodeTrustStore(redis_client),
        RedisRetryBuffer(redis_client),
        RedisAdmissionBacklog(redis_client),
    )
