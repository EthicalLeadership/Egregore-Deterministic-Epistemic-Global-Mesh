"""
ANCHORUM Entity Canonicalization Engine
========================================
Transforms raw metadata strings into normalized, deduplicated, court-auditable entities.
Unicode normalization, alias resolution, confidence scoring.
Stdlib only. Python 3.11+.

CBI-0:
- M3: Immutable outputs (CanonicalEntity)
- M4: Every canonicalization decision is traceable to source field + artifact
"""

from __future__ import annotations

import hashlib
import logging
import re
import unicodedata
from datetime import UTC, datetime

from anchorum.forensic.core.types import (
    ApplicationMetadata,
    CanonicalEntity,
    ContainerMetadata,
    ContentMetadata,
    EntityType,
)

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# 1. Normalization Rules
# ---------------------------------------------------------------------------
# French/English title stripping
PERSON_TITLES = {
    "mr",
    "mrs",
    "ms",
    "miss",
    "dr",
    "prof",
    "sir",
    "lord",
    "lady",
    "m",
    "me",
    "mme",
    "mlle",
    "mg",
    "mga",
    "mgn",
}

# Legal suffixes to strip from organizations
ORG_SUFFIXES = {
    "inc",
    "inc.",
    "ltd",
    "ltd.",
    "limited",
    "llc",
    "llp",
    "lp",
    "corp",
    "corp.",
    "corporation",
    "co",
    "co.",
    "company",
    "s.a.",
    "s.a",
    "sarl",
    "s.a.r.l.",
    "s.a.r.l",
    "s.a.s.",
    "sas",
    "s.a.s",
    "eurl",
    "s.c.",
    "sc",
    "gmbh",
    "ag",
    "kg",
    "ohg",
    "bv",
    "nv",
    "plc",
    "pty",
    "pty.",
}

# Software version normalization patterns
VERSION_RE = re.compile(
    r"[\s\-_]?(?:v?\d+(?:\.\d+)*(?:\s*(?:beta|alpha|rc|build|b)\s*\d+)?)$",
    re.IGNORECASE,
)

# Email normalization
EMAIL_PLUS_RE = re.compile(r"\+[^@]+@")

# MAC address pattern
MAC_RE = re.compile(r"([0-9a-fA-F]{2}[:-]){5}[0-9a-fA-F]{2}")

# IP address pattern
IPV4_RE = re.compile(r"\b(?:\d{1,3}\.){3}\d{1,3}\b")
IPV6_RE = re.compile(r"\b(?:[0-9a-fA-F]{1,4}:){7}[0-9a-fA-F]{1,4}\b")

# Phone number (North America + international loose)
PHONE_RE = re.compile(r"\b(?:\+?1[-.\s]?)?\(?\d{3}\)?[-.\s]?\d{3}[-.\s]?\d{4}\b")

# SSN (US/Canada pattern — loose, for detection only)
SSN_RE = re.compile(r"\b\d{3}[-.\s]?\d{2}[-.\s]?\d{4}\b")

# Credit card (Luhn-validated separately)
CC_RE = re.compile(r"\b(?:\d{4}[-.\s]?){3}\d{4}\b")


# ---------------------------------------------------------------------------
# 2. Normalization Functions
# ---------------------------------------------------------------------------
def normalize_text(text: str | None) -> str | None:
    """Unicode NFKC decomposition, replace control chars with space, collapse whitespace."""
    if text is None:
        return None
    # NFKC: compatibility decomposition + canonical composition
    normalized = unicodedata.normalize("NFKC", text)
    # Replace control characters with space, preserving tab/newline as whitespace
    cleaned = "".join(
        ch if unicodedata.category(ch)[0] != "C" or ch in "\t\n" else " "
        for ch in normalized
    )
    # Collapse whitespace
    cleaned = " ".join(cleaned.split())
    return cleaned.strip()


def normalize_person(name: str | None) -> str | None:
    """Normalize person name: strip titles, lowercase, sort components."""
    name = normalize_text(name)
    if not name:
        return None
    lower = name.lower()
    # Strip surrounding punctuation from each component
    parts = [p.strip(".,;:") for p in lower.split()]
    # Strip titles
    filtered = [p for p in parts if p.rstrip(".") not in PERSON_TITLES]
    if not filtered:
        filtered = parts  # If everything was a title, keep it
    # Sort components for canonical ordering (handles "Doe, John" vs "John Doe")
    sorted_parts = sorted(filtered)
    return " ".join(sorted_parts)


def normalize_organization(org: str | None) -> str | None:
    """Normalize organization: strip legal suffixes, lowercase."""
    org = normalize_text(org)
    if not org:
        return None
    lower = org.lower()
    parts = lower.split()
    # Strip suffixes from end
    while parts and parts[-1].rstrip(".") in ORG_SUFFIXES:
        parts.pop()
    return " ".join(parts) if parts else lower


