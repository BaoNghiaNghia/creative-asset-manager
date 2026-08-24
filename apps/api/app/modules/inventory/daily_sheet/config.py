from __future__ import annotations

import re
import unicodedata
from typing import Literal, TypeAlias

from pydantic import BaseModel, ConfigDict, Field, model_validator

_A1_RE = re.compile(r"^(?:'[^']+'|[^!]+)![A-Z]+[1-9][0-9]*(?::[A-Z]+[1-9][0-9]*)?$")
_A1_PARTS_RE = re.compile(r"^(?P<sheet>'[^']+'|[^!]+)!(?P<c1>[A-Z]+)(?P<r1>[1-9][0-9]*)(?::(?P<c2>[A-Z]+)(?P<r2>[1-9][0-9]*))?$")
_COLUMN_RE = re.compile(r"^[A-Z]+$")


def normalize_identifier(value: str) -> str:
    normalized = unicodedata.normalize("NFKC", value).strip().casefold()
    return re.sub(r"\s+", "_", normalized)


def normalize_sku(value: object) -> str:
    return re.sub(r"\s+", " ", unicodedata.normalize("NFKC", str(value or "")).strip()).upper()


def validate_a1(value: str) -> str:
    candidate = value.strip()
    if not _A1_RE.fullmatch(candidate):
        raise ValueError(f"invalid A1 range: {value}")
    return candidate


def _column_number(value: str) -> int:
    result = 0
    for character in value:
        result = result * 26 + ord(character) - ord("A") + 1
    return result


def _a1_bounds(value: str) -> tuple[str, int, int, int, int]:
    match = _A1_PARTS_RE.fullmatch(validate_a1(value))
    if match is None:
        raise ValueError(f"invalid A1 range: {value}")
    c1 = _column_number(match.group("c1"))
    c2 = _column_number(match.group("c2") or match.group("c1"))
    r1 = int(match.group("r1"))
    r2 = int(match.group("r2") or match.group("r1"))
    sheet = match.group("sheet").strip("'").casefold()
    return sheet, min(c1, c2), max(c1, c2), min(r1, r2), max(r1, r2)


def _overlaps(left: str, right: str) -> bool:
    ls, lc1, lc2, lr1, lr2 = _a1_bounds(left)
    rs, rc1, rc2, rr1, rr2 = _a1_bounds(right)
    return ls == rs and lc1 <= rc2 and rc1 <= lc2 and lr1 <= rr2 and rr1 <= lr2


class DailySheetModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class SourceRangeConfig(DailySheetModel):
    sheet: str = Field(min_length=1, max_length=255)
    range: str = Field(min_length=1, max_length=512)
    header_row: int = Field(default=1, ge=1)
    sku_column: str = Field(min_length=1, max_length=255)
    quantity_column: str = Field(min_length=1, max_length=255)
    warehouse_column: str = Field(min_length=1, max_length=255)

    @property
    def a1_range(self) -> str:
        if "!" in self.range:
            return validate_a1(self.range)
        return validate_a1(f"{self.sheet}!{self.range}")


class ResetConfig(DailySheetModel):
    mode: Literal["clear_ranges", "restore_template"]
    ranges: list[str] = Field(min_length=1)

    @model_validator(mode="after")
    def validate_ranges(self):
        self.ranges = [validate_a1(value) for value in self.ranges]
        if len(set(self.ranges)) != len(self.ranges):
            raise ValueError("reset ranges must be unique")
        return self


class TargetConfig(DailySheetModel):
    warehouse: str = Field(min_length=1, max_length=255)
    sheet: str = Field(min_length=1, max_length=255)
    sku_range: str = Field(min_length=1, max_length=512)
    quantity_column: str = Field(min_length=1, max_length=8)

    @model_validator(mode="after")
    def validate_target(self):
        self.sku_range = validate_a1(self.sku_range if "!" in self.sku_range else f"{self.sheet}!{self.sku_range}")
        self.quantity_column = self.quantity_column.strip().upper()
        if not _COLUMN_RE.fullmatch(self.quantity_column):
            raise ValueError("quantity_column must be an A1 column")
        return self

    @property
    def warehouse_key(self) -> str:
        return normalize_identifier(self.warehouse)

    @property
    def quantity_range(self) -> str:
        sheet, _c1, _c2, r1, r2 = _a1_bounds(self.sku_range)
        sheet_name = self.sku_range.split("!", 1)[0]
        return f"{sheet_name}!{self.quantity_column}{r1}:{self.quantity_column}{r2}"


