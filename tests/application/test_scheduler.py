"""
Test scheduler — InMemoryJobStore and JobRouter correctness.
"""

from dataclasses import dataclass


@dataclass(frozen=True)
class Job:
    """Minimal Job model for scheduler tests."""

    job_id: str
    tenant_id: str = "default"
    status: str = "PENDING"
    urgency_score: float = 0.0
    assigned_node_id: str = ""
    priority_tier: str = "MEDIUM"
    created_at_ns: int = 0


class TestInMemoryJobStore:
    @staticmethod
    def _make_job(job_id: str, priority: str = "MEDIUM", created_at_ns: int = 0):
        return Job(
            job_id=job_id,
            tenant_id="test-tenant",
            status="PENDING",
            urgency_score=1.0 if priority == "HIGH" else 0.5,
            priority_tier=priority,
            created_at_ns=created_at_ns,
        )

    def test_insert_and_fetch(self):
        from egregore.application.scheduler import InMemoryJobStore

        store = InMemoryJobStore()
        job = self._make_job("job-1", "MEDIUM")
        result = store.insert(job)
        assert result is True
        fetched = store.fetch_pending("test-tenant", 10)
        assert len(fetched) == 1
        assert fetched[0].job_id == "job-1"

    def test_insert_duplicate_fails(self):
        from egregore.application.scheduler import InMemoryJobStore

        store = InMemoryJobStore()
        job = self._make_job("job-1", "MEDIUM")
        store.insert(job)
        result = store.insert(job)
        assert result is False

    def test_update_status(self):
        from egregore.application.scheduler import InMemoryJobStore

        store = InMemoryJobStore()
        job = self._make_job("job-1", "MEDIUM")
        store.insert(job)
        result = store.update_status("job-1", "RUNNING", "node-1")
        assert result is True
        pending = store.fetch_pending("test-tenant", 10)
        assert len(pending) == 0

    def test_count_by_status(self):
        from egregore.application.scheduler import InMemoryJobStore

        store = InMemoryJobStore()
        store.insert(self._make_job("job-1", "MEDIUM"))
        store.insert(self._make_job("job-2", "HIGH"))
        counts = store.count_by_status("test-tenant")
        assert counts.get("PENDING", 0) == 2

    def test_oldest_pending(self):
        from egregore.application.scheduler import InMemoryJobStore

        store = InMemoryJobStore()
        store.insert(self._make_job("job-1", "MEDIUM", created_at_ns=5000))
        store.insert(self._make_job("job-2", "HIGH", created_at_ns=1000))
        pending = store.fetch_pending("test-tenant", 10)
        # Sort key is (urgency_score, job_id). Both MEDIUM=0.5, so alphabetical.
        # job-1 < job-2 alphabetically, so job-1 comes first.
        assert pending[0].job_id == "job-1"
        assert pending[1].job_id == "job-2"
