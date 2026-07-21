"""
BLACKSTAR LAW: DI Container
Dependency injection bootstrap. No hidden state, no global singletons.
"""

from __future__ import annotations

import contextlib
import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from egregore.application.code_factory import CodeFactoryService
from egregore.application.inference_service import (
    InferenceService,
    build_inference_service_from_env,
)
from egregore.infrastructure.block_store import BlockStore
from egregore.infrastructure.persistence.sqlite_anchor_store import SQLiteAnchorStore
from egregore.infrastructure.persistence.sqlite_dossier_adapter import (
    SQLiteTransactionalPersistence,
)
from egregore.services.anchor_orchestrator.service import AnchorOrchestrator
from egregore.services.anchor_orchestrator.timestamp_client import (
    ITimestampClient,
    LocalFallbackTimestampClient,
    MockTimestampClient,
    RFC3161TimestampClient,
)
from egregore.shared.freeze_state import FreezeController


@dataclass
class EgregoreContainer:
    """
    Production DI container. All dependencies are explicit and injectable.

    Usage:
        container = EgregoreContainer.from_env()
        orchestrator = container.anchor_orchestrator
    """

    block_store: BlockStore
    persistence: object  # ITransactionalPersistence
    anchor_store: object  # SQLiteAnchorStore or PostgresAnchorStore
    timestamp_client: ITimestampClient
    freeze_controller: FreezeController
    inference_service: InferenceService
    code_factory: CodeFactoryService
    anchor_orchestrator: AnchorOrchestrator = field(init=False)

    def __post_init__(self) -> None:
        self.anchor_orchestrator = AnchorOrchestrator(
            block_store=self.block_store,
            anchor_store=self.anchor_store,
            timestamp_client=self.timestamp_client,
            freeze_controller=self.freeze_controller,
        )

    @classmethod
    def from_env(cls) -> EgregoreContainer:
        """Bootstrap from environment variables."""
        node_id = os.environ.get("BLACKSTAR_NODE_ID", "pioneer1")
        data_dir = Path(
            os.environ.get("BLACKSTAR_DATA_DIR", f"~/egregore_data/{node_id}")
        )
        data_dir = data_dir.expanduser()

        # Block store (always local file)
        block_store = BlockStore(data_dir / "blocks.zarc")

        # Persistence tier: SQLite (default) or PostgreSQL
        dsn = os.environ.get("BLACKSTAR_DSN")
        if dsn:
            # Lazy import so the container can be built when psycopg2 is absent.
            from egregore.infrastructure.persistence.postgresql_dossier_adapter import (
                PostgreSQLTransactionalPersistence,
            )
            from egregore.infrastructure.postgres_anchor_store import (
                PostgresAnchorStore,
            )

            persistence = PostgreSQLTransactionalPersistence(
                dsn=dsn,
                zarc_dir=str(data_dir / "zarc"),
            )
            anchor_store = PostgresAnchorStore(dsn=dsn)
        else:
            db_path = str(data_dir / "node.db")
            persistence = SQLiteTransactionalPersistence(
                db_path=db_path,
                zarc_dir=str(data_dir / "zarc"),
            )
            anchor_store = SQLiteAnchorStore(db_path)

        # Timestamp client: TSA with fallback, or mock
        tsa_url = os.environ.get("BLACKSTAR_TSA_URL")
        signing_key_hex = os.environ.get("BLACKSTAR_SIGNING_KEY_HEX")
        if tsa_url and signing_key_hex:
            fallback = LocalFallbackTimestampClient(signing_key_hex)
            timestamp_client: ITimestampClient = RFC3161TimestampClient(
                tsa_url, fallback=fallback
            )
        elif signing_key_hex:
            timestamp_client = LocalFallbackTimestampClient(signing_key_hex)
        else:
            timestamp_client = MockTimestampClient()

        freeze_controller = FreezeController()

        inference_service = build_inference_service_from_env()
        code_factory = CodeFactoryService(inference_service)

        return cls(
            block_store=block_store,
            persistence=persistence,
            anchor_store=anchor_store,
            timestamp_client=timestamp_client,
            freeze_controller=freeze_controller,
            inference_service=inference_service,
            code_factory=code_factory,
        )

    @classmethod
    def for_testing(cls, tmp_path: Path) -> EgregoreContainer:
        """Minimal container for tests. All in-memory / temp files."""
        block_store = BlockStore(tmp_path / "blocks.zarc")
        persistence = SQLiteTransactionalPersistence(
            db_path=str(tmp_path / "node.db"),
            zarc_dir=str(tmp_path / "zarc"),
        )
        anchor_store = SQLiteAnchorStore(str(tmp_path / "anchors.db"))
        timestamp_client = MockTimestampClient()
        freeze_controller = FreezeController()
        # No live network clients in tests.
        default_backend = "local"
        # Pick first available backend for tests, prefer local if available
        clients: dict[str, Any] = {}
        with contextlib.suppress(Exception):
            from egregore.infrastructure.local_model_client import LocalModelClient

            lc = LocalModelClient()
            if lc.health():
                clients["local"] = lc
        inference_service = InferenceService(clients, default_backend=default_backend)
        code_factory = CodeFactoryService(inference_service)

        return cls(
            block_store=block_store,
            persistence=persistence,
            anchor_store=anchor_store,
            timestamp_client=timestamp_client,
            freeze_controller=freeze_controller,
            inference_service=inference_service,
            code_factory=code_factory,
        )
