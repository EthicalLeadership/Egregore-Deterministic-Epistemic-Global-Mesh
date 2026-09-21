"""Plane-2 writer for SelfRep dossier outputs."""

from __future__ import annotations

from pathlib import Path

from egregore.domain.self_rep_dossier.dossier_models import Dossier
from egregore.domain.self_rep_dossier.output_generator import (
    render_json,
    render_markdown,
)


def write_dossier_outputs(
    dossier: Dossier, output_dir: Path | str
) -> tuple[Path, Path]:
    """Write Markdown and JSON outputs and return their paths."""
    output_dir = Path(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)

    md_path = output_dir / f"{dossier.case_id}_self_rep_dossier.md"
    json_path = output_dir / f"{dossier.case_id}_self_rep_dossier.json"

    md_path.write_text(render_markdown(dossier), encoding="utf-8")
    json_path.write_text(render_json(dossier), encoding="utf-8")

    return md_path, json_path
