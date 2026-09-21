"""
GGUF Model Catalog — canonical directory structure and metadata tracking.

The catalog is a filesystem mirror: ``scan()`` walks ``GGUF_ROOT`` for
``.gguf`` files and keys entries by their raw relative path (no extension,
forward slashes). Filename-derived metadata (quantisation, parameters) is
informational only — logical model IDs live in ``config/model_profiles.json``
and map to these raw catalog keys.

Legacy model IDs (pre-mirror naming) still resolve through ``_LEGACY_ALIASES``
so existing consumers (EgregoreModelHost, chat interpreter) keep working.
"""

import contextlib
import hashlib
import json
import os
import re
import time
from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime
from pathlib import Path

MODELS_ROOT = Path(
    os.environ.get(
        "EGREGORE_MODELS_ROOT",
        os.environ.get("MODELS_DIR", "/opt/egregore/models"),
    )
)
GGUF_ROOT = MODELS_ROOT / "gguf"
EXPERT_DIR = GGUF_ROOT / "expert"
GENERAL_DIR = GGUF_ROOT / "general"
SPECIALIZED_DIR = GGUF_ROOT / "specialized"
CATALOG_FILE = GGUF_ROOT / ".catalog.json"

# Pre-mirror model IDs -> raw catalog keys. get() resolves both.
_LEGACY_ALIASES = {
    "my-coder-ft": "specialized/my_coder_ft_fixed-Q4_K_M",
    "qwen2.5-7b-instruct": "expert/Qwen2.5-7B-Instruct-Q4_K_M",
    "deepseek-coder-6.7b-instruct": "specialized/deepseek-coder-6.7b-instruct.Q4_K_M",
    "qwen2.5-1.5b-instruct": "general/qwen2.5-1.5b-instruct-q4_k_m",
}


@dataclass
class GGUFEntry:
    model_id: str
    filename: str
    tier: str  # expert | general | specialized
    quantization: str  # Q4_K_M, Q5_K_M, Q8_0, etc.
    parameters: str  # e.g. "7B", "13B", "70B"
    size_bytes: int
    sha256: str
    capabilities: list[str] = field(default_factory=list)
    installed_at: str = ""
    last_verified: str = ""

    @property
    def file_path(self) -> str:
        return str(GGUF_ROOT / self.tier / self.filename)


def _derive_metadata(filepath: Path) -> dict:
    """Extract quantisation/parameters from a filename for display only."""
    name = filepath.stem
    # Quantisation: match anywhere, not anchored to end. Longest forms first
    # (Q4_K_M before Q4_K before Q8_0).
    quant_match = re.search(
        r"[-._]([qQ]\d+_[kKmM](?:_[sSmM])?|[qQ]\d+_\d+|[fF](?:16|32))", name
    )
    quant = quant_match.group(1).upper() if quant_match else "unknown"

    param_match = re.search(r"(\d+[BbMm])", name)
    params = param_match.group(1) if param_match else "unknown"

    return {
        "size_bytes": filepath.stat().st_size,
        "quantization": quant,
        "parameters": params,
    }


def _raw_key(gguf_path: Path) -> str:
    """Raw catalog key: path relative to GGUF_ROOT, no extension, fwd slashes."""
    rel = gguf_path.relative_to(GGUF_ROOT)
    return str(rel.with_suffix("")).replace("\\", "/")