class DailySheetConfig(DailySheetModel):
    version: Literal[1] = 1
    source_ranges: list[SourceRangeConfig] = Field(min_length=1)
    reset: ResetConfig
    targets: list[TargetConfig] = Field(min_length=1)
    allow_negative_quantity: bool = False
    new_sku_policy: Literal["reject", "ignore"] = "reject"

    @model_validator(mode="after")
    def validate_config(self):
        warehouses = [target.warehouse_key for target in self.targets]
        if len(set(warehouses)) != len(warehouses):
            raise ValueError("target warehouses must be unique")
        # Reset ranges are expected to overlap the captured source input area.
        # Only target lookup/quantity cells must be protected from reset.
        protected = [item.sku_range for item in self.targets]
        protected.extend(item.quantity_range for item in self.targets)
        for reset_range in self.reset.ranges:
            if any(_overlaps(reset_range, candidate) for candidate in protected):
                raise ValueError("reset ranges cannot overlap source or target ranges")
        return self

class DailyCountItemRowConfig(DailySheetModel):
    strategy: Literal["numeric_key"] = "numeric_key"
    key_column: str = "STT"

class DailyCountColumnsConfig(DailySheetModel):
    item_key: str = Field(min_length=1, max_length=255)
    name: str = Field(min_length=1, max_length=255)
    category: str = Field(min_length=1, max_length=255)
    opening: str = Field(min_length=1, max_length=255)
    used: str = Field(min_length=1, max_length=255)
    inbound: str = Field(min_length=1, max_length=255)
    waste: str = Field(min_length=1, max_length=255)
    closing: str = Field(min_length=1, max_length=255)

class DailyCountSourceConfig(DailySheetModel):
    sheet: str = Field(min_length=1, max_length=255)
    range: str = Field(min_length=1, max_length=512)
    header_row: int = Field(default=1, ge=1)
    item_row: DailyCountItemRowConfig = Field(default_factory=DailyCountItemRowConfig)
    columns: DailyCountColumnsConfig
    warehouse: str = Field(default="main", min_length=1, max_length=255)

    @property
    def a1_range(self) -> str:
        if "!" in self.range:
            return validate_a1(self.range)
        sheet = self.sheet.replace("'", "''")
        return validate_a1(f"'{sheet}'!{self.range}")

class DailyCountStockConfig(DailySheetModel):
    authoritative_column: Literal["closing"] = "closing"

class DailyCountResetConfig(DailySheetModel):
    mode: Literal["restore_template", "clear_entry_columns", "carry_forward"]
    ranges: list[str] = Field(default_factory=list)
    entry_columns: list[Literal["opening", "used", "inbound", "waste", "closing"]] = Field(default_factory=list)
    carry_forward_from: Literal["closing"] = "closing"
    carry_forward_to: Literal["opening"] = "opening"
    clear_columns: list[Literal["used", "inbound", "waste", "closing"]] = Field(default_factory=list)

    @model_validator(mode="after")
    def validate_reset(self):
        self.ranges = [validate_a1(value) for value in self.ranges]
        if self.mode == "restore_template" and not self.ranges:
            raise ValueError("restore_template requires ranges")
        if self.mode == "clear_entry_columns" and not self.entry_columns:
            raise ValueError("clear_entry_columns requires entry_columns")
        if self.mode == "carry_forward" and not self.clear_columns:
            raise ValueError("carry_forward requires clear_columns")
        return self

