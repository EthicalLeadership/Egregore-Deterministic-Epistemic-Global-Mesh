"""composition_root.py — Single source of truth for service facades."""

import logging
import os
from dataclasses import dataclass
from pathlib import Path

logger = logging.getLogger(__name__)


class DossierFacade:
    def __init__(self, store, signer, policy_cache):
        self._store = store
        self._signer = signer
        self._policy_cache = policy_cache

    def generate(self, tenant_id: str, command: dict) -> dict:
        from egregore.application.dossier_generate_service import (
            DossierGenerateService,
        )

        svc = DossierGenerateService(self._store, self._signer)
        return svc.generate(tenant_id, command)


class IntakeFacade:
    def __init__(self, store, validator):
        self._store = store
        self._validator = validator

    def submit(self, tenant_id: str, metadata: dict) -> dict:
        validated = self._validator.validate(metadata)
        return self._store.append(tenant_id, validated)


class AnchorFacade:
    def __init__(self, timestamp_client, block_store):
        self._timestamp_client = timestamp_client
        self._block_store = block_store

    def anchor(self, block_hash: str) -> dict:
        token = self._timestamp_client.timestamp(block_hash)
        return {"tier": token.tier, "timestamp": token.timestamp_iso}


@dataclass
class FacadeBundle:
    dossier: DossierFacade
    intake: IntakeFacade
    anchor: AnchorFacade


class CompositionRoot:
    def __init__(self, container):
        self._container = container
        self._store = None
        self._signer = None
        self._policy_cache = {}
        self._timestamp_client = None
        self._facades = None
        self._disposed = False

    @classmethod
    def from_env(cls):
        from egregore.application.container import EgregoreContainer

        container = EgregoreContainer.from_env()
        return cls(container)

    def build_facades(self):
        if self._facades is not None:
            return self._facades
        if self._disposed:
            raise RuntimeError("CompositionRoot has been disposed")

        logger.info("[CompositionRoot] Building facades...")
        self._store = self._container.get_block_store()
        self._signer = self._container.get_signer()
        self._timestamp_client = self._container.get_timestamp_client()
        self._policy_cache = self._load_policy_cache()

        self._facades = FacadeBundle(
            dossier=DossierFacade(
                store=self._store, signer=self._signer, policy_cache=self._policy_cache
            ),
            intake=IntakeFacade(
                store=self._store, validator=self._container.get_validator()
            ),
            anchor=AnchorFacade(
                timestamp_client=self._timestamp_client, block_store=self._store
            ),
        )
        logger.info("[CompositionRoot] Facades built")
        return self._facades

    def dispose(self):
        if self._disposed:
            return
        logger.info("[CompositionRoot] Disposing...")
        if hasattr(self._store, "close"):
            self._store.close()
        self._policy_cache.clear()
        self._store = None
        self._signer = None
        self._timestamp_client = None
        self._facades = None
        self._disposed = True
        logger.info("[CompositionRoot] Disposed")

    def _load_policy_cache(self):
        cache = {}
        policy_dir = os.environ.get("BLACKSTAR_POLICY_DIR", "./policies")
        if not os.path.exists(policy_dir):
            return cache
        for policy_file in Path(policy_dir).glob("*.json"):
            try:
                import json

                with open(policy_file) as f:
                    policy = json.load(f)
                vertical = policy.get("vertical", policy_file.stem)
                version = policy.get("version", "0.0.0")
                cache[f"{vertical}:{version}"] = policy
            except Exception as e:
                logger.warning(f"Failed to load policy {policy_file}: {e}")
        logger.info(f"[CompositionRoot] Loaded {len(cache)} policies")
        return cache

    @property
    def is_disposed(self):
        return self._disposed

    @property
    def facades(self):
        return self._facades
