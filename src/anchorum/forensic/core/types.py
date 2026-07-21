"""
ANCHORUM Forensic Core Types
==============================
Immutable data primitives for the Metadata Intelligence Platform.
All dataclasses are frozen (hashable, thread-safe, court-auditable).
Python 3.11+. Stdlib only.

CBI-0 Governance:
- M1: Read-only enforcement at ingestion boundary
- M2: Tool registration via manifest stubs
- M3: Terminal outputs only (frozen dataclasses)
- M4: Every type serializes to canonical JSON for .zarc emission
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime
from enum import Enum, auto
from typing import Any


# ---------------------------------------------------------------------------
# 1. Enums
# ---------------------------------------------------------------------------
class ContainerType(Enum):
    PDF = "pdf"
    OOXML = "ooxml"
    ODT = "odt"
    LEGACY_OFFICE = "legacy_office"
    EMAIL = "email"
    JPEG = "jpeg"
    PNG = "png"
    TIFF = "tiff"
    GIF = "gif"
    BMP = "bmp"
    RAW_IMAGE = "raw_image"
    ZIP = "zip"
    TEXT = "text"
    UNKNOWN = "unknown"


class EventType(Enum):
    ARTIFACT_INGESTED = auto()
    METADATA_EXTRACTION = auto()
    ENTITY_CANONICALIZED = auto()
    CORRELATION_DETECTED = auto()
    TIMELINE_EVENT_CREATED = auto()
    ANOMALY_FLAGGED = auto()
    REPORT_GENERATED = auto()


class EntityType(Enum):
    PERSON = "person"
    ORGANIZATION = "organization"
    EMAIL = "email"
    DEVICE = "device"
    SOFTWARE = "software"
    LOCATION = "location"
    DOCUMENT = "document"
    EVENT = "event"


class EdgeType(Enum):
    AUTHORED = "authored"
    PRINTED_ON = "printed_on"
    SENT_TO = "sent_to"
    SENT_FROM = "sent_from"
    MODIFIED_BY = "modified_by"
    CREATED_BY = "created_by"
    CONTAINS = "contains"
    REFERENCES = "references"
    REPLIED_TO = "replied_to"
    FORWARDED_FROM = "forwarded_from"
    EMBEDDED_IN = "embedded_in"
    SHARED_TEMPLATE = "shared_template"
    TEMPORAL_PROXIMITY = "temporal_proximity"


class AnomalyType(Enum):
    BACKDATED = "backdated"
    FUTURE_DATED = "future_dated"
    TIMEZONE_INCONSISTENCY = "timezone_inconsistency"
    IMPOSSIBLE_SEQUENCE = "impossible_sequence"
    METADATA_SCRUBBED = "metadata_scrubbed"
    METADATA_FORGED = "metadata_forged"
    SELECTIVE_DEGRADATION = "selective_degradation"
    ENCRYPTION_INTERMITTENT = "encryption_intermittent"
    RASTERIZATION_OBFUSCATION = "rasterization_obfuscation"
    JAVASCRIPT_INJECTION = "javascript_injection"
    EMBEDDED_FILE_CONCEALMENT = "embedded_file_concealment"
    FORM_OBFUSCATION = "form_obfuscation"
    XFA_CONCEALMENT = "xfa_concealment"
    PRINTER_MARK_ANOMALY = "printer_mark_anomaly"
    FONT_INCONSISTENCY = "font_inconsistency"
    SOFTWARE_MISMATCH = "software_mismatch"
    WEEKEND_CREATION = "weekend_creation"
    AFTER_HOURS_CREATION = "after_hours_creation"
    GAP_DETECTED = "gap_detected"
    BIRDTIME_MISSING = "birthtime_missing"
    DELETED_CONTENT_RECOVERED = "deleted_content_recovered"
    HIDDEN_REVISIONS = "hidden_revisions"
    COMMENTS_DETECTED = "comments_detected"
    PREVIOUS_VERSIONS = "previous_versions"
    REDACTION_ANNOTATIONS = "redaction_annotations"
    HIDDEN_LAYERS = "hidden_layers"
    PLAINTEXT_EVIDENCE = "plaintext_evidence"


# ---------------------------------------------------------------------------
# 2. Core Immutable Types
# ---------------------------------------------------------------------------
@dataclass(frozen=True, slots=True)
class Artifact:
    """Immutable representation of an ingested file."""

    artifact_id: str  # SHA-256 hex of full file bytes
    source_path: str  # Absolute path at ingestion
    ingest_time: datetime  # UTC
    size_bytes: int
    container_type: ContainerType
    mime_type: str | None = None
    original_filename: str | None = None
    filesystem_metadata: FsMetadata | None = None

    def __post_init__(self) -> None:
        # Force UTC if naive datetime passed
        if self.ingest_time.tzinfo is None:
            object.__setattr__(
                self, "ingest_time", self.ingest_time.replace(tzinfo=UTC)
            )


@dataclass(frozen=True, slots=True)
class FsMetadata:
    """Filesystem-level metadata."""

    birth_time: datetime | None  # st_birthtime (macOS/BSD) or None
    mod_time: datetime
    access_time: datetime
    inode: int
    device: int
    mode: int
    owner_uid: int
    group_gid: int
    owner_name: str | None = None
    group_name: str | None = None
    hardlink_count: int = 1
    extended_attrs: dict[str, bytes] = field(default_factory=dict)
    alternate_data_streams: tuple[str, ...] = ()
    volume_label: str | None = None
    filesystem_type: str | None = None  # "ext4", "apfs", "ntfs", etc.

    def __post_init__(self) -> None:
        for attr in ("birth_time", "mod_time", "access_time"):
            val = getattr(self, attr)
            if val is not None and val.tzinfo is None:
                object.__setattr__(self, attr, val.replace(tzinfo=UTC))


@dataclass(frozen=True, slots=True)
class ExtractedMetadata:
    """Container for all 5 planes of metadata from a single artifact."""

    artifact_id: str
    extraction_time: datetime
    plane_fs: FsMetadata | None = None
    plane_container: ContainerMetadata | None = None
    plane_application: ApplicationMetadata | None = None
    plane_content: ContentMetadata | None = None
    plane_temporal: TemporalMetadata | None = None
    extraction_errors: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class ContainerMetadata:
    """Plane 2: Format-specific structure."""

    # Generic
    format_version: str | None = None
    object_count: int | None = None
    stream_count: int | None = None

    # PDF-specific
    pdf_header_version: str | None = None
    xref_count: int | None = None
    object_streams: int | None = None
    incremental_updates: int | None = None
    trailer_count: int | None = None
    encrypt_revisions: tuple[bool, ...] = ()
    javascript_locations: tuple[str, ...] = ()
    embedded_files: tuple[str, ...] = ()
    acroform_fields: int = 0
    xfa_detected: bool = False
    cross_reference_corruption: bool = False
    linearized: bool = False

    # OOXML-specific
    core_properties: dict[str, Any] = field(default_factory=dict)
    extended_properties: dict[str, Any] = field(default_factory=dict)
    custom_properties: dict[str, Any] = field(default_factory=dict)
    relationships: tuple[Relationship, ...] = ()
    revision_history: tuple[Revision, ...] = ()
    comments: tuple[Comment, ...] = ()
    embedded_objects: tuple[str, ...] = ()
    template_path: str | None = None
    attached_template: str | None = None
    default_tab_stop: float | None = None
    zoom_percentage: int | None = None

    # Email-specific
    message_id: str | None = None
    in_reply_to: str | None = None
    references: tuple[str, ...] = ()
    from_addr: str | None = None
    to_addrs: tuple[str, ...] = ()
    cc_addrs: tuple[str, ...] = ()
    bcc_addrs: tuple[str, ...] = ()
    subject: str | None = None
    date: datetime | None = None
    received_chain: tuple[ReceivedHop, ...] = ()
    x_mailer: str | None = None
    x_originating_ip: str | None = None
    return_path: str | None = None
    dkim_signature: str | None = None
    content_type: str | None = None
    boundary: str | None = None
    attachments: tuple[AttachmentRef, ...] = ()

    # Image-specific
    exif: dict[str, Any] = field(default_factory=dict)
    xmp: dict[str, Any] = field(default_factory=dict)
    iptc: dict[str, Any] = field(default_factory=dict)
    gps_latitude: float | None = None
    gps_longitude: float | None = None
    gps_altitude: float | None = None
    camera_make: str | None = None
    camera_model: str | None = None
    camera_serial: str | None = None
    lens_model: str | None = None
    software: str | None = None
    image_width: int | None = None
    image_height: int | None = None
    bits_per_sample: int | None = None
    color_space: str | None = None
    compression: str | None = None
    orientation: int | None = None
    original_document_name: str | None = None

    # ZIP-specific
    zip_entries: tuple[ZipEntry, ...] = ()
    zip_comment: str | None = None
    zip_encrypted: bool = False


@dataclass(frozen=True, slots=True)
class ApplicationMetadata:
    """Plane 3: Software fingerprints."""

    producer: str | None = None  # PDF /Producer
    creator: str | None = None  # PDF /Creator or OOXML creator
    author: str | None = None
    company: str | None = None
    title: str | None = None
    subject: str | None = None
    keywords: tuple[str, ...] = ()
    application: str | None = None  # OOXML Application
    app_version: str | None = None
    platform: str | None = None  # "Windows", "Mac", "Linux" inferred
    template: str | None = None
    last_modified_by: str | None = None
    total_editing_time_minutes: int | None = None
    pages: int | None = None
    words: int | None = None
    characters: int | None = None
    paragraphs: int | None = None
    lines: int | None = None
    company_address: str | None = None
    manager: str | None = None
    category: str | None = None
    hyperlink_base: str | None = None


@dataclass(frozen=True, slots=True)
class ContentMetadata:
    """Plane 4: Content-derived intelligence."""

    fonts: tuple[str, ...] = ()
    font_families: tuple[str, ...] = ()
    embedded_urls: tuple[str, ...] = ()
    email_addresses: tuple[str, ...] = ()
    phone_numbers: tuple[str, ...] = ()
    ip_addresses: tuple[str, ...] = ()
    mac_addresses: tuple[str, ...] = ()
    social_security_numbers: tuple[str, ...] = ()
    credit_card_numbers: tuple[str, ...] = ()
    language_detected: str | None = None
    language_confidence: float | None = None
    word_count: int | None = None
    character_count: int | None = None
    line_count: int | None = None
    has_printer_marks: bool = False
    has_bleed_box: bool = False
    has_crop_box: bool = False
    has_art_box: bool = False
    has_trim_box: bool = False
    page_count: int | None = None
    image_count: int | None = None
    table_count: int | None = None
    hyperlink_count: int | None = None
    bookmark_count: int | None = None
    annotation_count: int | None = None
    redaction_count: int | None = None
    javascript_snippets: tuple[str, ...] = ()
    suspicious_strings: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class TemporalMetadata:
    """Plane 5: Every timestamp found anywhere in the artifact."""

    events: tuple[TemporalEvent, ...] = ()
    earliest: datetime | None = None
    latest: datetime | None = None
    timezone_count: int = 0
    timezone_names: tuple[str, ...] = ()
    duration_seconds: float | None = None

    def __post_init__(self) -> None:
        if self.events:
            sorted_events = sorted(self.events, key=lambda e: e.timestamp)
            object.__setattr__(self, "earliest", sorted_events[0].timestamp)
            object.__setattr__(self, "latest", sorted_events[-1].timestamp)
            if self.earliest and self.latest:
                object.__setattr__(
                    self,
                    "duration_seconds",
                    (self.latest - self.earliest).total_seconds(),
                )


@dataclass(frozen=True, slots=True)
class TemporalEvent:
    """A single timestamped event from any metadata plane."""

    timestamp: datetime
    event_type: str  # "creation", "modification", "access", "print", "email_sent", etc.
    source_plane: str  # "fs", "container", "app", "content", "temporal"
    source_field: str  # The exact field name: "CreationDate", "st_mtime", etc.
    raw_value: str  # Raw string before normalization
    timezone: str | None = None
    confidence: float = 1.0  # 1.0 = direct from metadata, <1.0 = inferred
    artifact_id: str = ""


@dataclass(frozen=True, slots=True)
class CanonicalEntity:
    """Normalized, deduplicated entity discovered across artifacts."""

    entity_id: str  # SHA-256 of normalized form
    entity_type: EntityType
    display_name: str
    normalized_form: str
    aliases: tuple[str, ...] = ()
    first_seen: datetime | None = None
    last_seen: datetime | None = None
    source_artifacts: tuple[str, ...] = ()
    source_fields: tuple[str, ...] = ()
    confidence: float = 1.0
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class GraphNode:
    """Node in the correlation graph."""

    node_id: str
    entity: CanonicalEntity
    node_type: str  # "document", "person", "device", "software", "event"
    artifact_ids: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class GraphEdge:
    """Edge in the correlation graph."""

    edge_id: str  # SHA-256 of sorted(node_ids) + edge_type
    source_id: str
    target_id: str
    edge_type: EdgeType
    confidence: float
    provenance: tuple[str, ...] = ()  # artifact IDs supporting this edge
    description: str = ""
    temporal_window_seconds: float | None = None


@dataclass(frozen=True, slots=True)
class TimelineEntry:
    """Entry in the master timeline view."""

    entry_id: str
    timestamp: datetime
    event_type: str
    artifact_id: str
    entity_ids: tuple[str, ...] = ()
    description: str = ""
    confidence: float = 1.0
    sources: tuple[str, ...] = ()  # Which metadata plane(s)
    related_entries: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class AnomalyFinding:
    """A detected anomaly with full provenance."""

    anomaly_id: str
    anomaly_type: AnomalyType
    severity: str  # "critical", "high", "medium", "low", "info"
    confidence: float
    description: str
    affected_artifacts: tuple[str, ...] = ()
    affected_entities: tuple[str, ...] = ()
    supporting_evidence: tuple[str, ...] = ()
    recommended_action: str = ""
    timestamp_detected: datetime | None = None


@dataclass(frozen=True, slots=True)
class InvestigationReport:
    """Final forensic investigation output."""

    report_id: str
    case_id: str
    generated_at: datetime
    operator: str
    artifact_count: int
    entity_count: int
    anomaly_count: int
    critical_findings: tuple[AnomalyFinding, ...] = ()
    high_findings: tuple[AnomalyFinding, ...] = ()
    medium_findings: tuple[AnomalyFinding, ...] = ()
    low_findings: tuple[AnomalyFinding, ...] = ()
    info_findings: tuple[AnomalyFinding, ...] = ()
    master_timeline: tuple[TimelineEntry, ...] = ()
    entity_directory: tuple[CanonicalEntity, ...] = ()
    correlation_graph_nodes: tuple[GraphNode, ...] = ()
    correlation_graph_edges: tuple[GraphEdge, ...] = ()
    methodology_version: str = "1.0.0"
    limitations: tuple[str, ...] = ()
    zarc_event_id: str | None = None
    llm_summary: str | None = None
    llm_model_id: str | None = None
    unverified_enrichment: bool = False


# ---------------------------------------------------------------------------
# 3. Sub-types (used by ContainerMetadata)
# ---------------------------------------------------------------------------
@dataclass(frozen=True, slots=True)
class Relationship:
    rel_id: str
    rel_type: str
    target: str
    target_mode: str | None = None


@dataclass(frozen=True, slots=True)
class Revision:
    rev_id: str
    author: str | None = None
    date: datetime | None = None
    rev_type: str | None = None  # "insertion", "deletion", "formatting"
    text_preview: str | None = None


@dataclass(frozen=True, slots=True)
class Comment:
    comment_id: str
    author: str | None = None
    date: datetime | None = None
    text: str | None = None
    initials: str | None = None


@dataclass(frozen=True, slots=True)
class ReceivedHop:
    """One hop in the email Received chain."""

    from_host: str | None = None
    by_host: str | None = None
    with_protocol: str | None = None
    timestamp: datetime | None = None
    id_string: str | None = None
    raw_line: str = ""


@dataclass(frozen=True, slots=True)
class AttachmentRef:
    filename: str
    content_type: str | None = None
    size: int | None = None
    content_id: str | None = None
    artifact_id: str | None = None  # If ingested separately


@dataclass(frozen=True, slots=True)
class ZipEntry:
    name: str
    size: int
    compressed_size: int
    is_encrypted: bool
    modified_time: datetime | None = None
    crc: int | None = None


# ---------------------------------------------------------------------------
# 4. Utility
# ---------------------------------------------------------------------------
def to_canonical_json(obj: Any) -> Any:
    """Serialize any frozen dataclass (or nested structure) to JSON-compatible types."""
    if hasattr(obj, "__dataclass_fields__"):
        obj = asdict(obj)
    if isinstance(obj, Enum):
        return obj.value
    if isinstance(obj, datetime):
        return obj.isoformat()
    if isinstance(obj, bytes):
        return obj.hex()
    if isinstance(obj, (tuple, list)):
        return [to_canonical_json(i) for i in obj]
    if isinstance(obj, dict):
        return {k: to_canonical_json(v) for k, v in obj.items()}
    return obj


# ---------------------------------------------------------------------------
# 5. Standalone stubs
# ---------------------------------------------------------------------------
# These stubs keep the core foundation stdlib-only and runnable without the
# full ANCHORUM provenance/manifest subsystems. Production deployments may
# replace them with governed implementations.
def register_tool(**kwargs: Any) -> None:
    """No-op tool registration stub."""


class ZarcEventType(Enum):
    ARTIFACT_INGESTED = auto()


def emit_zarc_event(**kwargs: Any) -> str:
    """No-op provenance event emitter stub."""
    return ""
