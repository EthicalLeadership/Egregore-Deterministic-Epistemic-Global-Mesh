# epistemic marker: provenance / auditability
"""Pydantic models for cell specifications.

The schema is intentionally backward-compatible with the existing BCCBP protocol
used by ``egregore.governance.cell_protocol.CellProtocolController`` while adding
the metadata required by the Ombudsman Router v2 (type, tier, structured taxonomy,
load limits, advisory relationships, and RFE stream formatting).
"""

from __future__ import annotations

from typing import Any, Literal, cast

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

CellType = Literal["university", "guild", "investigation"]


class Taxonomy(BaseModel):
    """Structured taxonomy for University, Guildhall, or custom cells.

    Accepts either:
      - root/branch/leaf/specialty (University/Guildhall cells)
      - domain/subdomain/function/tags (investigation cells such as ANCHORUM)

    Always serializes to a slash-delimited path string for BCCBP compatibility.
    """

    model_config = ConfigDict(extra="allow")

    root: str | None = Field(
        default=None,
        description="Top-level namespace, e.g. 'university' or 'guildhall'.",
    )
    branch: str | None = Field(
        default=None, description="Faculty or trade, e.g. 'science' or 'building'."
    )
    leaf: str | None = Field(
        default=None,
        description="Department or specialty, e.g. 'mathematics' or 'carpentry'.",
    )
    specialty: str | None = Field(default=None, description="Optional sub-specialty.")

    # Alternative taxonomy vocabulary (e.g. ANCHORUM investigation cells)
    domain: str | None = Field(
        default=None, description="Investigation domain, e.g. 'investigation'."
    )
    subdomain: str | None = Field(
        default=None, description="Investigation subdomain, e.g. 'forensic'."
    )
    function: str | None = Field(
        default=None, description="Investigation function, e.g. 'document_analysis'."
    )
    tags: list[str] = Field(default_factory=list, description="Optional taxonomy tags.")

    @model_validator(mode="after")
    def _normalize(self) -> Taxonomy:
        # If domain/subdomain/function are provided, map them to root/branch/leaf.
        if self.domain and self.subdomain and self.function:
            self.root = self.domain
            self.branch = self.subdomain
            self.leaf = self.function
        if not (self.root and self.branch and self.leaf):
            raise ValueError(
                "taxonomy must provide either (root, branch, leaf) or (domain, subdomain, function)"
            )
        return self

    @field_validator("root")
    @classmethod
    def _root_must_be_university_or_guild(cls, value: str | None) -> str | None:
        if value is None:
            return value
        normalized = value.lower()
        if normalized not in {
            "university",
            "guild",
            "guildhall",
            "investigation",
            "legal",
            "audit",
        }:
            raise ValueError(f"taxonomy root not recognized: {value!r}")
        return normalized

    def to_path(self) -> str:
        parts = [cast(str, self.root), cast(str, self.branch), cast(str, self.leaf)]
        if self.specialty:
            parts.append(self.specialty)
        return "/".join(parts)

    @classmethod
    def from_path(cls, path: str) -> Taxonomy:
        parts = [p for p in path.split("/") if p]
        if len(parts) < 3:
            raise ValueError(
                f"taxonomy path must have at least 3 segments, got {path!r}"
            )
        return cls(
            root=parts[0],
            branch=parts[1],
            leaf=parts[2],
            specialty="/".join(parts[3:]) if len(parts) > 3 else None,
        )


class Input(BaseModel):
    """Declared input for a cell."""

    model_config = ConfigDict(extra="allow")

    name: str
    type: str = "string"
    description: str = ""
    required: bool = True
    schema_: dict[str, Any] | None = Field(default=None, alias="schema")


class Output(BaseModel):
    """Declared output for a cell."""

    model_config = ConfigDict(extra="allow")

    name: str
    type: str = "string"
    description: str = ""
    schema_: dict[str, Any] | None = Field(default=None, alias="schema")


class OutputFormat(BaseModel):
    """RFE stream formatting rules for a cell."""

    model_config = ConfigDict(extra="allow")

    stream_type: str = Field(default="cell_output")
    claim_field: str = Field(default="claim")
    claim_map: dict[str, str] = Field(default_factory=dict)
    schema_: dict[str, Any] | None = Field(default=None, alias="schema")


class Stage(BaseModel):
    """One stage in a cell pipeline."""

    model_config = ConfigDict(extra="allow")

    stage_id: str
    name: str = ""
    model: str | None = Field(default=None, description="Model ID for LLM stages.")
    tool: str | None = Field(
        default=None, description="Tool name for deterministic stages."
    )
    system: str | None = None
    prompt: str | None = None
    output_format: Literal["json", "code", "text"] = "text"
    max_tokens: int | None = None
    temperature: float | None = None
    depends_on: list[str] = Field(default_factory=list)

    @model_validator(mode="after")
    def _model_or_tool(self) -> Stage:
        if self.model is None and self.tool is None:
            raise ValueError(
                f"stage '{self.stage_id}' must specify either 'model' or 'tool'"
            )
        if self.model is not None and self.tool is not None:
            raise ValueError(
                f"stage '{self.stage_id}' cannot specify both 'model' and 'tool'"
            )
        return self


class Pipeline(BaseModel):
    """Cell execution pipeline."""

    model_config = ConfigDict(extra="allow")

    description: str = ""
    stages: list[Stage]


class ModelRef(BaseModel):
    """Model reference inside a cell spec."""

    model_config = ConfigDict(extra="allow")

    model_id: str
    purpose: str = ""
    path_or_alias: str | None = Field(default=None, alias="path")
    required: bool = True