class DailyCountTargetConfig(DailySheetModel):
    warehouse: str = Field(min_length=1, max_length=255)
    sheet: str = Field(min_length=1, max_length=255)
    item_key_range: str = Field(min_length=1, max_length=512)
    quantity_column: str = Field(min_length=1, max_length=8)
    unit_column: str | None = Field(default=None, max_length=8)

    @model_validator(mode="after")
    def validate_target(self):
        self.item_key_range = validate_a1(self.item_key_range if "!" in self.item_key_range else f"{self.sheet}!{self.item_key_range}")
        for name in ("quantity_column", "unit_column"):
            value = getattr(self, name)
            if value is not None:
                value = value.strip().upper()
                if not _COLUMN_RE.fullmatch(value):
                    raise ValueError(f"{name} must be an A1 column")
                setattr(self, name, value)
        return self

class DailyCountReconciliationConfig(DailySheetModel):
    mode: Literal["report_only", "target_table"] = "report_only"
    target_spreadsheet_file_id: str | None = None
    targets: list[DailyCountTargetConfig] = Field(default_factory=list)
    name_change_policy: Literal["warn", "reject"] = "warn"
    missing_item_policy: Literal["report", "reject"] = "report"

    @model_validator(mode="after")
    def validate_mode(self):
        if self.mode == "target_table" and (not self.target_spreadsheet_file_id or not self.targets):
            raise ValueError("target_table requires target_spreadsheet_file_id and targets")
        return self

class DailyCountSheetConfig(DailySheetModel):
    version: Literal[2] = 2
    mode: Literal["daily_count_sheet"] = "daily_count_sheet"
    source: DailyCountSourceConfig
    stock: DailyCountStockConfig = Field(default_factory=DailyCountStockConfig)
    reset: DailyCountResetConfig
    reconciliation: DailyCountReconciliationConfig = Field(default_factory=DailyCountReconciliationConfig)
    new_material_policy: Literal["review_required", "auto_register_high_confidence", "ignore", "block"] = "review_required"

class SheetAgentSourceConfig(DailySheetModel):
    sheet: str = Field(min_length=1, max_length=255)
    range: str = Field(min_length=1, max_length=512)

    @property
    def a1_range(self) -> str:
        if "!" in self.range:
            return validate_a1(self.range)
        sheet = self.sheet.replace("'", "''")
        return validate_a1(f"'{sheet}'!{self.range}")


class SheetAgentConfig(DailySheetModel):
    apply_mode: Literal["shadow", "review", "auto"] = "shadow"
    business_goal: list[str] = Field(
        default_factory=lambda: [
            "Preserve final inventory as next-day opening inventory",
            "Clear daily transaction/input values after verified carry-forward",
            "Preserve workbook identity, labels, formatting and formulas",
        ],
        min_length=1,
    )


class SheetAgentSafetyConfig(DailySheetModel):
    max_edit_operations: int = Field(default=200, ge=1, le=1000)
    allow_structure_changes: bool = False
    allow_formula_changes: bool = False
    require_review_for_repairs: bool = True
    require_review_for_material_changes: bool = True


class SheetAgentReconciliationConfig(DailySheetModel):
    mode: Literal["report_only"] = "report_only"


class GeminiSheetAgentConfig(DailySheetModel):
    version: Literal[3] = 3
    mode: Literal["gemini_sheet_agent"] = "gemini_sheet_agent"
    source: SheetAgentSourceConfig
    agent: SheetAgentConfig = Field(default_factory=SheetAgentConfig)
    safety: SheetAgentSafetyConfig = Field(default_factory=SheetAgentSafetyConfig)
    reconciliation: SheetAgentReconciliationConfig = Field(default_factory=SheetAgentReconciliationConfig)


DailySheetAnyConfig: TypeAlias = DailySheetConfig | DailyCountSheetConfig | GeminiSheetAgentConfig


def parse_daily_sheet_config(value: object) -> DailySheetAnyConfig:
    if isinstance(value, (DailySheetConfig, DailyCountSheetConfig, GeminiSheetAgentConfig)):
        return value
    if isinstance(value, dict) and value.get("version") == 3:
        return GeminiSheetAgentConfig.model_validate(value)
    if isinstance(value, dict) and value.get("version") == 2:
        return DailyCountSheetConfig.model_validate(value)
    return DailySheetConfig.model_validate(value)
