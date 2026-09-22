"""
Consent Registry — tamper-evident authorization records for remote actions.
"""
from __future__ import annotations
import hashlib, json, logging, os
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, List

logger = logging.getLogger(__name__)
DEFAULT_CONSENT_FILE = Path("config/consent_registry.jsonl")

class ConsentRecord:
    def __init__(self, node_id: str, user_id: str, scope: List[str], status: str = "active", timestamp: str | None = None, signature: str = ""):
        self.node_id = node_id
        self.user_id = user_id
        self.scope = scope
        self.status = status
        self.timestamp = timestamp or datetime.now(UTC).isoformat()
        self.signature = signature
    def to_dict(self):
        return {"node_id": self.node_id, "user_id": self.user_id, "scope": self.scope, "status": self.status, "timestamp": self.timestamp, "signature": self.signature}

class ConsentRegistry:
    def __init__(self, file_path=DEFAULT_CONSENT_FILE):
        self.file_path = Path(file_path)
        self.file_path.parent.mkdir(parents=True, exist_ok=True)
        self._records = self._load_existing()
        self._last_hash = ""
        # ensure last hash from file if exists
        if self.file_path.exists():
            with open(self.file_path, "r", encoding="utf-8") as f:
                lines = f.readlines()
                if lines:
                    last = json.loads(lines[-1].strip())
                    self._last_hash = last.get("hash", "")

    def _load_existing(self):
        records = {}
        if not self.file_path.exists():
            return records
        prev_hash = ""
        with open(self.file_path, "r", encoding="utf-8") as f:
            for line_no, line in enumerate(f, 1):
                line = line.strip()
                if not line: continue
                try: entry = json.loads(line)
                except json.JSONDecodeError:
                    logger.error("Corrupt consent ledger at line %d", line_no)
                    raise
                rec_hash = entry.get("hash", "")
                expected = self._chain_hash(entry.get("record", {}), prev_hash)
                if rec_hash != expected:
                    logger.error("Consent ledger tampered at line %d", line_no)
                    raise ValueError("Consent ledger integrity check failed")
                record = entry["record"]
                records[record["node_id"]] = record
                prev_hash = rec_hash
        return records

    def _chain_hash(self, record, prev_hash):
        canonical = json.dumps(record, sort_keys=True, separators=(",", ":"))
        return hashlib.sha256(f"{prev_hash}:{canonical}".encode()).hexdigest()

    def _append(self, record):
        rec_hash = self._chain_hash(record, self._last_hash)
        entry = {"record": record, "hash": rec_hash, "prev_hash": self._last_hash}
        with open(self.file_path, "a", encoding="utf-8") as f:
            f.write(json.dumps(entry) + "\n")
        self._last_hash = rec_hash
        self._records[record["node_id"]] = record

    def register_consent(self, node_id, user_id, scope, status="active", signature=""):
        if node_id in self._records and self._records[node_id]["status"] == "active":
            raise ValueError("Active consent already exists. Revoke before re-registering.")
        record = ConsentRecord(node_id, user_id, scope, status=status, signature=signature)
        self._append(record.to_dict())
        return record

    def revoke_consent(self, node_id):
        if node_id not in self._records:
            raise KeyError(f"No consent record for node {node_id}")
        old = self._records[node_id].copy()
        old["status"] = "revoked"
        old["timestamp"] = datetime.now(UTC).isoformat()
        old["revoked_by"] = "system"
        self._append(old)

    def is_consented(self, node_id, action=None):
        record = self._records.get(node_id)
        if not record or record["status"] != "active":
            return False
        if action is not None:
            return action in record.get("scope", [])
        return True

    def list_consents(self):
        return list(self._records.values())

    def verify_chain(self):
        if not self.file_path.exists():
            return True
        prev_hash = ""
        with open(self.file_path, "r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line: continue
                entry = json.loads(line)
                rec_hash = entry.get("hash", "")
                expected = self._chain_hash(entry["record"], prev_hash)
                if rec_hash != expected:
                    return False
                prev_hash = rec_hash
        return True