def normalize_email(email: str | None) -> str | None:
    """Normalize email: lowercase, strip +tags, normalize domain."""
    email = normalize_text(email)
    if not email:
        return None
    email = email.lower().strip()
    # Strip +tags (e.g., john+spam@example.com -> john@example.com)
    email = EMAIL_PLUS_RE.sub("@", email)
    return email


def normalize_software(software: str | None) -> str | None:
    """Normalize software: strip version numbers, lowercase."""
    software = normalize_text(software)
    if not software:
        return None
    lower = software.lower()
    # Strip version suffix
    lower = VERSION_RE.sub("", lower).strip()
    return lower


def normalize_device(device: str | None) -> str | None:
    """Normalize device identifier: uppercase hex, strip separators."""
    device = normalize_text(device)
    if not device:
        return None
    # If it looks like a MAC, normalize to colon-separated uppercase
    if MAC_RE.match(device):
        hex_only = re.sub(r"[^0-9a-fA-F]", "", device)
        return ":".join(hex_only[i : i + 2] for i in range(0, 12, 2)).upper()
    return device.lower().strip()


# ---------------------------------------------------------------------------
# 3. Entity ID Generation
# ---------------------------------------------------------------------------
def entity_id(normalized: str, entity_type: EntityType) -> str:
    """Deterministic SHA-256 of normalized form + type."""
    payload = f"{entity_type.value}:{normalized}".encode()
    return hashlib.sha256(payload).hexdigest()


# ---------------------------------------------------------------------------
# 4. Extraction from Metadata Planes
# ---------------------------------------------------------------------------
class EntityExtractor:
    """
    Extracts candidate entities from all 5 metadata planes.
    Returns list of (CanonicalEntity, source_field, source_artifact) tuples.
    """

    def __init__(self) -> None:
        self._entities: dict[str, CanonicalEntity] = {}

    def extract_from_container(
        self, container: ContainerMetadata, artifact_id: str
    ) -> list[CanonicalEntity]:
        """Extract entities from Plane 2 (container)."""
        found: list[CanonicalEntity] = []

        # Email addresses
        for addr in (
            (container.from_addr or "",)
            + (container.to_addrs or ())
            + (container.cc_addrs or ())
            + (container.bcc_addrs or ())
        ):
            if addr:
                e = self._make_email(addr, artifact_id, "container.from_addr")
                if e:
                    found.append(e)

        # Embedded file names -> possible persons
        for _fname in container.embedded_files or ():
            # Heuristic: if filename contains a name pattern
            pass  # Too noisy without NLP

        return found

    def extract_from_application(
        self, app: ApplicationMetadata, artifact_id: str
    ) -> list[CanonicalEntity]:
        """Extract entities from Plane 3 (application)."""
        found: list[CanonicalEntity] = []

        mappings = [
            (app.author, EntityType.PERSON, "app.author", normalize_person),
            (app.producer, EntityType.SOFTWARE, "app.producer", normalize_software),
            (app.creator, EntityType.SOFTWARE, "app.creator", normalize_software),
            (
                app.company,
                EntityType.ORGANIZATION,
                "app.company",
                normalize_organization,
            ),
            (
                app.last_modified_by,
                EntityType.PERSON,
                "app.last_modified_by",
                normalize_person,
            ),
            (app.manager, EntityType.PERSON, "app.manager", normalize_person),
        ]

        for raw_value, e_type, field_name, normalizer in mappings:
            if raw_value:
                normalized = normalizer(raw_value)
                if normalized:
                    e = self._make_entity(
                        normalized, e_type, raw_value, artifact_id, field_name
                    )
                    found.append(e)

        # Template path -> network location / department inference
        if app.template:
            # Extract server name from \SERVER\share\path
            m = re.search(r"\\\\([^\\]+)", app.template)
            if m:
                server = m.group(1).lower()
                e = self._make_entity(
                    server,
                    EntityType.DEVICE,
                    app.template,
                    artifact_id,
                    "app.template_server",
                )
                found.append(e)

        return found

    def extract_from_content(
        self, content: ContentMetadata, artifact_id: str
    ) -> list[CanonicalEntity]:
        """Extract entities from Plane 4 (content-derived)."""
        found: list[CanonicalEntity] = []

        for addr in content.email_addresses or ():
            e = self._make_email(addr, artifact_id, "content.email_addresses")
            if e:
                found.append(e)

        for ip in content.ip_addresses or ():
            e = self._make_entity(
                ip.lower(), EntityType.DEVICE, ip, artifact_id, "content.ip_addresses"
            )
            found.append(e)

        for mac in content.mac_addresses or ():
            e = self._make_entity(
                normalize_device(mac),
                EntityType.DEVICE,
                mac,
                artifact_id,
                "content.mac_addresses",
            )
            found.append(e)

        for phone in content.phone_numbers or ():
            e = self._make_entity(
                phone, EntityType.DEVICE, phone, artifact_id, "content.phone_numbers"
            )
            found.append(e)

        return found

    def _make_email(
        self, raw: str, artifact_id: str, field: str
    ) -> CanonicalEntity | None:
        normalized = normalize_email(raw)
        if not normalized:
            return None
        # Extract local part and domain as separate entities
        if "@" in normalized:
            local, domain = normalized.rsplit("@", 1)
            # Person entity from local part (weak signal)
            normalize_person(local.replace(".", " ").replace("_", " "))
            # Not creating person from email local part — too noisy
            pass
        return self._make_entity(normalized, EntityType.EMAIL, raw, artifact_id, field)

    def _make_entity(
        self,
        normalized: str,
        e_type: EntityType,
        raw_value: str,
        artifact_id: str,
        field: str,
    ) -> CanonicalEntity:
        eid = entity_id(normalized, e_type)
        return CanonicalEntity(
            entity_id=eid,
            entity_type=e_type,
            display_name=raw_value,
            normalized_form=normalized,
            aliases=(raw_value,) if raw_value != normalized else (),
            first_seen=None,
            last_seen=None,
            source_artifacts=(artifact_id,),
            source_fields=(field,),
            confidence=1.0,
        )


