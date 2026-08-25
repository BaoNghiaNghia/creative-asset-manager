from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator


class V4Contract(BaseModel):
    model_config = ConfigDict(extra="forbid")


class CellEvidence(V4Contract):
    sheet: str = Field(min_length=1)
    cell: str = Field(pattern=r"^[A-Z]+[1-9][0-9]*$")
    raw_value: Any = None
    formula: str | None = None
    evidence_hash: str = Field(min_length=64, max_length=64)


class EvidenceReference(V4Contract):
    sheet: str = Field(min_length=1)
    cell: str = Field(pattern=r"^[A-Z]+[1-9][0-9]*$")
    evidence_hash: str = Field(min_length=64, max_length=64)


class WorkbookAssessmentObservation(V4Contract):
    code: str = Field(min_length=1, max_length=128)
    conclusion: str = Field(min_length=1)
    evidence: list[EvidenceReference] = Field(min_length=1)
    confidence: float = Field(ge=0.0, le=1.0)


class WorkbookAssessmentUncertainty(V4Contract):
    code: str = Field(min_length=1, max_length=128)
    message: str = Field(min_length=1)
    evidence: list[EvidenceReference] = Field(default_factory=list)


class WorkbookAssessment(V4Contract):
    summary: str = Field(min_length=1)
    observations: list[WorkbookAssessmentObservation] = Field(default_factory=list)
    uncertainties: list[WorkbookAssessmentUncertainty] = Field(default_factory=list)
    additional_reads_needed: bool = False


class StagedEditOperation(V4Contract):
    operation_id: str = Field(min_length=1, max_length=128)
    type: Literal["set_cell", "clear_cell"]
    sheet: str = Field(min_length=1)
    cell: str = Field(pattern=r"^[A-Z]+[1-9][0-9]*$")
    value: Any = None
    evidence: list[EvidenceReference] = Field(min_length=1)
    provenance: Literal["exact_copy", "transformed"]
    copy_from: EvidenceReference | None = None
    reason: str = ""
    requires_review: bool = False

    @model_validator(mode="after")
    def validate_shape(self):
        if self.type == "clear_cell" and self.value is not None:
            raise ValueError("clear_cell cannot contain a value")
        if self.type == "set_cell" and self.value is None:
            raise ValueError("set_cell requires a non-null value")
        if self.provenance == "exact_copy" and self.copy_from is None:
            raise ValueError("exact_copy requires copy_from")
        return self


class StagedIssue(V4Contract):
    code: str = Field(min_length=1)
    message: str = ""
    evidence: list[EvidenceReference] = Field(default_factory=list)
    requires_review: bool = False


class StagedMaterialAction(V4Contract):
    action: Literal["MATCH_EXISTING", "NEW_MATERIAL", "POSSIBLE_RENAME", "AMBIGUOUS"]
    material_id: str | None = None
    source_evidence: list[EvidenceReference] = Field(min_length=1)
    reason: str = ""
    requires_review: bool = False


class StagedEdits(V4Contract):
    status: Literal["ready", "review_required", "blocked"]
    summary: str = ""
    requires_review: bool = False
    operations: list[StagedEditOperation] = Field(default_factory=list)
    issues: list[StagedIssue] = Field(default_factory=list)
    material_actions: list[StagedMaterialAction] = Field(default_factory=list)


class V4AgentRunResult(V4Contract):
    version: Literal[4] = 4
    mode: Literal["gemini_tool_sheet_agent"] = "gemini_tool_sheet_agent"
    apply_mode: Literal["shadow", "review", "auto"] = "shadow"
    status: Literal["shadow", "completed", "review_required", "blocked"]
    run_id: str | None = Field(default=None, min_length=64, max_length=64)
    tenant_id: str
    spreadsheet_file_id: str
    business_date: str
    tool_rounds: int
    read_calls: int
    read_cells: int
    plan_hash: str
    staged: StagedEdits
    tools_called: list[str] = Field(default_factory=list)
    assessment_present: bool = False
    catalog_read: bool = False
    ranges_read: list[str] = Field(default_factory=list)
    tool_trace: list[dict[str, Any]] = Field(default_factory=list)
    writes: int = 0
