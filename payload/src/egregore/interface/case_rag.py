"""Per-case RAG index for ANCHORUM cases.

Each case gets its own isolated Chroma store under ``rag/cases/{case_id}/`` —
case-level containment: indexing, querying, and deleting one case never
touches another's vectors. This is the retrieval layer behind the Legal
Dossier chat: without it the LLM answers from training data only.

Indexed content (whatever exists on disk for the case):
- report findings / entities / summary counts (from the report JSON)
- audio/video transcripts (the batch runner's ``transcripts/`` sidecars,
  labelled with their original filename via the report's audio section)
- staged evidence fetched by the desktop Fetch/Email tabs (``fetched/``)

Deletion of a case removes its vector store along with the report files.
"""

from __future__ import annotations

import contextlib
import json
import logging
import os
import shutil
import threading
from pathlib import Path
from typing import Any

from egregore.shared.paths import repo_root

logger = logging.getLogger("egregore.case_rag")

_CHUNK_CHARS = int(os.environ.get("EGREGORE_CASE_RAG_CHUNK_CHARS", "1800"))
_CHUNK_OVERLAP = int(os.environ.get("EGREGORE_CASE_RAG_CHUNK_OVERLAP", "100"))
_TEXT_SUFFIXES = {".txt", ".md", ".csv", ".json", ".eml", ".html", ".htm", ".log"}
_MAX_SOURCE_BYTES = 2 * 1024 * 1024

_index_lock = threading.Lock()


def _sha256_file(path: Path) -> str:
    """Return the SHA-256 hex digest of a file."""
    import hashlib

    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(8192), b""):
            h.update(chunk)
    return h.hexdigest()


def _extra_dirs_path(case_id: str) -> Path:
    from egregore.interface.anchorum_router import _report_dir

    return _report_dir() / f"{case_id}_rag_dirs.json"


def get_extra_dirs(case_id: str) -> list[str]:
    """Return persisted extra evidence directories for a case."""
    path = _extra_dirs_path(case_id)
    if not path.exists():
        return []
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        return [str(d) for d in data if Path(d).is_dir()]
    except Exception:  # noqa: BLE001
        return []


def set_extra_dirs(case_id: str, dirs: list[str]) -> None:
    """Persist a list of extra evidence directories for a case."""
    path = _extra_dirs_path(case_id)
    cleaned = [str(Path(d).resolve()) for d in dirs if Path(d).is_dir()]
    path.write_text(json.dumps(cleaned), encoding="utf-8")


def _cases_root() -> Path:
    override = os.environ.get("EGREGORE_CASE_RAG_ROOT")
    root = Path(override) if override else repo_root() / "rag" / "cases"
    root.mkdir(parents=True, exist_ok=True)
    return root


def case_index_dir(case_id: str) -> Path:
    return _cases_root() / case_id


def delete_case_index(case_id: str) -> bool:
    """Remove a case's vector store. Returns True if something was removed."""
    target = case_index_dir(case_id)
    if target.exists():
        shutil.rmtree(target)
        return True
    return False


def _get_embedder():
    # Share the process-wide embedder with the global RAG API.
    from egregore.interface.rag_api import _get_embedder as _shared

    return _shared()


