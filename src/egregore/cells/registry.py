"""Cell registry: discover, validate, and index cell specs on disk.

The registry scans ``cells/<cell_id>/spec.yaml`` files, validates them with
Pydantic, registers them with the BCCBP SQLite controller, and builds taxonomy
and load indices for the Ombudsman Router v2.
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

# PyYAML has no PEP 561 stubs; ignore for compatibility.
import yaml  # type: ignore[import-untyped]

from egregore.cells.models import CellSpec
from egregore.governance.cell_protocol import CellProtocolController

logger = logging.getLogger("egregore.cells.registry")


def _default_cells_dir() -> Path:
    candidates = [
        Path(__file__).resolve().parents[3] / "cells",
        Path("/opt/egregore/cells"),
        Path("cells"),
    ]
    for candidate in candidates:
        if candidate.exists():
            return candidate
    return candidates[0]


class CellRegistry:
    """In-memory registry of discovered cell specs.

    Loads specs lazily on first use and can be refreshed. Load indices are kept
    in memory only; they are not persisted to SQLite.
    """

    def __init__(
        self,
        cells_dir: Path | str | None = None,
        controller: CellProtocolController | None = None,
    ) -> None:
        self.cells_dir = Path(cells_dir or _default_cells_dir()).resolve()
        self.controller = controller or CellProtocolController()
        self._specs: dict[str, CellSpec] | None = None
        self._load_index: dict[str, float] = {}

    def _discover_spec_paths(self) -> list[Path]:
        if not self.cells_dir.exists():
            logger.warning("Cells directory does not exist: %s", self.cells_dir)
            return []
        specs = sorted(self.cells_dir.glob("*/spec.yaml"))
        logger.debug("Discovered %d cell specs in %s", len(specs), self.cells_dir)
        return specs

    def refresh(self) -> CellRegistry:
        """Reload all specs from disk and register them with BCCBP."""
        specs: dict[str, CellSpec] = {}
        for spec_path in self._discover_spec_paths():
            try:
                raw = yaml.safe_load(spec_path.read_text(encoding="utf-8"))
                spec = CellSpec.model_validate(raw)
                # Register with BCCBP (idempotent upsert).
                self.controller.register_cell(spec_path)
                specs[spec.cell_id] = spec
                logger.info("Registered cell %s from %s", spec.cell_id, spec_path)
            except Exception as exc:  # noqa: BLE001
                logger.warning("Failed to register cell from %s: %s", spec_path, exc)

        self._specs = specs
        self._load_index = dict.fromkeys(specs, 0.0)
        return self

    def _ensure_loaded(self) -> None:
        if self._specs is None:
            self.refresh()

    @property
    def specs(self) -> dict[str, CellSpec]:
        self._ensure_loaded()
        if self._specs is None:
            raise RuntimeError("Cell registry failed to load")
        return self._specs

    def get(self, cell_id: str) -> CellSpec:
        self._ensure_loaded()
        if self._specs is None:
            raise RuntimeError("Cell registry failed to load")
        if cell_id not in self._specs:
            raise KeyError(f"Cell not registered: {cell_id}")
        return self._specs[cell_id]

    def list_cells(self) -> list[CellSpec]:
        return list(self.specs.values())

    def find_by_taxonomy(self, taxonomy: str) -> list[CellSpec]:
        """Return cells whose taxonomy path starts with the requested prefix.

        The match is case-insensitive and ignores leading/trailing slashes.
        """
        prefix = taxonomy.strip().strip("/").lower()
        return [
            spec
            for spec in self.list_cells()
            if spec.taxonomy_path().lower().startswith(prefix)
        ]

    def get_load(self, cell_id: str) -> float:
        return self._load_index.get(cell_id, 1.0)

    def set_load(self, cell_id: str, load: float) -> None:
        if cell_id in self._load_index:
            self._load_index[cell_id] = max(0.0, min(1.0, float(load)))

    def increment_load(self, cell_id: str, delta: float = 0.1) -> float:
        current = self.get_load(cell_id)
        new_load = max(0.0, min(1.0, current + delta))
        self.set_load(cell_id, new_load)
        return new_load

    def select_least_loaded(self, candidates: list[CellSpec]) -> CellSpec | None:
        """Pick the delivered/available cell with the lowest load index.

        Cells with load >= max_load are excluded. A tiny jitter breaks ties
        deterministically only when loads are identical; we use the cell_id for
        stable tie-breaking instead of randomness so tests remain reproducible.
        """
        available = [
            spec for spec in candidates if self.get_load(spec.cell_id) < spec.max_load
        ]
        if not available:
            return None
        return min(
            available,
            key=lambda spec: (self.get_load(spec.cell_id), spec.cell_id),
        )

    def to_summary(self) -> list[dict[str, Any]]:
        return [
            {
                "cell_id": spec.cell_id,
                "version": spec.version,
                "type": spec.type,
                "tier": spec.tier,
                "taxonomy": spec.taxonomy_path(),
                "max_load": spec.max_load,
                "current_load": self.get_load(spec.cell_id),
                "advisory_cells": spec.advisory_cells,
            }
            for spec in self.list_cells()
        ]
