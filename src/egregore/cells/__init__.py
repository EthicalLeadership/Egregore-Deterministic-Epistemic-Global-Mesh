"""Cell execution layer for the Egregore University / Guildhall.

A *cell* is a governed, reproducible micro-factory declared by a YAML spec under
``cells/<cell_id>/spec.yaml``. The ``egregore.cells`` package loads those specs,
executes their staged pipelines through local GGUF models, and adapts their
outputs into RFE-compatible evidence streams.
"""

from egregore.cells.executor import CellExecutor, CellResult, StageOutput
from egregore.cells.models import CellSpec, CellType, Stage, Taxonomy
from egregore.cells.registry import CellRegistry
from egregore.cells.rfe_adapter import build_manifest, cell_result_to_stream

__all__ = [
    "CellExecutor",
    "CellRegistry",
    "CellResult",
    "CellSpec",
    "CellType",
    "Stage",
    "StageOutput",
    "Taxonomy",
    "build_manifest",
    "cell_result_to_stream",
]