class VerificationRule(BaseModel):
    """Structured verification rule."""

    model_config = ConfigDict(extra="allow")

    rule_id: str
    description: str = ""
    severity: str = "medium"
    check: str | None = None


class Verification(BaseModel):
    """Verification configuration for a cell."""

    model_config = ConfigDict(extra="allow")

    static_tools: list[str] = Field(default_factory=list)
    dynamic_tools: list[str] = Field(default_factory=list)
    rules: list[VerificationRule] = Field(default_factory=list)


class MoralRule(BaseModel):
    """Structured moral / legal rule."""

    model_config = ConfigDict(extra="allow")

    law_id: str
    description: str = ""
    enforcement: str = ""


class MoralCompliance(BaseModel):
    """Moral and legal compliance configuration."""

    model_config = ConfigDict(extra="allow")

    egregore_laws: list[MoralRule] = Field(default_factory=list)


class Artifacts(BaseModel):
    """BCCBP artifact stage-gate paths."""

    model_config = ConfigDict(extra="allow")

    stage_gates: dict[str, str]


class CellSpec(BaseModel):
    """Full cell specification.

    The ``taxonomy`` field accepts either a slash-delimited path string (legacy
    BCCBP) or a structured ``Taxonomy`` object (Ombudsman v2). It is always
    stored internally as a ``Taxonomy`` and serialized back to a path string when
    registering with the BCCBP controller.
    """

    model_config = ConfigDict(extra="allow")

    cell_id: str
    version: str
    taxonomy: Taxonomy
    owner: str
    purpose: str = ""
    type: CellType = "university"
    tier: int = Field(default=3, ge=1, le=5)
    max_load: float = Field(default=1.0, ge=0.0, le=1.0)
    advisory_cells: list[str] = Field(default_factory=list)
    inputs: list[Input] = Field(default_factory=list)
    outputs: list[Output] = Field(default_factory=list)
    output_format: OutputFormat = Field(default_factory=OutputFormat)
    pipeline: Pipeline
    models: list[ModelRef] = Field(default_factory=list)
    verification: Verification = Field(default_factory=Verification)
    moral_compliance: MoralCompliance = Field(default_factory=MoralCompliance)
    dependencies: list[str] = Field(default_factory=list)
    artifacts: Artifacts

    @field_validator("taxonomy", mode="before")
    @classmethod
    def _coerce_taxonomy(cls, value: Any) -> Taxonomy:
        if isinstance(value, Taxonomy):
            return value
        if isinstance(value, str):
            return Taxonomy.from_path(value)
        if isinstance(value, dict):
            return Taxonomy.model_validate(value)
        raise ValueError(
            f"taxonomy must be a path string or a taxonomy object, got {type(value)}"
        )

    @field_validator("artifacts", mode="before")
    @classmethod
    def _coerce_artifacts(cls, value: Any, info: Any) -> Any:
        """Accept either a BCCBP stage-gates dict or an ANCHORUM-style artifact list."""
        if isinstance(value, dict):
            return value
        if isinstance(value, list):
            # Look for a sibling stage_gates field; otherwise fall back to defaults.
            data = info.data if hasattr(info, "data") else {}
            stage_gates = data.get("stage_gates") if isinstance(data, dict) else None
            if stage_gates is None:
                cell_id = data.get("cell_id") if isinstance(data, dict) else "cell"
                stage_gates = {
                    "plan": f"cells/{cell_id}/spec.yaml",
                    "draw": f"cells/{cell_id}/artifacts/draw.json",
                    "layout": f"cells/{cell_id}/artifacts/layout.json",
                    "erect": f"cells/{cell_id}/artifacts/erect.json",
                    "build": f"cells/{cell_id}/artifacts/build.json",
                    "finish": f"cells/{cell_id}/artifacts/finish.json",
                    "inspect": f"cells/{cell_id}/artifacts/inspect.json",
                    "deliver": f"cells/{cell_id}/artifacts/deliver.json",
                }
            return {"stage_gates": stage_gates}
        raise ValueError(
            "artifacts must be a stage_gates dict or an artifact metadata list"
        )

    @model_validator(mode="after")
    def _taxonomy_root_matches_type(self) -> CellSpec:
        if self.type == "guild":
            expected = {"guild", "guildhall"}
        elif self.type == "investigation":
            expected = {"investigation", "legal", "audit"}
        else:
            expected = {"university"}
        root = cast(str, self.taxonomy.root)
        if root not in expected:
            raise ValueError(
                f"cell type '{self.type}' requires taxonomy root in {expected}, got '{root}'"
            )
        return self

    def taxonomy_path(self) -> str:
        return self.taxonomy.to_path()

    def to_bccbp_dict(self) -> dict[str, Any]:
        """Return a dict compatible with CellProtocolController._validate_spec."""
        return {
            "cell_id": self.cell_id,
            "version": self.version,
            "taxonomy": self.taxonomy_path(),
            "owner": self.owner,
            "purpose": self.purpose,
            "inputs": [i.model_dump(by_alias=True) for i in self.inputs],
            "outputs": [o.model_dump(by_alias=True) for o in self.outputs],
            "pipeline": {
                "description": self.pipeline.description,
                "stages": [
                    {
                        "stage_id": s.stage_id,
                        "depends_on": s.depends_on,
                    }
                    for s in self.pipeline.stages
                ],
            },
            "models": [m.model_dump(by_alias=True) for m in self.models],
            "verification": self.verification.model_dump(),
            "moral_compliance": self.moral_compliance.model_dump(),
            "dependencies": self.dependencies,
            "artifacts": self.artifacts.model_dump(),
        }
