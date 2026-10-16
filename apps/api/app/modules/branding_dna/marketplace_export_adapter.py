"""Offline Etsy and Amazon seller-export preflight, strictly read-only.

This code does NOT connect to marketplaces, fetch URLs or interpret a CSV as
permission to collect private data. It provides preview metadata only.
"""
from __future__ import annotations

import csv
import hashlib
import io
import re
from collections import OrderedDict
from urllib.parse import urlsplit

from app.modules.branding_dna.shopify_csv_adapter import (
    MAX_CSV_BYTES, MAX_CSV_ROWS, MAX_PRODUCTS, MAX_COLUMNS, MAX_ISSUES,
)

SKU_SAFE = re.compile(r"^[^\x00-\x1f\x7f]{1,128}$")
ASIN_SAFE = re.compile(r"^[A-Z0-9]{10}$")
LISTING_SAFE = re.compile(r"^[0-9]{1,20}$")
ETSY_IMAGE_HOST = re.compile(r"(?:i|img[0-9]+)\.etsystatic\.com", re.IGNORECASE)
ETSY_IMAGE_HEADER = re.compile(r"IMAGE([1-9][0-9]?)$", re.IGNORECASE)


class MarketplaceExportError(ValueError):
    pass


def _read_rows(content: str, *, delimiter: str) -> tuple[list[str], list[tuple[int, dict[str, str]]], str]:
    if not isinstance(content, str) or not content:
        raise MarketplaceExportError("export_empty")
    encoded = content.encode("utf-8", "strict")
    if len(encoded) > MAX_CSV_BYTES or "\x00" in content:
        raise MarketplaceExportError("export_invalid_size_or_null")
    digest = hashlib.sha256(encoded).hexdigest()
    reader = csv.reader(io.StringIO(content.lstrip("\ufeff"), newline=""), delimiter=delimiter, strict=True)
    try:
        original = next(reader)
        headers = [name.strip().casefold() for name in original]
        if len(headers) > MAX_COLUMNS or not headers or len(set(headers)) != len(headers) or any(not h for h in headers):
            raise MarketplaceExportError("export_headers_invalid")
        rows = []
        for line, values in enumerate(reader, start=2):
            if line > MAX_CSV_ROWS + 1:
                raise MarketplaceExportError("export_too_many_rows")
            # Reject malformed line shape; don't reinterpret a column shifted value.
            if len(values) != len(headers):
                raise MarketplaceExportError("export_row_columns_mismatch")
            rows.append((line, dict(zip(headers, [v.strip() for v in values]))))
        return headers, rows, digest
    except (csv.Error, UnicodeError) as exc:
        raise MarketplaceExportError("export_csv_invalid") from exc


def _etsy_image_hash(value: str) -> str:
    if len(value) > 2048 or not value.isascii():
        raise MarketplaceExportError("etsy_image_url_invalid")
    try:
        parts = urlsplit(value)
        host = (parts.hostname or "").lower().rstrip(".")
        if (
            parts.scheme != "https" or not ETSY_IMAGE_HOST.fullmatch(host)
            or parts.username or parts.password or parts.port not in (None, 443)
            or not parts.path.startswith("/") or not parts.path.lower().endswith((".jpg", ".jpeg", ".png", ".webp"))
            or parts.query or parts.fragment
        ):
            raise MarketplaceExportError("etsy_image_url_not_allowed")
    except ValueError as exc:
        raise MarketplaceExportError("etsy_image_url_invalid") from exc
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def _base_report(name: str, revision: str, sha256: str, rows: int, items: list[dict], issues: list[dict],
                 issue_count: int) -> dict:
    return {
        "format": name,
        "adapter_version": revision,
        "source_sha256": sha256,
        "row_count": rows,
        "product_count": len(items),
        "issue_count": issue_count,
        "issues": issues[:MAX_ISSUES],
        "items": items,
        "rights_verified": False,
        "source_identity_verified": False,
        "evidence_committed": False,
        "scan_enabled": False,
        "sales_verified": False,
        "coverage_state": "unknown",
        "blockers": [
            "export_origin_not_verified",
            "seller_authorization_not_verified",
            "media_bytes_not_downloaded_or_verified",
            "no_sales_or_bestseller_verification",
            "preview_only_no_dispatch",
        ],
    }


