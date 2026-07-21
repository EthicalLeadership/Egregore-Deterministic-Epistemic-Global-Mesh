"""FastAPI router for /v1/embeddings — local sentence-transformers backend."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter
from pydantic import BaseModel

router = APIRouter(prefix="/v1", tags=["embeddings"])

_EMBEDDER: Any | None = None


def _get_embedder() -> Any:
    """Lazy-load the local embedding model (same model as the RAG store)."""
    global _EMBEDDER
    if _EMBEDDER is None:
        import os

        os.environ.setdefault(
            "HF_HOME", os.environ.get("HF_HOME", "/opt/egregore/cache/huggingface")
        )
        from sentence_transformers import SentenceTransformer

        _EMBEDDER = SentenceTransformer("all-MiniLM-L6-v2", trust_remote_code=True)
    return _EMBEDDER


class EmbeddingRequest(BaseModel):
    model: str
    input: str | list[str]


class EmbeddingObject(BaseModel):
    object: str = "embedding"
    embedding: list[float]
    index: int


class EmbeddingResponse(BaseModel):
    object: str = "list"
    data: list[EmbeddingObject]
    model: str
    usage: dict[str, int]


@router.post("/embeddings", response_model=EmbeddingResponse)
def create_embeddings(req: EmbeddingRequest) -> EmbeddingResponse:
    """Return embeddings for one or more texts using the local sentence-transformers model."""
    model = _get_embedder()
    texts = [req.input] if isinstance(req.input, str) else req.input
    embeddings = model.encode(texts, normalize_embeddings=True, show_progress_bar=False)
    data = [
        EmbeddingObject(embedding=emb.tolist(), index=i)
        for i, emb in enumerate(embeddings)
    ]
    total_tokens = sum(len(t.split()) for t in texts)
    return EmbeddingResponse(
        object="list",
        data=data,
        model=req.model,
        usage={"prompt_tokens": total_tokens, "total_tokens": total_tokens},
    )
