"""RAG query API for the Egregore knowledge base."""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any

from fastapi import APIRouter
from pydantic import BaseModel, Field
from sentence_transformers import SentenceTransformer

router = APIRouter(prefix="/api/v1/rag", tags=["rag"])

CHROMA_PATH = Path(os.environ.get("BLACKSTAR_REPO_ROOT", "/opt/egregore")) / "rag/chroma_db"
_EMBEDDER: SentenceTransformer | None = None


def _get_embedder() -> SentenceTransformer:
    global _EMBEDDER
    if _EMBEDDER is None:
        _EMBEDDER = SentenceTransformer("all-MiniLM-L6-v2")
    return _EMBEDDER


class RAGQuery(BaseModel):
    query: str = Field(..., min_length=1, max_length=2000)
    top_k: int = Field(default=5, ge=1, le=20)


class RAGResponse(BaseModel):
    query: str
    results: list[dict[str, Any]]


@router.post("/query")
def query_rag(q: RAGQuery) -> RAGResponse:
    """Query the Egregore RAG knowledge base."""
    try:
        import chromadb
    except ImportError as exc:
        raise RuntimeError("chromadb not installed") from exc

    client = chromadb.PersistentClient(path=str(CHROMA_PATH))
    collection = client.get_or_create_collection(name="egregore_knowledge")

    embedder = _get_embedder()
    embedding = embedder.encode(q.query).tolist()
    results = collection.query(query_embeddings=[embedding], n_results=q.top_k)

    out = []
    for i in range(len(results["ids"][0])):
        out.append(
            {
                "id": results["ids"][0][i],
                "distance": (
                    results["distances"][0][i] if results["distances"] else None
                ),
                "document": results["documents"][0][i],
                "metadata": results["metadatas"][0][i] if results["metadatas"] else {},
            }
        )
    return RAGResponse(query=q.query, results=out)


@router.get("/status")
def rag_status() -> dict[str, Any]:
    """Return basic RAG store status."""
    try:
        import chromadb
    except ImportError:
        return {"status": "chromadb_not_installed", "count": 0}

    client = chromadb.PersistentClient(path=str(CHROMA_PATH))
    collection = client.get_or_create_collection(name="egregore_knowledge")
    return {"status": "ready", "count": collection.count(), "path": str(CHROMA_PATH)}
