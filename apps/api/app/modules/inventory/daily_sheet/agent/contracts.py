from __future__ import annotations
from typing import Literal
from pydantic import BaseModel, ConfigDict, Field, model_validator
JsonScalar = str | int | float | bool | None
class AgentContract(BaseModel):
    model_config = ConfigDict(extra="forbid")
class PlanSource(AgentContract):
    spreadsheet_file_id: str = Field(min_length=1)
    source_hash: str = Field(min_length=64, max_length=64)
    sheet: str = Field(min_length=1)
    range: str = Field(min_length=1)
class EditOperation(AgentContract):
    operation_id: str = Field(min_length=1)
    type: Literal["set_cell", "clear_cell", "insert_row", "delete_row", "insert_column", "delete_column"]
    sheet: str = Field(min_length=1)
    cell: str = Field(min_length=1)
    value: JsonScalar = None
    reason: str = ""
    business_action: Literal["carry_forward", "daily_reset", "data_repair", "other"] = "other"
    evidence_cells: list[str] = Field(default_factory=list)
    copy_from: str | None = None
    confidence: float = Field(default=1.0, ge=0, le=1)
    requires_review: bool = False
    @model_validator(mode="after")
    def validate_value_shape(self):
        if self.type == "set_cell" and self.value is None: raise ValueError("set_cell requires a non-null value")
        if self.type == "clear_cell" and self.value is not None: raise ValueError("clear_cell cannot contain a value")
        return self
class PlanIssue(AgentContract):
    type: str = Field(min_length=1)
    cells: list[str] = Field(default_factory=list)
    message: str = ""
    confidence: float = Field(default=1.0, ge=0, le=1)
    requires_review: bool = False
class MaterialAction(AgentContract):
    action: Literal["MATCH_EXISTING", "NEW_MATERIAL", "POSSIBLE_RENAME", "AMBIGUOUS"]
    source_key: str = ""
    material_id: str | None = None
    suggested_name: str | None = None
    reason: str = ""
    requires_review: bool = False
class EditPlan(AgentContract):
    plan_version: Literal[1] = 1
    status: Literal["ready", "review_required", "blocked"]
    summary: str = ""
    source: PlanSource
    operations: list[EditOperation] = Field(default_factory=list)
    issues: list[PlanIssue] = Field(default_factory=list)
    material_actions: list[MaterialAction] = Field(default_factory=list)
    requires_review: bool = False
class WorkbookSnapshot(AgentContract):
    spreadsheet_file_id: str
    spreadsheet_title: str
    workbook_timezone: str
    sheet_id: int | None = None
    sheet_title: str
    requested_range: str
    raw_values: list[list[JsonScalar]] = Field(default_factory=list)
    formulas: list[list[JsonScalar]] = Field(default_factory=list)
    coordinates: list[list[str]] = Field(default_factory=list)
    merged_ranges: list[str] = Field(default_factory=list)
    protected_ranges: list[str] = Field(default_factory=list)
    source_modified_time: str | None = None
    source_hash: str = Field(min_length=64, max_length=64)
class GuardResult(AgentContract):
    accepted: bool
    requires_review: bool
    errors: list[str] = Field(default_factory=list)
    review_reasons: list[str] = Field(default_factory=list)
    set_operations: list[EditOperation] = Field(default_factory=list)
    clear_operations: list[EditOperation] = Field(default_factory=list)
class ExecutionResult(AgentContract):
    status: Literal["completed", "blocked", "stale"]
    source_hash: str
    plan_hash: str
    set_count: int = 0
    clear_count: int = 0
    verification_status: str
    before_state: dict[str, JsonScalar] = Field(default_factory=dict)
class AgentRunResult(AgentContract):
    status: Literal["shadow", "review_required", "blocked", "completed"]
    tenant_id: str
    business_date: str
    source_hash: str
    plan_hash: str
    plan: EditPlan
    operation_count: int
    set_operations: list[EditOperation] = Field(default_factory=list)
    clear_operations: list[EditOperation] = Field(default_factory=list)
    review_operations: list[EditOperation] = Field(default_factory=list)
    blocked_reasons: list[str] = Field(default_factory=list)
    issues: list[PlanIssue] = Field(default_factory=list)
    material_suggestions: list[MaterialAction] = Field(default_factory=list)
    execution: ExecutionResult | None = None
