"""Reconstruct email/message threads from artifact metadata."""

from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any

from egregore.domain.self_rep_dossier.actor_classifier import ActorRegistry
from egregore.domain.self_rep_dossier.dossier_models import (
    Artifact,
    Thread,
    ThreadMessage,
)

# Regex to strip Re:/Fwd:/ etc. from subjects for normalization.
_SUBJECT_PREFIX_RE = re.compile(
    r"^(\s*(re|fwd|fw|ré|re:|fwd:|fw:|ré:|transféré\s*:|transfer)\s*[:\-]*)+", re.I
)


def _normalize_subject(subject: str) -> str:
    """Remove reply/forward prefixes and whitespace for thread grouping."""
    subject = (subject or "").strip()
    subject = _SUBJECT_PREFIX_RE.sub("", subject)
    return " ".join(subject.split()).lower()


def _message_id_from_headers(headers: dict[str, Any]) -> str:
    """Extract a stable message id from email headers if available."""
    raw = (
        headers.get("message_id")
        or headers.get("Message-Id")
        or headers.get("Message-ID")
    )
    if raw:
        return str(raw).strip()
    return ""


def _references_from_headers(headers: dict[str, Any]) -> list[str]:
    """Extract References / In-Reply-To headers as a list."""
    refs: list[str] = []
    for key in ("references", "References", "in_reply_to", "In-Reply-To"):
        val = headers.get(key)
        if not val:
            continue
        if isinstance(val, list):
            refs.extend(str(v).strip() for v in val if v)
        elif isinstance(val, str):
            # References can be space-separated message ids.
            refs.extend(v.strip() for v in val.split() if v.strip())
    return refs


def _thread_id_for_subject(subject: str) -> str:
    norm = _normalize_subject(subject)
    if not norm:
        return "thread:empty_subject"
    h = hashlib.sha256(norm.encode("utf-8")).hexdigest()[:16]
    return f"thread:{h}"


@dataclass
class ThreadBuilder:
    """Build email threads from artifacts with email metadata."""

    registry: ActorRegistry
    threads: dict[str, list[ThreadMessage]] = field(default_factory=dict)
    subjects: dict[str, str] = field(default_factory=dict)

    def add_artifact(self, artifact: Artifact) -> None:
        """Add an email artifact to the thread builder."""
        if artifact.modality != "email":
            return

        metadata = artifact.metadata
        subject = metadata.get("subject") or "(no subject)"
        thread_id = _thread_id_for_subject(subject)

        actor_id = self.registry.classify_artifact(artifact)

        headers = metadata.get("eml_headers") or {}
        references = _references_from_headers(headers)
        in_reply_to = [
            str(headers[k]).strip()
            for k in ("in_reply_to", "In-Reply-To")
            if headers.get(k)
        ]

        message = ThreadMessage(
            artifact_id=artifact.artifact_id,
            actor_id=actor_id,
            timestamp=artifact.timestamp,
            subject=subject,
            in_reply_to=tuple(in_reply_to),
            references=tuple(references),
            body_excerpt=artifact.content_text[:800] if artifact.content_text else "",
        )

        self.threads.setdefault(thread_id, []).append(message)
        self.subjects[thread_id] = subject

        # Also link threads by references: if this message references another
        # thread's message id, merge them under the referenced thread id.
        if references:
            referenced_thread = self._find_thread_by_message_id(references[0])
            if referenced_thread and referenced_thread != thread_id:
                # For now keep separate but store relationship in metadata.
                # A full merge would require re-keying; we keep it simple.
                pass

    def _find_thread_by_message_id(self, message_id: str) -> str | None:
        """Find which thread contains a given message id."""
        message_id = message_id.strip()
        for _thread_id, messages in self.threads.items():
            for _msg in messages:
                # We don't store message_id in ThreadMessage currently.
                pass
        return None

    def build_threads(self) -> tuple[Thread, ...]:
        """Return sorted Thread objects."""
        out: list[Thread] = []
        for thread_id, messages in self.threads.items():
            sorted_messages = sorted(
                messages,
                key=lambda m: (m.timestamp or datetime.min.replace(tzinfo=UTC)),
            )
            participants = tuple(sorted({m.actor_id for m in sorted_messages}))
            initiator = sorted_messages[0].actor_id if sorted_messages else ""
            out.append(
                Thread(
                    thread_id=thread_id,
                    subject=self.subjects.get(thread_id, "(no subject)"),
                    messages=tuple(sorted_messages),
                    participants=participants,
                    initiator=initiator,
                )
            )
        return tuple(sorted(out, key=lambda t: t.thread_id))
