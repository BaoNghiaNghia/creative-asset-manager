"""Offline Shopify product CSV preflight for Branding DNA (read-only).

This adapter parses a merchant-supplied export, never fetches Shopify or an
image URL, and never treats self-declared ownership as verified authority.
It creates no durable evidence, task, sales claims or media objects.
"""
from __future__ import annotations

import csv
import hashlib
import io
import re
from collections import OrderedDict
from urllib.parse import urlsplit, parse_qsl

from pydantic import BaseModel, Field, ConfigDict

MAX_CSV_BYTES = 2_000_000
MAX_CSV_ROWS = 1000
MAX_PRODUCTS = 250
MAX_COLUMNS = 80
MAX_ISSUES = 60
REQUIRED_HEADERS = ("Handle", "Title", "Variant SKU", "Image Src", "Image Position")
HANDLE = re.compile(r"^[a-z0-9][a-z0-9-]{0,119}$")


class ShopifyCsvError(ValueError):
    pass


class ShopifyCsvRowIssue(BaseModel):
    model_config = ConfigDict(extra="forbid")
    row: int
    code: str


class ShopifyCsvProduct(BaseModel):
    model_config = ConfigDict(extra="forbid")
    listing_key: str
    title: str
    category: str | None = None
    variant_count: int
    image_count: int
    source_url_sha256: list[str]
    warnings: list[str] = Field(default_factory=list)


def _validated_image_hash(value: str) -> str:
    if len(value) > 2048 or not value.isascii():
        raise ShopifyCsvError("image_url_invalid")
    try:
        parts = urlsplit(value)
        host = (parts.hostname or "").lower().rstrip(".")
        if (
            parts.scheme != "https"
            or parts.username is not None
            or parts.password is not None
            or parts.port not in (None, 443)
            or host != "cdn.shopify.com"
            or not parts.path.startswith("/s/files/")
            or parts.fragment
        ):
            raise ShopifyCsvError("image_url_not_approved_cdn")
        if any(k != "v" or not v.isdecimal() for k, v in parse_qsl(parts.query, keep_blank_values=True)):
            raise ShopifyCsvError("image_url_unsafe_query")
    except ValueError as exc:
        raise ShopifyCsvError("image_url_invalid") from exc
    # This hashes the full locator, not the image bytes. Never use as asset_sha256.
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def preview_shopify_csv(csv_text: str) -> dict:
    if not isinstance(csv_text, str) or not csv_text:
        raise ShopifyCsvError("csv_empty")
    raw = csv_text.encode("utf-8", "strict")
    if len(raw) > MAX_CSV_BYTES or "\x00" in csv_text:
        raise ShopifyCsvError("csv_invalid_size_or_null")
    file_digest = hashlib.sha256(raw).hexdigest()

    stream = io.StringIO(csv_text.lstrip("\ufeff"), newline="")
    reader = csv.reader(stream, strict=True)
    try:
        headers = next(reader)
    except (StopIteration, csv.Error) as exc:
        raise ShopifyCsvError("csv_invalid_header") from exc
    headers = [header.strip() for header in headers]
    if (
        not headers
        or len(headers) > MAX_COLUMNS
        or len(set(headers)) != len(headers)
        or any(header not in headers for header in REQUIRED_HEADERS)
    ):
        raise ShopifyCsvError("csv_invalid_shopify_headers")
    index = {name: headers.index(name) for name in REQUIRED_HEADERS}
    type_index = headers.index("Type") if "Type" in headers else None
    products: OrderedDict[str, dict] = OrderedDict()
    issues: list[dict] = []
    invalid_count = 0
    row_count = 0

    def issue(row: int, code: str):
        nonlocal invalid_count
        invalid_count += 1
        if len(issues) < MAX_ISSUES:
            issues.append(ShopifyCsvRowIssue(row=row, code=code).model_dump())

    try:
        for line, cells in enumerate(reader, start=2):
            row_count += 1
            if row_count > MAX_CSV_ROWS:
                raise ShopifyCsvError("csv_too_many_rows")
            if len(cells) != len(headers):
                issue(line, "csv_column_count_mismatch")
                continue
            handle, title, sku, url, position = (
                cells[index[column]].strip() for column in REQUIRED_HEADERS
            )
            if not HANDLE.fullmatch(handle):
                issue(line, "handle_invalid")
                continue
            if handle not in products:
                if len(products) >= MAX_PRODUCTS:
                    raise ShopifyCsvError("csv_too_many_products")
                products[handle] = {
                    "title": "", "category": None, "variants": set(),
                    "positions": {}, "images": set(), "warnings": set(),
                }
            product = products[handle]
            if title:
                if len(title) > 300:
                    issue(line, "title_too_long")
                elif product["title"] and product["title"] != title:
                    issue(line, "title_inconsistent")
                else:
                    product["title"] = title
            if type_index is not None:
                category = cells[type_index].strip()
                if category and len(category) <= 80:
                    if product["category"] not in (None, category):
                        issue(line, "category_inconsistent")
                    else:
                        product["category"] = category
            if sku:
                if len(sku) > 128:
                    issue(line, "sku_too_long")
                else:
                    product["variants"].add(sku)
            if url:
                try:
                    digest = _validated_image_hash(url)
                    if not position.isdigit() or int(position) < 1 or int(position) > 100:
                        issue(line, "image_position_invalid")
                        continue
                    pos = int(position)
                    previous = product["positions"].get(pos)
                    if previous and previous != digest:
                        issue(line, "image_position_conflict")
                    else:
                        product["positions"][pos] = digest
                        product["images"].add(digest)
                except ShopifyCsvError as exc:
                    issue(line, str(exc))
            elif position:
                issue(line, "image_position_without_image")
    except csv.Error as exc:
        raise ShopifyCsvError("csv_malformed") from exc

    items: list[dict] = []
    for handle, p in products.items():
        if not p["title"]:
            issue(0, "product_missing_title")
        if not p["images"]:
            p["warnings"].add("image_locator_missing")
        if not p["variants"]:
            p["warnings"].add("sku_missing")
        positions = sorted(p["positions"])
        if positions and positions != list(range(1, len(positions) + 1)):
            p["warnings"].add("gallery_position_gaps")
        items.append(ShopifyCsvProduct(
            listing_key=handle, title=p["title"],
            category=p["category"], variant_count=len(p["variants"]),
            image_count=len(p["images"]),
            source_url_sha256=[p["positions"][pos] for pos in positions],
            warnings=sorted(p["warnings"]),
        ).model_dump())

    return {
        "format": "shopify_products_csv",
        "adapter_version": "offline_shopify_csv_v1",
        "source_sha256": file_digest,
        "row_count": row_count,
        "product_count": len(items),
        "issue_count": invalid_count,
        "issues": issues,
        "items": items,
        "rights_verified": False,
        "source_identity_verified": False,
        "evidence_committed": False,
        "scan_enabled": False,
        "blockers": [
            "merchant_export_origin_not_verified",
            "image_content_not_downloaded_or_verified",
            "source_rights_not_verified",
            "preview_only_no_dispatch",
        ],
    }