class GGUFCatalog:
    def __init__(self):
        self._ensure_dirs()
        self._entries: dict[str, GGUFEntry] = {}
        self._load()

    def _ensure_dirs(self):
        for d in [MODELS_ROOT, GGUF_ROOT, EXPERT_DIR, GENERAL_DIR, SPECIALIZED_DIR]:
            try:
                d.mkdir(parents=True, mode=0o755, exist_ok=True)
            except OSError:
                # Read-only or restricted namespace; ignore if directory exists.
                if not d.exists():
                    raise

    # ------------------------------------------------------------ scan/load
    def scan(self) -> dict[str, GGUFEntry]:
        """Mirror the filesystem: every .gguf under GGUF_ROOT, raw-path keyed."""
        found: dict[str, GGUFEntry] = {}
        if not GGUF_ROOT.exists():
            return found
        for gguf_path in sorted(GGUF_ROOT.rglob("*.gguf")):
            if gguf_path.suffix != ".gguf" or not gguf_path.is_file():
                continue
            key = _raw_key(gguf_path)
            meta = _derive_metadata(gguf_path)
            found[key] = GGUFEntry(
                model_id=key,
                filename=gguf_path.name,
                tier=gguf_path.relative_to(GGUF_ROOT).parts[0],
                quantization=meta["quantization"],
                parameters=meta["parameters"],
                size_bytes=meta["size_bytes"],
                sha256="",  # hashed lazily by verify_all()
                installed_at=datetime.fromtimestamp(
                    gguf_path.stat().st_mtime, tz=UTC
                ).isoformat(),
            )
        return found

    def _load(self):
        """Load catalog; tolerate v1 format, corruption, and stale entries.

        Valid entries whose file has disappeared are pruned; any on-disk
        .gguf not yet catalogued is merged in via scan(). Corrupt or
        unreadable JSON falls through to a pure filesystem mirror.
        """
        changed = self._load_file()
        changed |= self._prune_missing()
        changed |= self._migrate_legacy_keys()
        changed |= self._merge_scan()
        if changed or not CATALOG_FILE.exists():
            self._save()

    def _load_file(self) -> bool:
        """Read the catalog JSON into _entries. Returns True if corrupt."""
        if not (CATALOG_FILE.exists() and CATALOG_FILE.stat().st_size > 0):
            return False
        try:
            with open(CATALOG_FILE) as f:
                raw = json.load(f)
            entries = raw.get("entries", {}) if isinstance(raw, dict) else {}
            for k, v in entries.items():
                if isinstance(v, dict):
                    with contextlib.suppress(TypeError):
                        self._entries[k] = GGUFEntry(**v)
        except (json.JSONDecodeError, OSError):
            self._entries = {}
            return True
        return False

    def _prune_missing(self) -> bool:
        """Drop entries whose file no longer exists (stale registrations)."""
        changed = False
        for key in list(self._entries):
            entry = self._entries[key]
            if not (GGUF_ROOT / entry.tier / entry.filename).exists():
                del self._entries[key]
                changed = True
        return changed

    def _migrate_legacy_keys(self) -> bool:
        """Re-key v1 legacy-model-ID entries to raw path keys."""
        changed = False
        for legacy, raw in _LEGACY_ALIASES.items():
            entry = self._entries.pop(legacy, None)
            if entry is None:
                continue
            changed = True
            entry.model_id = raw
            existing = self._entries.get(raw)
            if existing is None or (not existing.sha256 and entry.sha256):
                self._entries[raw] = entry
        return changed

    def _merge_scan(self) -> bool:
        """Add new on-disk files; refresh informational metadata for known
        files (sha256 is preserved).
        """
        changed = False
        for key, scanned in self.scan().items():
            existing = self._entries.get(key)
            if existing is None:
                self._entries[key] = scanned
                changed = True
            elif (
                existing.size_bytes != scanned.size_bytes
                or existing.quantization != scanned.quantization
                or existing.parameters != scanned.parameters
            ):
                existing.size_bytes = scanned.size_bytes
                existing.quantization = scanned.quantization
                existing.parameters = scanned.parameters
                changed = True
        return changed

    def _save(self):
        # Security: refuse to write outside the resolved models root.
        root = MODELS_ROOT.resolve()
        target = CATALOG_FILE.resolve()
        if not MODELS_ROOT.is_absolute() or not str(target).startswith(str(root) + os.sep):
            raise ValueError(
                f"Refusing to write catalog outside models root: {target}"
            )
        raw = {
            "meta": {
                "version": "2.0",
                "node": os.environ.get("EGREGORE_NODE_ID", "pioneer1"),
                "last_updated": datetime.now(tz=UTC).isoformat(),
            },
            "entries": {k: asdict(v) for k, v in self._entries.items()},
        }
        try:
            tmp = CATALOG_FILE.with_suffix(".json.tmp")
            with open(tmp, "w") as f:
                json.dump(raw, f, indent=2)
            os.replace(tmp, CATALOG_FILE)
        except OSError:
            # In a restricted service namespace (e.g. systemd ProtectSystem)
            # the catalog may be read-only. Verification should still succeed.
            pass

    # ------------------------------------------------------------ registry
    def register(self, entry: GGUFEntry) -> None:
        entry.installed_at = datetime.fromtimestamp(time.time_ns() / 1e9, tz=UTC).isoformat()
        entry.last_verified = entry.installed_at
        self._entries[entry.model_id] = entry
        self._save()

    def get(self, model_id: str) -> GGUFEntry | None:
        """Resolve by raw catalog key or legacy model ID."""
        entry = self._entries.get(model_id)
        if entry is None and model_id in _LEGACY_ALIASES:
            entry = self._entries.get(_LEGACY_ALIASES[model_id])
        return entry

    def entries(self) -> dict[str, GGUFEntry]:
        """Public read-only view of catalog entries (raw-path keyed)."""
        return dict(self._entries)

    def get_catalog(self) -> dict[str, dict]:
        """Raw-key -> metadata mapping for the ModelSelector manifest."""
        return {
            key: {
                "file_path": entry.file_path,
                "size_bytes": entry.size_bytes,
                "quantization": entry.quantization,
                "parameters": entry.parameters,
            }
            for key, entry in self._entries.items()
        }

    def list_models(self) -> list[str]:
        """Return registered model IDs."""
        return list(self._entries.keys())

    def list_by_tier(self, tier: str) -> list[GGUFEntry]:
        return [e for e in self._entries.values() if e.tier == tier]

    def verify_all(self) -> dict[str, str]:
        results = {}
        for model_id, entry in self._entries.items():
            target_path = GGUF_ROOT / entry.tier / entry.filename
            if not target_path.exists():
                results[model_id] = "MISSING"
                continue
            if not entry.sha256:
                # Scanned entries are hashed once, then cached in the catalog.
                entry.sha256 = self._hash(target_path)
                entry.last_verified = datetime.fromtimestamp(
                    time.time_ns() / 1e9, tz=UTC
                ).isoformat()
                results[model_id] = "VERIFIED"
                continue
            if self._hash(target_path) == entry.sha256:
                results[model_id] = "VERIFIED"
                entry.last_verified = datetime.fromtimestamp(
                    time.time_ns() / 1e9, tz=UTC
                ).isoformat()
            else:
                results[model_id] = "CORRUPT"
        with contextlib.suppress(OSError):
            self._save()
        return results

    @staticmethod
    def _hash(path: Path) -> str:
        h = hashlib.sha256()
        with open(path, "rb") as f:
            for chunk in iter(lambda: f.read(8192), b""):
                h.update(chunk)
        return h.hexdigest()

    def health_check(self) -> dict:
        total = len(self._entries)
        missing = sum(
            1
            for e in self._entries.values()
            if not (GGUF_ROOT / e.tier / e.filename).exists()
        )
        return {
            "status": "HEALTHY" if missing == 0 else "DEGRADED",
            "total_models": total,
            "missing": missing,
            "catalog_path": str(CATALOG_FILE),
            "tiers": {
                "expert": len(self.list_by_tier("expert")),
                "general": len(self.list_by_tier("general")),
                "specialized": len(self.list_by_tier("specialized")),
            },
        }


def run_gguf_health_check() -> dict:
    """ANCHORUM hook."""
    catalog = GGUFCatalog()
    return catalog.health_check()