def _chunk(text: str, size: int = _CHUNK_CHARS, overlap: int = _CHUNK_OVERLAP) -> list[str]:
    """Split text into overlapping chunks on paragraph/line boundaries."""
    text = text.strip()
    if not text:
        return []
    chunks: list[str] = []
    start = 0
    while start < len(text):
        end = min(start + size, len(text))
        if end < len(text):
            boundary = text.rfind("\n", start + size // 2, end)
            if boundary > start:
                end = boundary
        chunk = text[start:end].strip()
        if chunk:
            chunks.append(chunk)
        start = max(end - overlap, start + 1)
        if start >= len(text) - overlap:
            break
    return chunks


def _gather_case_documents(  # noqa: C901
    case_id: str, extra_dirs: list[str] | None = None
) -> list[dict[str, str]]:
    """Collect {doc_id, source, text} for everything textual a case owns."""
    from egregore.interface.anchorum_router import _load_report, _report_dir

    docs: list[dict[str, str]] = []

    # 1. Report-derived text: findings + entities (compact, high signal).
    try:
        report = _load_report(case_id)
    except Exception as exc:  # noqa: BLE001
        logger.warning("case_rag: report load failed for %s: %s", case_id, exc)
        report = {}
    if report:
        parts: list[str] = [
            f"Case {report.get('case_id', case_id)}: "
            f"{report.get('artifact_count', 0)} artifacts, "
            f"{report.get('entity_count', 0)} entities, "
            f"{report.get('anomaly_count', 0)} anomalies."
        ]
        for sev in ("critical", "high", "medium", "low", "info"):
            for f in report.get(f"{sev}_findings", []) or []:
                if isinstance(f, dict):
                    parts.append(
                        f"[{sev}] {f.get('anomaly_type', '?')}: "
                        f"{f.get('description', '')}"
                    )
        for e in report.get("entity_directory", []) or []:
            if isinstance(e, dict):
                parts.append(
                    "entity: "
                    + str(e.get("value") or e.get("entity_value") or e.get("name") or "")
                )
        docs.append(
            {
                "doc_id": "report",
                "source": f"{case_id}_report.json",
                "source_type": "report",
                "text": "\n".join(parts),
            }
        )
        transcripts_meta = {
            t.get("artifact_id"): t
            for t in report.get("audio_transcripts", []) or []
            if isinstance(t, dict)
        }
    else:
        transcripts_meta = {}

    # 2. Transcript sidecars from the batch work dir.
    tdir = _report_dir() / f"{case_id}_work" / "anchorum_output" / "transcripts"
    if tdir.is_dir():
        for txt in sorted(tdir.glob("*.txt")):
            try:
                text = txt.read_text(encoding="utf-8", errors="replace")
            except OSError:
                continue
            if not text.strip():
                continue
            artifact_id = txt.stem
            label = transcripts_meta.get(artifact_id, {}).get(
                "original_filename"
            ) or f"transcript {artifact_id[:12]}"
            docs.append(
                {
                    "doc_id": f"transcript:{artifact_id}",
                    "source": f"audio transcript: {label}",
                    "source_type": "transcript",
                    "text": text,
                }
            )

    # 3. Staged evidence from the desktop Fetch/Email tabs.
    fetched = repo_root() / "fetched" / case_id
    if fetched.is_dir():
        for path in sorted(fetched.rglob("*")):
            if not path.is_file() or path.suffix.lower() not in _TEXT_SUFFIXES:
                continue
            try:
                if path.stat().st_size > _MAX_SOURCE_BYTES:
                    continue
                text = path.read_text(encoding="utf-8", errors="replace")
            except OSError:
                continue
            if not text.strip():
                continue
            docs.append(
                {
                    "doc_id": f"fetched:{path.relative_to(fetched)}",
                    "source": f"staged evidence: {path.relative_to(fetched)}",
                    "source_type": "fetched",
                    "text": text,
                }
            )

    # 4. Explicitly attached evidence directories (e.g. the dossier's
    # 3_EMAILS / 4_ANALYSIS folders) — text-like files only, size-capped.
    for extra in extra_dirs or []:
        root = Path(extra)
        if not root.is_dir():
            logger.warning("case_rag: extra dir missing for %s: %s", case_id, extra)
            continue
        for path in sorted(root.rglob("*")):
            if not path.is_file() or path.suffix.lower() not in _TEXT_SUFFIXES:
                continue
            try:
                if path.stat().st_size > _MAX_SOURCE_BYTES:
                    continue
                text = path.read_text(encoding="utf-8", errors="replace")
            except OSError:
                continue
            if not text.strip():
                continue
            docs.append(
                {
                    "doc_id": f"evidence:{path}",
                    "source": f"evidence file: {path.name}",
                    "source_type": "evidence",
                    "text": text,
                }
            )

    return docs


def index_case(case_id: str, extra_dirs: list[str] | None = None) -> dict[str, Any]:
    """(Re)build the case's vector store. Returns indexing stats."""
    import chromadb

    if extra_dirs is None:
        extra_dirs = get_extra_dirs(case_id)
    docs = _gather_case_documents(case_id, extra_dirs)
    with _index_lock:
        store = case_index_dir(case_id)
        client = chromadb.PersistentClient(path=str(store))
        # Rebuild from scratch: indexing is cheap and this avoids stale chunks.
        with contextlib.suppress(Exception):  # collection may not exist yet
            client.delete_collection("case")
        collection = client.get_or_create_collection("case")

        ids: list[str] = []
        texts: list[str] = []
        metas: list[dict[str, Any]] = []
        for doc in docs:
            for idx, chunk in enumerate(_chunk(doc["text"])):
                ids.append(f"{doc['doc_id']}#{idx}")
                texts.append(chunk)
                metas.append(
                    {
                        "source": doc["source"],
                        "source_type": doc.get("source_type", "unknown"),
                        "chunk": idx,
                    }
                )

        if texts:
            embedder = _get_embedder()
            embeddings = embedder.encode(texts, show_progress_bar=False).tolist()
            # chroma 1.x caps batch adds; stay well under the limit.
            for off in range(0, len(ids), 4000):
                sl = slice(off, off + 4000)
                collection.add(
                    ids=ids[sl],
                    documents=texts[sl],
                    embeddings=embeddings[sl],
                    metadatas=metas[sl],
                )
    stats = {
        "case_id": case_id,
        "documents": len(docs),
        "chunks": len(ids),
        "extra_dirs": extra_dirs or [],
        "index_dir": str(case_index_dir(case_id)),
    }
    logger.info("case_rag indexed %s: %s", case_id, stats)
    return stats


def _source_descriptor(
    doc_id: str, source_type: str, path: Path, root: Path | None = None
) -> dict[str, Any] | None:
    """Return a source descriptor for a file, or None if it cannot be read."""
    try:
        return {
            "id": doc_id,
            "source_type": source_type,
            "path": str(path),
            "size": path.stat().st_size,
            "sha256": _sha256_file(path),
        }
    except OSError:
        return None


def _append_file_sources(
    sources: list[dict[str, Any]],
    root: Path,
    source_type: str,
    id_prefix: str,
    relative_to: Path | None = None,
) -> None:
    """Append descriptors for every file under ``root`` to ``sources``."""
    if not root.is_dir():
        return
    for path in sorted(root.rglob("*")):
        if not path.is_file():
            continue
        if source_type in ("transcript",) and path.suffix.lower() != ".txt":
            continue
        if relative_to is not None:
            doc_id = f"{id_prefix}:{path.relative_to(relative_to)}"
        else:
            doc_id = f"{id_prefix}:{path}"
        desc = _source_descriptor(doc_id, source_type, path, relative_to)
        if desc:
            sources.append(desc)


def list_case_sources(
    case_id: str, extra_dirs: list[str] | None = None
) -> dict[str, Any]:
    """List all source files that feed a case's vector store.

    Returns descriptors with path, size, sha256, and source_type so the UI can
    show a browsable dossier source tree without re-reading file contents.
    """
    from egregore.interface.anchorum_router import _load_report, _report_dir

    if extra_dirs is None:
        extra_dirs = get_extra_dirs(case_id)

    sources: list[dict[str, Any]] = []

    # 1. Report.
    with contextlib.suppress(Exception):
        _load_report(case_id)
        report_path = _report_dir() / f"{case_id}_report.json"
        desc = _source_descriptor("report", "report", report_path)
        if desc:
            sources.append(desc)

    # 2. Transcript sidecars.
    tdir = _report_dir() / f"{case_id}_work" / "anchorum_output" / "transcripts"
    _append_file_sources(sources, tdir, "transcript", "transcript")

    # 3. Staged fetched evidence.
    fetched = repo_root() / "fetched" / case_id
    _append_file_sources(sources, fetched, "fetched", "fetched", fetched)

    # 4. Attached evidence directories.
    for extra in extra_dirs:
        _append_file_sources(sources, Path(extra), "evidence", "evidence")

    return {
        "case_id": case_id,
        "sources": sources,
        "extra_dirs": extra_dirs,
    }


def index_stats(case_id: str) -> dict[str, Any]:
    """Cheap index status: no re-embed, just collection count + store mtime."""
    store = case_index_dir(case_id)
    stats: dict[str, Any] = {
        "case_id": case_id,
        "indexed": False,
        "chunks": 0,
        "index_dir": str(store),
        "last_indexed": None,
    }
    if not store.exists():
        return stats
    import chromadb

    try:
        client = chromadb.PersistentClient(path=str(store))
        collection = client.get_collection("case")
        stats["chunks"] = collection.count()
        stats["indexed"] = stats["chunks"] > 0
    except Exception:  # noqa: BLE001 — store exists but unreadable/empty
        return stats
    with contextlib.suppress(OSError, ValueError):
        stats["last_indexed"] = max(
            f.stat().st_mtime for f in store.rglob("*") if f.is_file()
        )
    return stats


def query_case(case_id: str, query: str, top_k: int = 4) -> list[dict[str, Any]]:
    """Return top-k chunks with source citations; [] if no index/chunks."""
    import chromadb

    store = case_index_dir(case_id)
    if not store.exists():
        return []
    client = chromadb.PersistentClient(path=str(store))
    try:
        collection = client.get_collection("case")
    except Exception:  # noqa: BLE001
        return []
    if collection.count() == 0:
        return []
    embedder = _get_embedder()
    embedding = embedder.encode(query).tolist()
    results = collection.query(query_embeddings=[embedding], n_results=top_k)
    out: list[dict[str, Any]] = []
    ids = results.get("ids", [[]])[0]
    for i, chunk_id in enumerate(ids):
        meta = (results["metadatas"][0][i] or {}) if results.get("metadatas") else {}
        out.append(
            {
                "id": chunk_id,
                "distance": (
                    results["distances"][0][i] if results.get("distances") else None
                ),
                "document": results["documents"][0][i],
                "source": meta.get("source", "?"),
                "source_type": meta.get("source_type", "unknown"),
            }
        )
    return out
