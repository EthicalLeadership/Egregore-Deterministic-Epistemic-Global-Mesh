"""ANCHORUM forensic document intelligence engine."""

__all__ = [
    "PdfDocument",
    "PdfPharosEngine",
    "DocumentVerdict",
    "MetadataExtractor",
    "MetadataPlane",
    "SignaturePharos",
    "SignatureVerdict",
    "HiddenLayerDetector",
    "HiddenLayerVerdict",
    "IntegrityAttestor",
    "IntegrityAttestation",
    "FusedManifest",
    "compile_fused_manifest",
    "FilesystemVaultAdapter",
    "VaultReceipt",
    "recover_revisions",
    "DocumentRevisionReport",
    "RevisionEntry",
    "CommentEntry",
    "VersionMetadata",
    "liberate",
    "detect_obstruction",
    "SignalAudit",
    "EventReference",
    "analyze_ocr",
    "OcrEnginePort",
    "OcrWord",
    "OcrPageResult",
    "OcrReport",
    "TesseractCliEngine",
    "detect_steganography",
    "StegoToolPort",
    "SteghideTool",
    "ZstegTool",
    "LsbAnalysis",
    "EntropyAnalysis",
    "ToolResult",
    "StegoReport",
]


def __getattr__(name: str):  # type: ignore[no-redef]  # noqa: C901
    """Lazy import document submodules so stdlib-only code can import PdfDocument."""
    if name == "PdfDocument":
        from anchorum.forensic.core.document.pdf_document import PdfDocument

        return PdfDocument

    if name in ("PdfPharosEngine", "DocumentVerdict"):
        from anchorum.forensic.core.document.pdf_pharos_engine import (  # noqa: F401
            DocumentVerdict,
            PdfPharosEngine,
        )

        return locals()[name]

    if name in ("MetadataExtractor", "MetadataPlane"):
        from anchorum.forensic.core.document.metadata_extraction import (  # noqa: F401
            MetadataExtractor,
            MetadataPlane,
        )

        return locals()[name]

    if name in ("SignaturePharos", "SignatureVerdict"):
        from anchorum.forensic.core.document.signature_pharos import (  # noqa: F401
            SignaturePharos,
            SignatureVerdict,
        )

        return locals()[name]

    if name in ("HiddenLayerDetector", "HiddenLayerVerdict"):
        from anchorum.forensic.core.document.hidden_layer_detection import (  # noqa: F401
            HiddenLayerDetector,
            HiddenLayerVerdict,
        )

        return locals()[name]

    if name in ("IntegrityAttestor", "IntegrityAttestation"):
        from anchorum.forensic.core.document.integrity_attestation import (  # noqa: F401
            IntegrityAttestation,
            IntegrityAttestor,
        )

        return locals()[name]

    if name in ("FusedManifest", "compile_fused_manifest"):
        from anchorum.forensic.core.document.fused_manifest_compiler import (  # noqa: F401
            FusedManifest,
            compile_fused_manifest,
        )

        return locals()[name]

    if name in ("FilesystemVaultAdapter", "VaultReceipt"):
        from anchorum.forensic.core.document.vault_adapter import (  # noqa: F401
            FilesystemVaultAdapter,
            VaultReceipt,
        )

        return locals()[name]

    if name in (
        "recover_revisions",
        "DocumentRevisionReport",
        "RevisionEntry",
        "CommentEntry",
        "VersionMetadata",
    ):
        from anchorum.forensic.core.document.office_deep_revision import (  # noqa: F401
            CommentEntry,
            DocumentRevisionReport,
            RevisionEntry,
            VersionMetadata,
            recover_revisions,
        )

        return locals()[name]

    if name == "liberate":
        from anchorum.forensic.core.document.pdf_liberation import liberate

        return liberate

    if name in ("detect_obstruction", "SignalAudit", "EventReference"):
        from anchorum.forensic.core.document.pdf_obstruction import (  # noqa: F401
            EventReference,
            SignalAudit,
            detect_obstruction,
        )

        return locals()[name]

    if name in (
        "analyze_ocr",
        "OcrEnginePort",
        "OcrWord",
        "OcrPageResult",
        "OcrReport",
        "TesseractCliEngine",
    ):
        from anchorum.forensic.core.document.ocr_confidence import (  # noqa: F401
            OcrEnginePort,
            OcrPageResult,
            OcrReport,
            OcrWord,
            TesseractCliEngine,
            analyze_ocr,
        )

        return locals()[name]

    if name in (
        "detect_steganography",
        "StegoToolPort",
        "SteghideTool",
        "ZstegTool",
        "LsbAnalysis",
        "EntropyAnalysis",
        "ToolResult",
        "StegoReport",
    ):
        from anchorum.forensic.core.document.steganography_detector import (  # noqa: F401
            EntropyAnalysis,
            LsbAnalysis,
            SteghideTool,
            StegoReport,
            StegoToolPort,
            ToolResult,
            ZstegTool,
            detect_steganography,
        )

        return locals()[name]

    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