def preview_etsy_listings_csv(content: str) -> dict:
    """Etsy Shop Manager Download Data CSV (listing IDs may not be provided)."""
    headers, rows, digest = _read_rows(content, delimiter=",")
    if "title" not in headers or not any(h.startswith("image") and ETSY_IMAGE_HEADER.fullmatch(h) for h in headers):
        raise MarketplaceExportError("etsy_csv_headers_invalid")
    if len(rows) > MAX_PRODUCTS:
        raise MarketplaceExportError("export_too_many_products")
    image_headers = sorted(
        [h for h in headers if ETSY_IMAGE_HEADER.fullmatch(h)],
        key=lambda h: int(ETSY_IMAGE_HEADER.fullmatch(h).group(1)),
    )
    issues: list[dict] = []
    issue_count = 0

    def issue(line: int, code: str):
        nonlocal issue_count
        issue_count += 1
        if len(issues) < MAX_ISSUES:
            issues.append({"row": line, "code": code})

    seen_keys = set()
    items = []
    for line, row in rows:
        title = row["title"]
        warnings = []
        if not title or len(title) > 300:
            issue(line, "etsy_title_invalid")
        listing_id = row.get("listing_id", "")
        if listing_id and not LISTING_SAFE.fullmatch(listing_id):
            issue(line, "etsy_listing_id_invalid")
            listing_id = ""
        # CSV exports often do not contain the Etsy stable listing ID.
        # Use a file-position identifier, explicitly provisional. Never treat
        # SKU, Title or row position as canonical listing identity.
        if listing_id:
            key = "etsy:" + listing_id
            identity_quality = "export_claim_unverified"
        else:
            key = "etsy-export-row:" + str(line)
            identity_quality = "provisional_not_canonical"
            warnings.append("listing_id_missing")
        if key in seen_keys:
            issue(line, "etsy_listing_id_repeated")
        seen_keys.add(key)

        sku = row.get("sku", "")
        if not sku:
            warnings.append("sku_missing")
        elif not SKU_SAFE.fullmatch(sku):
            issue(line, "etsy_sku_invalid")
        image_hashes = []
        for image_column in image_headers:
            locator = row.get(image_column, "")
            if not locator:
                continue
            try:
                digest_url = _etsy_image_hash(locator)
                if digest_url not in image_hashes:
                    image_hashes.append(digest_url)
            except MarketplaceExportError as exc:
                issue(line, str(exc))
        if not image_hashes:
            warnings.append("image_locator_missing")
        category = row.get("category") or None
        if category and len(category) > 80:
            issue(line, "etsy_category_too_long")
            category = None
        items.append({
            "listing_key": key,
            "identity_quality": identity_quality,
            "title": title[:300],
            "category": category,
            "variant_count": 1 if sku else 0,  # SKU presence only; variants not enumerated.
            "image_count": len(image_hashes),
            "source_url_sha256": image_hashes,
            "warnings": sorted(warnings),
        })
    return _base_report("etsy_listings_csv", "offline_etsy_csv_v1", digest, len(rows), items, issues, issue_count)


def preview_amazon_inventory_report(content: str) -> dict:
    """Amazon Seller Central inventory report (CSV or tab-delimited).

    Only SKU, ASIN and display-name metadata are used. Files that do not
    include these fields are rejected; no guessing from purchase/order reports.
    """
    first_line = content.lstrip("\ufeff").splitlines()[0] if content else ""
    delimiter = "\t" if first_line.count("\t") > first_line.count(",") else ","
    headers, rows, digest = _read_rows(content, delimiter=delimiter)
    sku_field = next((h for h in ("seller-sku", "sku") if h in headers), None)
    name_field = next((h for h in ("item-name", "product-name") if h in headers), None)
    asin_field = next((h for h in ("asin1", "asin") if h in headers), None)
    if sku_field is None or name_field is None:
        raise MarketplaceExportError("amazon_inventory_headers_invalid")
    groups: OrderedDict[str, dict] = OrderedDict()
    issues: list[dict] = []
    issue_count = 0

    def issue(line: int, code: str):
        nonlocal issue_count
        issue_count += 1
        if len(issues) < MAX_ISSUES:
            issues.append({"row": line, "code": code})

    for line, row in rows:
        sku, title = row[sku_field], row[name_field]
        asin = row[asin_field].upper() if asin_field else ""
        if not sku or not SKU_SAFE.fullmatch(sku):
            issue(line, "amazon_sku_invalid")
            continue
        if not title or len(title) > 300:
            issue(line, "amazon_title_invalid")
            continue
        if asin and not ASIN_SAFE.fullmatch(asin):
            issue(line, "amazon_asin_invalid")
            asin = ""
        # Do not guess a stable ASIN when absent; SKU identity is seller-local.
        key = "amazon-asin:" + asin if asin else "amazon-seller-sku:" + hashlib.sha256(sku.encode()).hexdigest()[:24]
        if key not in groups:
            if len(groups) >= MAX_PRODUCTS:
                raise MarketplaceExportError("export_too_many_products")
            groups[key] = {
                "listing_key": key,
                "identity_quality": "export_claim_unverified" if asin else "seller_sku_only_unverified",
                "title": title, "category": None,
                "skus": set(), "image_count": 0, "source_url_sha256": [],
                "warnings": {"image_locator_missing"},
            }
        current = groups[key]
        if current["title"] != title:
            current["warnings"].add("title_differs_between_skus")
        if sku in current["skus"]:
            issue(line, "amazon_duplicate_sku")
        current["skus"].add(sku)
        if not asin:
            current["warnings"].add("asin_missing")
    items = []
    for g in groups.values():
        items.append({
            "listing_key": g["listing_key"],
            "identity_quality": g["identity_quality"],
            "title": g["title"], "category": g["category"],
            "variant_count": len(g["skus"]),
            "image_count": g["image_count"],
            "source_url_sha256": [],
            "warnings": sorted(g["warnings"]),
        })
    return _base_report("amazon_inventory_report", "offline_amazon_inventory_v1", digest, len(rows),
                        items, issues, issue_count)
