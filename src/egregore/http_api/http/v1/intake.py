from __future__ import annotations

import hashlib
from typing import Any

# Optional dependency guard (same pattern as dossiers.py / workflows.py)
try:
    from fastapi import APIRouter, Depends, File, Form, HTTPException, UploadFile
    from pydantic import BaseModel
except ModuleNotFoundError:  # pragma: no cover
    APIRouter = None  # type: ignore[assignment,misc]
    Depends = None  # type: ignore[assignment,misc]
    HTTPException = Exception  # type: ignore[misc]
    UploadFile = None  # type: ignore[assignment,misc]
    File = None  # type: ignore[assignment,misc]
    Form = None  # type: ignore[assignment,misc]
    BaseModel = object  # type: ignore[misc]

import importlib


def _get_facade_dep() -> Any:
    """Lazy import to keep AST-visible imports out of interface modules."""
    mod = importlib.import_module("egregore.infrastructure.bootstrap")
    return mod.get_dossier_facade()


def _build_router() -> Any:  # noqa: C901
    if APIRouter is None:  # pragma: no cover
        return None

    router = APIRouter()

    class UploadResponse(BaseModel):  # type: ignore[misc]
        status: str
        intake_id: str
        files_processed: int
        results: list[dict[str, Any]]

    @router.post("/v1/intake/upload", response_model=UploadResponse)
    async def intake_upload(
        organization_id: str = Form(...),  # type: ignore[call-arg]
        case_id: str = Form(...),  # type: ignore[call-arg]
        actor_id: str = Form(...),  # type: ignore[call-arg]
        causality_id: str = Form(...),  # type: ignore[call-arg]
        vertical: str = Form("cannabis"),  # type: ignore[call-arg]
        documents: list[UploadFile] = File(...),  # type: ignore[call-arg]  # noqa: B008
        facade: Any = Depends(_get_facade_dep),  # type: ignore[call-arg]  # noqa: B008
    ) -> dict[str, Any]:
        """
        Accept multipart file uploads, extract text, classify, and run through
        the deterministic dossier generation core.
        """
        try:
            # Lazy import application-layer intake adapter
            intake_mod = importlib.import_module(
                "egregore.application.document_intake"
            )
            extract_document = intake_mod.extract_document
            build_dossier_request_from_intake = (
                intake_mod.build_dossier_request_from_intake
            )
        except Exception as exc:
            raise HTTPException(
                status_code=500, detail="intake_adapter_unavailable"
            ) from exc

        if not documents:
            raise HTTPException(status_code=400, detail="no_documents_provided")

        extracted: list[Any] = []
        for upload in documents:
            if upload.filename is None:
                continue
            try:
                file_bytes = await upload.read()
                doc = extract_document(file_bytes, filename=upload.filename)
                extracted.append(doc)
            except Exception as exc:
                raise HTTPException(
                    status_code=422,
                    detail=f"extraction_failed for {upload.filename}: {exc}",
                ) from exc
            finally:
                await upload.close()

        if not extracted:
            raise HTTPException(status_code=400, detail="no_valid_documents")

        request = build_dossier_request_from_intake(
            organization_id=organization_id,
            case_id=case_id,
            actor_id=actor_id,
            causality_id=causality_id,
            vertical=vertical if vertical.strip() else None,
            documents=extracted,
        )

        try:
            ack = facade.generate(request=request)
        except Exception as exc:
            raise HTTPException(status_code=500, detail="generation_failed") from exc

        # Deterministic intake ID from canonical payload hash
        intake_id = hashlib.sha256(
            f"{organization_id}|{case_id}|{causality_id}".encode()
        ).hexdigest()[:16]

        results: list[dict[str, Any]] = []
        for doc in extracted:
            results.append(
                {
                    "file": doc.filename,
                    "fingerprint": doc.fingerprint,
                    "version_id": ack.result.version_id if ack.result else None,
                    "version_number": ack.result.version_number if ack.result else None,
                    "outbox_ids": ack.outbox_ids,
                }
            )

        return {
            "status": "ok",
            "intake_id": intake_id,
            "files_processed": len(extracted),
            "results": results,
        }

    return router


router = _build_router()
