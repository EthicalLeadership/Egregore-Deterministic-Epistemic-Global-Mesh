"""
ANCHORUM Email Metadata Extractor
==================================
Stdlib-only extraction of RFC-822 email metadata.
CBI-0 governed: read-only input, immutable output.

Extracts all 5 metadata planes:
- Container plane: headers, MIME structure, attachments, Received chain
- Application plane: mailer, originating IP
- Content plane: body text, URLs, email addresses, attachment count
- Temporal plane: Date, Received hop timestamps
"""

from __future__ import annotations

import email.utils
import re
from datetime import UTC, datetime
from email.message import Message
from email.parser import BytesParser
from email.policy import default as default_policy
from typing import Any

from anchorum.forensic.core.types import (
    ApplicationMetadata,
    AttachmentRef,
    ContainerMetadata,
    ContentMetadata,
    ExtractedMetadata,
    ReceivedHop,
    TemporalEvent,
    TemporalMetadata,
)

# ---------------------------------------------------------------------------
# 1. Regex helpers
# ---------------------------------------------------------------------------
_URL_RE = re.compile(r"https?://[^\s<>\"{}|\\^`\[\]]+", re.IGNORECASE)
_EMAIL_RE = re.compile(r"[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}")


# ---------------------------------------------------------------------------
# 2. Helpers
# ---------------------------------------------------------------------------
def _parse_email_date(value: str | None) -> datetime | None:
    """Parse an RFC-2822 date string into a UTC-aware datetime."""
    if not value:
        return None
    try:
        parsed = email.utils.parsedate_to_datetime(value.strip())
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=UTC)
        return parsed.astimezone(UTC)
    except (ValueError, TypeError, LookupError):
        return None


def _normalize_addr(raw: str) -> str:
    """Return 'user@example.com' from 'Name <user@example.com>'."""
    parsed = email.utils.parseaddr(raw)
    if parsed[1]:
        return parsed[1]
    return raw.strip()


def _extract_addrs(msg: Message, header: str) -> tuple[str, ...]:
    """Extract normalized email addresses from a comma-separated header."""
    values = msg.get_all(header, [])
    addrs: list[str] = []
    for raw in values:
        for _, addr in email.utils.getaddresses([raw]):
            if addr:
                addrs.append(addr.lower())
    return tuple(sorted(set(addrs)))


def _extract_body_text(msg: Message) -> tuple[str | None, str | None]:
    """Return (plain_text, html_text) best-effort."""
    plain_parts: list[str] = []
    html_parts: list[str] = []

    if msg.is_multipart():
        for part in msg.walk():
            ctype = part.get_content_type()
            if ctype == "text/plain":
                payload = part.get_payload(decode=True)
                if payload:
                    plain_parts.append(_decode_payload(payload, part))
            elif ctype == "text/html":
                payload = part.get_payload(decode=True)
                if payload:
                    html_parts.append(_decode_payload(payload, part))
    else:
        ctype = msg.get_content_type()
        payload = msg.get_payload(decode=True)
        if payload:
            text = _decode_payload(payload, msg)
            if ctype == "text/html":
                html_parts.append(text)
            else:
                plain_parts.append(text)

    plain = "\n".join(plain_parts).strip() or None
    html = "\n".join(html_parts).strip() or None
    return plain, html


def _decode_payload(payload: bytes | Any, part: Message) -> str:
    """Decode a message payload to unicode using the part's charset."""
    if isinstance(payload, str):
        return payload
    charset = part.get_content_charset() or "utf-8"
    try:
        return payload.decode(charset, errors="replace")
    except (LookupError, TypeError):
        return payload.decode("utf-8", errors="replace")


def _extract_attachments(msg: Message) -> tuple[AttachmentRef, ...]:
    """List non-inline attachments with filename, content type, and size."""
    attachments: list[AttachmentRef] = []
    if not msg.is_multipart():
        return ()

    for part in msg.walk():
        if part.is_multipart():
            continue
        disposition = part.get_content_disposition() or ""
        filename = part.get_filename()
        content_id = part.get("Content-ID", "")
        if filename or disposition == "attachment":
            payload = part.get_payload(decode=True)
            size = len(payload) if isinstance(payload, bytes) else None
            attachments.append(
                AttachmentRef(
                    filename=filename or "unnamed",
                    content_type=part.get_content_type(),
                    size=size,
                    content_id=content_id.strip("<>") if content_id else None,
                )
            )
    return tuple(attachments)


def _extract_received_chain(msg: Message) -> tuple[ReceivedHop, ...]:
    """Parse Received headers into a chain of hops (newest/final hop first)."""
    hops: list[ReceivedHop] = []
    for raw in msg.get_all("Received", []):
        hop = _parse_received_line(raw)
        if hop:
            hops.append(hop)
    return tuple(hops)


