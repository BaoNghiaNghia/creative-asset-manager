from __future__ import annotations

import re
import unicodedata
from typing import Literal

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