# ---------------------------------------------------------------------------
# 5. Entity Merger (Deduplication)
# ---------------------------------------------------------------------------
def merge_entities(entities: list[CanonicalEntity]) -> list[CanonicalEntity]:
    """
    Merge entities by entity_id, collapsing aliases and source references.
    Returns deduplicated list sorted by first_seen.
    """
    merged: dict[str, CanonicalEntity] = {}

    for e in entities:
        if e.entity_id in merged:
            existing = merged[e.entity_id]
            # Merge aliases
            all_aliases = set(existing.aliases) | set(e.aliases)
            if existing.display_name != existing.normalized_form:
                all_aliases.add(existing.display_name)
            if e.display_name != e.normalized_form:
                all_aliases.add(e.display_name)
            all_aliases.discard(existing.normalized_form)

            # Merge artifacts and fields
            all_artifacts = set(existing.source_artifacts) | set(e.source_artifacts)
            all_fields = set(existing.source_fields) | set(e.source_fields)

            # Temporal bounds
            first = existing.first_seen
            if first is None or (e.first_seen is not None and e.first_seen < first):
                first = e.first_seen
            last = existing.last_seen
            if last is None or (e.last_seen is not None and e.last_seen > last):
                last = e.last_seen

            merged[e.entity_id] = CanonicalEntity(
                entity_id=e.entity_id,
                entity_type=e.entity_type,
                display_name=existing.display_name,  # Keep first seen display name
                normalized_form=existing.normalized_form,
                aliases=tuple(sorted(all_aliases)),
                first_seen=first,
                last_seen=last,
                source_artifacts=tuple(sorted(all_artifacts)),
                source_fields=tuple(sorted(all_fields)),
                confidence=max(existing.confidence, e.confidence),
            )
        else:
            merged[e.entity_id] = e

    return sorted(
        merged.values(),
        key=lambda x: (
            x.first_seen or datetime.max.replace(tzinfo=UTC),
            x.entity_id,
        ),
    )


# ---------------------------------------------------------------------------
# 6. Self-test
# ---------------------------------------------------------------------------
if __name__ == "__main__":
    # Test normalization
    if not (normalize_person("Dr. John Smith") == "john smith"):
        raise AssertionError
    if not (normalize_person("SMITH, JOHN") == "john smith"):
        raise AssertionError
    if not (normalize_organization("Acme Corp Canada Inc.") == "acme corp canada"):
        raise AssertionError
    if not (
        normalize_email("John.Smith+HR@Acme-Corp.Example.COM")
        == "john.smith@acme-corp.example.com"
    ):
        raise AssertionError
    if not (normalize_software("Microsoft Word 16.0.12345") == "microsoft word"):
        raise AssertionError
    if not (normalize_device("00:1A:2B:3C:4D:5E") == "00:1A:2B:3C:4D:5E"):
        raise AssertionError
    print("Normalization: PASS")

    # Test entity extraction
    extractor = EntityExtractor()
    app_meta = ApplicationMetadata(
        author="Dr. Jane Doe",
        company="Acme Corp Canada Inc.",
        producer="Microsoft Word 16.0",
        template=r"\\EXAMPLE-HQ-FS01\Templates\HR_Grievance.dotx",
    )
    entities = extractor.extract_from_application(app_meta, artifact_id="ART-001")
    # author, company, producer/software, template_server
    if not (len(entities) == 4):
        raise AssertionError
    print(f"Entity extraction: PASS ({len(entities)} entities)")

    # Test deduplication
    e1 = extractor._make_entity(
        "john smith", EntityType.PERSON, "John Smith", "ART-001", "app.author"
    )
    e2 = extractor._make_entity(
        "john smith", EntityType.PERSON, "J. Smith", "ART-002", "app.author"
    )
    merged = merge_entities([e1, e2])
    if not (len(merged) == 1):
        raise AssertionError
    if not (merged[0].aliases == ("J. Smith", "John Smith")):
        raise AssertionError
    if not (len(merged[0].source_artifacts) == 2):
        raise AssertionError
    print("Deduplication: PASS")

    print("\nAll canonicalization tests passed.")