def _parse_received_line(line: str) -> ReceivedHop | None:
    """Best-effort parse of a single Received header line."""
    line = line.replace("\n", " ").replace("\r", " ")
    timestamp: datetime | None = None

    # Find the last semicolon-delimited date
    if ";" in line:
        date_part = line.rsplit(";", 1)[-1].strip()
        timestamp = _parse_email_date(date_part)

    from_host = _extract_received_token(line, r"from\s+([^\s;]+)")
    by_host = _extract_received_token(line, r"by\s+([^\s;]+)")
    with_protocol = _extract_received_token(line, r"with\s+([^\s;]+)")
    id_string = _extract_received_token(line, r"id\s+([^\s;]+)")

    return ReceivedHop(
        from_host=from_host,
        by_host=by_host,
        with_protocol=with_protocol,
        timestamp=timestamp,
        id_string=id_string,
        raw_line=line.strip(),
    )


def _extract_received_token(line: str, pattern: str) -> str | None:
    match = re.search(pattern, line, re.IGNORECASE)
    if match:
        token = match.group(1).strip()
        # Ignore parenthesized comments that the regex may have captured
        if token.startswith("("):
            return None
        return token
    return None


# ---------------------------------------------------------------------------
# 3. Temporal plane
# ---------------------------------------------------------------------------
def _build_temporal_metadata(
    msg: Message,
    received_chain: tuple[ReceivedHop, ...],
    artifact_id: str,
) -> TemporalMetadata:
    events: list[TemporalEvent] = []

    date_hdr = msg.get("Date")
    parsed_date = _parse_email_date(date_hdr)
    if parsed_date and date_hdr:
        events.append(
            TemporalEvent(
                timestamp=parsed_date,
                event_type="email_sent",
                source_plane="container",
                source_field="Date",
                raw_value=date_hdr.strip(),
                timezone="UTC",
                confidence=1.0,
                artifact_id=artifact_id,
            )
        )

    for idx, hop in enumerate(received_chain):
        if hop.timestamp:
            events.append(
                TemporalEvent(
                    timestamp=hop.timestamp,
                    event_type="email_received",
                    source_plane="container",
                    source_field=f"Received[{idx}]",
                    raw_value=hop.raw_line,
                    timezone="UTC",
                    confidence=0.9,
                    artifact_id=artifact_id,
                )
            )

    return TemporalMetadata(events=tuple(events))


# ---------------------------------------------------------------------------
# 4. Main extractor
# ---------------------------------------------------------------------------
def extract_email_metadata(
    data: bytes,
    artifact_id: str,
) -> ExtractedMetadata:
    """Extract all 5 metadata planes from an RFC-822 email message."""
    extraction_time = datetime.now(UTC)

    try:
        msg = BytesParser(policy=default_policy).parsebytes(data)
    except Exception as exc:
        return ExtractedMetadata(
            artifact_id=artifact_id,
            extraction_time=extraction_time,
            extraction_errors=(f"email parse error: {exc}",),
        )

    # Container plane
    from_addr = _normalize_addr(msg.get("From", ""))
    to_addrs = _extract_addrs(msg, "To")
    cc_addrs = _extract_addrs(msg, "Cc")
    bcc_addrs = _extract_addrs(msg, "Bcc")
    subject = msg.get("Subject")
    message_id = msg.get("Message-ID")
    in_reply_to = msg.get("In-Reply-To")
    references = tuple(msg.get_all("References", []))
    x_mailer = msg.get("X-Mailer") or msg.get("User-Agent")
    x_originating_ip = msg.get("X-Originating-IP")
    return_path = msg.get("Return-Path")
    dkim_signature = msg.get("DKIM-Signature")
    content_type = msg.get_content_type()
    boundary = msg.get_boundary()

    received_chain = _extract_received_chain(msg)
    attachments = _extract_attachments(msg)

    date_hdr = msg.get("Date")
    email_date = _parse_email_date(date_hdr)

    container_metadata = ContainerMetadata(
        message_id=message_id,
        in_reply_to=in_reply_to,
        references=references,
        from_addr=from_addr or None,
        to_addrs=to_addrs,
        cc_addrs=cc_addrs,
        bcc_addrs=bcc_addrs,
        subject=subject,
        date=email_date,
        received_chain=received_chain,
        x_mailer=x_mailer,
        x_originating_ip=x_originating_ip,
        return_path=return_path,
        dkim_signature=dkim_signature,
        content_type=content_type,
        boundary=boundary,
        attachments=attachments,
    )

    # Application plane
    application_metadata = ApplicationMetadata(
        application=x_mailer,
        creator=from_addr or None,
        author=from_addr or None,
    )

    # Content plane
    plain_text, html_text = _extract_body_text(msg)
    body_text = plain_text or html_text or ""
    urls = set(_URL_RE.findall(body_text))
    emails = set(_EMAIL_RE.findall(body_text))
    word_count = len(body_text.split()) if body_text else None
    char_count = len(body_text) if body_text else None

    content_metadata = ContentMetadata(
        embedded_urls=tuple(sorted(urls)),
        email_addresses=tuple(sorted(emails)),
        word_count=word_count,
        character_count=char_count,
        hyperlink_count=len(urls),
        annotation_count=len(attachments),
    )

    # Temporal plane
    temporal_metadata = _build_temporal_metadata(msg, received_chain, artifact_id)

    return ExtractedMetadata(
        artifact_id=artifact_id,
        extraction_time=extraction_time,
        plane_container=container_metadata,
        plane_application=application_metadata,
        plane_content=content_metadata,
        plane_temporal=temporal_metadata,
    )
