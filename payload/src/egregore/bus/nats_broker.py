# epistemic marker: provenance / auditability
from __future__ import annotations

from collections.abc import Callable, Iterable, Sequence
from dataclasses import dataclass
from typing import Any, Protocol


@dataclass(frozen=True)
class StreamBootstrapConfig:
    name: str
    subjects: Sequence[str]
    retention: str
    storage: str
    max_age_seconds: int


class JetStreamLike(Protocol):
    async def add_stream(self, config: Any) -> Any: ...


def _default_stream_config(
    cfg: StreamBootstrapConfig,
) -> Any:
    """
    Adapter factory for a jetstream StreamConfig-like object.

    We intentionally keep this generic because we want tests to run without nats-py installed.
    """
    return {
        "name": cfg.name,
        "subjects": list(cfg.subjects),
        "retention": cfg.retention,
        "storage": cfg.storage,
        "max_age": cfg.max_age_seconds,
    }


def _is_idempotent_conflict(
    exc: BaseException, *, idempotent_markers: Sequence[str]
) -> bool:
    msg = str(exc).lower()
    return any(marker.lower() in msg for marker in idempotent_markers)


async def bootstrap_jetstream(
    js: JetStreamLike,
    *,
    stream_configs: Iterable[StreamBootstrapConfig],
    stream_config_factory: Callable[
        [StreamBootstrapConfig], Any
    ] = _default_stream_config,
    idempotent_markers: Sequence[str] = (
        "badstreamerror",
        "already exists",
        "duplicate",
    ),
    ignore_exceptions: bool = True,
) -> None:
    """
    Idempotently bootstrap JetStream streams.

    Contract:
    - For each StreamBootstrapConfig:
      - call js.add_stream(stream_config_factory(cfg))
      - if an exception indicates an already-existing stream, ignore it
      - otherwise re-raise

    This is deliberately dependency-light (no nats-py imports).
    """
    for cfg in stream_configs:
        stream_cfg = stream_config_factory(cfg)
        try:
            await js.add_stream(stream_cfg)
        except BaseException as exc:  # noqa: BLE001
            if ignore_exceptions and _is_idempotent_conflict(
                exc, idempotent_markers=idempotent_markers
            ):
                continue
            raise
