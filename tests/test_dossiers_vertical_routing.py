from __future__ import annotations

from egregore.http_api.http.v1 import dossiers


def test_service_for_payload_uses_fallback_when_vertical_missing(monkeypatch) -> None:
    fallback = object()
    monkeypatch.setattr(dossiers, "get_service", lambda: fallback)

    service = dossiers._service_for_payload({"policy_version": "policy_v1"})
    assert service is fallback


def test_service_for_payload_uses_vertical_service_when_vertical_present(
    monkeypatch,
) -> None:
    vertical_service = object()
    monkeypatch.setattr(dossiers, "get_service", lambda: object())
    monkeypatch.setattr(
        dossiers,
        "_build_vertical_service",
        lambda *, vertical, policy_version: (
            vertical_service
            if vertical == "legal" and policy_version == "policy_v1"
            else None
        ),
    )

    service = dossiers._service_for_payload(
        {"policy_version": "policy_v1", "vertical": "legal"}
    )
    assert service is vertical_service
