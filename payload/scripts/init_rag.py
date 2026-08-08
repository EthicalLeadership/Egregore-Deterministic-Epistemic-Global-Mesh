#!/usr/bin/env python3
"""Initialize the Egregore RAG knowledge base.

Populates a Chroma vector store with:
- The Universal Cell Schema
- The BCCBP build protocol summary
- Egregore law interpretations
- Best-practice prompt engineering snippets

Run this after installing chromadb + sentence-transformers.
"""
from __future__ import annotations

import hashlib
import sys
from pathlib import Path

from sentence_transformers import SentenceTransformer

ROOT = Path("/opt/egregore")
CHROMA_PATH = ROOT / "rag" / "chroma_db"


def _chunks(text: str, size: int = 500, overlap: int = 50) -> list[str]:
    """Simple sliding-window chunker."""
    words = text.split()
    chunks = []
    start = 0
    while start < len(words):
        end = min(start + size, len(words))
        chunks.append(" ".join(words[start:end]))
        start += size - overlap
    return chunks


def _documents() -> list[dict[str, str]]:
    docs = []

    # 1. Universal Cell Schema
    schema_text = (ROOT / "schemas" / "cell_spec.schema.yaml").read_text(encoding="utf-8")
    docs.append({
        "id": "cell_schema",
        "title": "Universal Cell Schema",
        "source": "schemas/cell_spec.schema.yaml",
        "text": schema_text,
    })

    # 2. BCCBP protocol summary
    protocol_text = """
The Egregore Cognitive Civilization Build Protocol (BCCBP) is an 8-stage artifact-driven
protocol for every cell in the University. Stages are: PLAN, DRAW, LAYOUT, ERECT, BUILD,
FINISH, INSPECT, DELIVER. No stage may be skipped. Each stage produces a mandatory artifact
that is checked into version control and verified by the Ombudsman before the next stage
unlocks. PLAN produces spec.yaml. DRAW produces architecture.dot. LAYOUT produces
deployment.yaml. ERECT produces a live /health/live response. BUILD produces test_report.json
with at least 20 tests. FINISH produces audit_certificate.json. INSPECT produces
acceptance_report.md. DELIVER produces a git tag and shelf entry.
""".strip()
    docs.append({
        "id": "bccbp_protocol",
        "title": "BCCBP Protocol Summary",
        "source": "internal",
        "text": protocol_text,
    })

    # 3. Egregore law interpretations
    law_text = """
Egregore laws governing all cells:
1. Data sovereignty: no unauthorized external network connections.
2. Auditability: every action must be logged with timestamp and provenance.
3. No data exfiltration: cells may only read and write within designated paths.
4. No hardcoded secrets: credentials must be injected via environment or secret store.
5. Safety: cells must refuse requests to generate malware, keyloggers, or harmful code.
6. Transparency: every output must include provenance and model lineage.
""".strip()
    docs.append({
        "id": "egregore_laws",
        "title": "Egregore Law Interpretations",
        "source": "internal",
        "text": law_text,
    })

    # 4. Prompt engineering best practices
    prompt_text = """
Best practices for Egregore cell prompts:
- Use structured output formats (JSON) with explicit field names.
- Keep system prompts task-focused; avoid aggressive personas that trigger refusals.
- Provide examples in prompts when output format matters.
- Break complex generation into stages: spec -> scaffold -> fill -> verify.
- Use deterministic tools (AST, mypy, bandit, pytest) for verification, not LLM critics alone.
- Include path-traversal protection as defensive coding, not offensive security.
- Log every generation event with input hash, output hash, and model identifiers.
""".strip()
    docs.append({
        "id": "prompt_best_practices",
        "title": "Prompt Engineering Best Practices",
        "source": "internal",
        "text": prompt_text,
    })

    return docs


def main() -> int:
    try:
        import chromadb
    except ImportError:
        print("ERROR: chromadb not installed. Run: .venv/bin/pip install chromadb sentence-transformers")
        return 1

    print("Loading embedding model (this may download ~80 MB once)...")
    model = SentenceTransformer("all-MiniLM-L6-v2")

    client = chromadb.PersistentClient(path=str(CHROMA_PATH))
    collection = client.get_or_create_collection(name="egregore_knowledge")

    existing = collection.get()
    existing_ids = set(existing["ids"])

    for doc in _documents():
        chunks = _chunks(doc["text"], size=300, overlap=30)
        for idx, chunk in enumerate(chunks):
            chunk_id = f"{doc['id']}_chunk_{idx}"
            if chunk_id in existing_ids:
                print(f"Skipping existing chunk: {chunk_id}")
                continue
            embedding = model.encode(chunk).tolist()
            collection.add(
                ids=[chunk_id],
                embeddings=[embedding],
                documents=[chunk],
                metadatas=[{
                    "title": doc["title"],
                    "source": doc["source"],
                    "chunk_index": idx,
                    "doc_id": doc["id"],
                    "hash": hashlib.sha256(chunk.encode()).hexdigest(),
                }],
            )
            print(f"Added {chunk_id}")

    print(f"RAG knowledge base initialized at {CHROMA_PATH}")
    print(f"Total documents in collection: {collection.count()}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
