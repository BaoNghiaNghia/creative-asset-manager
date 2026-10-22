"""Minimal, standards-compliant XLSX of Stage 5 synthetic review concepts.

Fixed eight-column Loox template shape. No macros, formulas, customer emails,
or claims of verified purchases. Do not represent synthetic content as reviews
from actual customers.
"""
from __future__ import annotations

import csv
import hashlib
from datetime import datetime, timedelta, timezone
import re
import unicodedata
from uuid import UUID
from io import BytesIO, StringIO
from ipaddress import ip_address
from urllib.parse import urlsplit
from zipfile import ZipFile, ZIP_DEFLATED
from xml.sax.saxutils import escape

HEADERS = (
    "product_handle", "rating", "author", "email", "body", "created_at",
    "photo_url", "verified_purchase",
)
PREFIX = "AI-generated review concept — not a real customer testimonial. "
PUBLIC_PHOTO_ORIGIN = "https://creative-assets.ddns.net"
PUBLIC_PHOTO_PATH = "/api/v1/realistic-review-ugc/stage5/public-photo/"
PUBLIC_PHOTO_ROUTE = re.compile(
    r"^/api/v1/realistic-review-ugc/stage5/public-photo/"
    r"([0-9a-fA-F-]{36})\.(png|jpg)$"
)


def loox_original_photo_url(job_id: str, mime_type: str | None) -> str:
    """Public, unguessable UUID URL backed by the original Stage 4 Drive file.

    Access to the image is revocable by hiding its Stage 5 concept. No Drive
    sharing permissions, temporary thumbnails, or signed query parameters.
    """
    try:
        token = str(UUID(str(job_id)))
    except (TypeError, ValueError, AttributeError):
        return ""
    extension = "jpg" if str(mime_type or "").lower() in {"image/jpeg", "image/jpg"} else "png"
    return f"{PUBLIC_PHOTO_ORIGIN}{PUBLIC_PHOTO_PATH}{token}.{extension}"


def stage5_sample_email(stage2_job_id: str, reviewer_name: str | None = None) -> str:
    """Generate a name-based synthetic email for internal Stage 5 sample data.

    The UUID-seeded numeric suffix resembles a randomized address but is
    deterministic on every export and uses the non-deliverable .invalid TLD.
    Never export it as a real person's email address.
    """
    try:
        stable_id = str(UUID(str(stage2_job_id)))
    except (TypeError, ValueError, AttributeError):
        return ""
    clean_name = clean_concept_copy(reviewer_name, author=True)
    first_name = clean_name.split(maxsplit=1)[0] if clean_name else ""
    normalized = unicodedata.normalize("NFKD", first_name)
    normalized = "".join(ch for ch in normalized if not unicodedata.combining(ch))
    safe_name = re.sub(r"[^a-z0-9]", "", normalized.casefold())[:24] or "review"
    digits = int.from_bytes(
        hashlib.sha256(stable_id.encode("ascii")).digest()[:8], "big"
    ) % 10_000_000_000
    return f"{safe_name}{digits:010d}@example.invalid"


def format_stage5_created_at(value: datetime | str | None) -> str:
    """Fixed-width YYYY-MM-DD HH:MM:SS, filling unavailable components with zeros.

    Keep known calendar components. Do not infer a fictional date for absent
    source timestamps; use zeros, and never turn missing time into local now.
    """
    if isinstance(value, datetime):
        return value.strftime("%Y-%m-%d %H:%M:%S")
    if value is None or not str(value).strip():
        return "0000-00-00 00:00:00"
    raw = str(value).strip()
    match = re.fullmatch(
        r"(\d{4})(?:-(\d{1,2}))?(?:-(\d{1,2}))?"
        r"(?:[T ](\d{1,2})(?::(\d{1,2}))?(?::(\d{1,2})(?:\.\d+)?)?"
        r"(?:Z|[+-]\d{2}:?\d{2})?)?", raw
    )
    if not match:
        return "0000-00-00 00:00:00"
    parts = [int(part) if part is not None else 0 for part in match.groups()]
    year, month, day, hour, minute, second = parts
    if not (0 <= month <= 12 and 0 <= day <= 31 and hour <= 23 and minute <= 59 and second <= 59):
        return "0000-00-00 00:00:00"
    return f"{year:04d}-{month:02d}-{day:02d} {hour:02d}:{minute:02d}:{second:02d}"


def stage5_sample_review_date(stage2_job_id: str, generated_at: datetime | None = None) -> str:
    """Stable fictitious date for internal synthetic-review fixture previews.

    This is not the DB created_at or evidence of a customer review. It should
    never replace real event timestamps or be used in a public review import.
    """
    try:
        stable_id = str(UUID(str(stage2_job_id)))
    except (TypeError, ValueError, AttributeError):
        return ""
    end = generated_at or datetime.now(timezone.utc)
    if end.tzinfo is None:
        end = end.replace(tzinfo=timezone.utc)
    end = end.astimezone(timezone.utc)
    seconds = int.from_bytes(hashlib.sha256((stable_id + ':fixture-date').encode()).digest()[:8], 'big') % (730 * 24 * 3600)
    date = end - timedelta(seconds=seconds)
    return format_stage5_created_at(date)


def clean_concept_copy(value: str | None, *, author: bool = False) -> str:
    """Remove historical UI/export labels from internal draft text only."""
    text = str(value or "").strip()
    if author:
        return re.sub(r"^(?:AI\s+concept\s*:\s*)+", "", text, flags=re.IGNORECASE).strip()
    while text.casefold().startswith(PREFIX.casefold()):
        text = text[len(PREFIX):].strip()
    return text


def shopify_handle_from_folder(folder: str) -> str:
    """Best-effort slug, NOT verified against the actual Shopify catalog."""
    text = unicodedata.normalize("NFKD", str(folder or ""))
    text = "".join(char for char in text if not unicodedata.combining(char))
    return re.sub(r"-+", "-", re.sub(r"[^a-z0-9]+", "-", text.casefold())).strip("-")


def loox_public_photo_url(candidate: str | None) -> str:
    """Do not export authenticated, signed, private, or non-image media links.

    The Loox custom-file importer requires a directly accessible public JPG
    or PNG URL ending in that extension, without a trailing query string.
    """
    if not candidate:
        return ""
    try:
        parts = urlsplit(candidate.strip())
        host = (parts.hostname or "").lower()
        if parts.scheme != "https" or not host or parts.username or parts.password or parts.port not in (None, 443):
            return ""
        if parts.query or parts.fragment or not parts.path.lower().endswith((".jpg", ".jpeg", ".png")):
            return ""
        if host == "creative-assets.ddns.net" and not PUBLIC_PHOTO_ROUTE.fullmatch(parts.path):
            return ""
        if host == "localhost" or host.endswith(
            (".localhost", ".local", ".internal", ".google.com", ".googleusercontent.com")
        ):
            return ""
        try:
            ip = ip_address(host)
            if not ip.is_global:
                return ""
        except ValueError:
            pass
        return candidate.strip()
    except ValueError:
        return ""


def build_loox_csv(rows: list[tuple]) -> bytes:
    """Loox Custom File accepts CSV UTF-8, not an XLSX upload."""
    if len(rows) > 100_000:
        raise ValueError("Loox custom import supports at most 100,000 rows")
    out = StringIO(newline="")
    writer = csv.writer(out, dialect="excel", lineterminator="\r\n")
    writer.writerow(HEADERS)
    for row in rows:
        if len(row) != len(HEADERS):
            raise ValueError("Invalid Loox export column count")
        # Optional verified_purchase must be blank for non-verified concepts.
        writer.writerow(tuple("" if item is None or item is False else item for item in row))
    return out.getvalue().encode("utf-8-sig")


def build_internal_concept_csv(rows: list[tuple]) -> bytes:
    """Internal concept dataset: clean body but row-level provenance is mandatory."""
    out = StringIO(newline="")
    writer = csv.writer(out, dialect="excel", lineterminator="\r\n")
    writer.writerow((*HEADERS, "review_origin"))
    for row in rows:
        if len(row) != len(HEADERS):
            raise ValueError("Invalid Stage 5 review column count")
        writer.writerow((*("" if cell is None or cell is False else cell for cell in row), "synthetic_ai"))
    return out.getvalue().encode("utf-8-sig")


def _cell(value: object, reference: str) -> str:
    if isinstance(value, bool):
        return f'<c r="{reference}" t="b"><v>{int(value)}</v></c>'
    if isinstance(value, int):
        return f'<c r="{reference}"><v>{value}</v></c>'
    text = str(value or "")
    # Neutralize formula injection if exported cells are edited in Excel.
    if text.startswith(("=", "+", "-", "@")):
        text = "'" + text
    text = "".join(c for c in text if ord(c) >= 32 or c in "\t\n\r")
    return f'<c r="{reference}" t="inlineStr"><is><t xml:space="preserve">{escape(text)}</t></is></c>'


def build_loox_xlsx(rows: list[tuple]) -> bytes:
    if len(rows) > 1_000_000:
        raise ValueError("Too many rows for one Excel worksheet")
    table = [HEADERS, *rows]
    sheet_rows = []
    for index, values in enumerate(table, 1):
        if len(values) != len(HEADERS):
            raise ValueError("Invalid Loox export column count")
        cells = "".join(_cell(value, f"{chr(65 + col)}{index}") for col, value in enumerate(values))
        sheet_rows.append(f'<row r="{index}">{cells}</row>')
    sheet = ('<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
             '<worksheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main">'
             '<sheetViews><sheetView workbookViewId="0"><pane ySplit="1" topLeftCell="A2" state="frozen"/></sheetView></sheetViews>'
             '<cols>' + ''.join(f'<col min="{i}" max="{i}" width="{w}" customWidth="1"/>'
               for i, w in enumerate((44, 10, 23, 18, 70, 22, 55, 20), 1)) +
             '</cols><sheetData>' + ''.join(sheet_rows) + '</sheetData></worksheet>')
    provenance = (
        "AI-GENERATED CONCEPTS ONLY — not real customer reviews or verified purchases.",
        "Review author names and copy are synthetic drafts, not customer testimonials.",
        "The email field contains name-based sample addresses @example.invalid, never real customer inboxes.",
        "XLSX created_at is a fictional sample date in the past 2 years, NOT the time of an actual customer review.",
        "CSV created_at remains the actual AI generation date, NOT a customer review date.",
        "verified_purchase remains blank until actual customer order proof exists.",
        "Only publish confirmed customer submissions with documented consent.",
        "Public photo links expose original AI-generated images while concepts are ready.",
    )
    about = ('<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
             '<worksheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main">'
             '<sheetData>' + ''.join(
                 f'<row r="{i}">{_cell(label, f"A{i}")}</row>'
                 for i, label in enumerate(provenance, 1)
             ) + '</sheetData></worksheet>')
    memory = BytesIO()
    with ZipFile(memory, "w", compression=ZIP_DEFLATED) as archive:
        archive.writestr("[Content_Types].xml",
            '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
            '<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">'
            '<Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/>'
            '<Default Extension="xml" ContentType="application/xml"/>'
            '<Override PartName="/xl/workbook.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet.main+xml"/>'
            '<Override PartName="/xl/worksheets/sheet1.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.worksheet+xml"/>'
            '<Override PartName="/xl/worksheets/sheet2.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.worksheet+xml"/>'
            '</Types>')
        archive.writestr("_rels/.rels",
            '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
            '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
            '<Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument" Target="xl/workbook.xml"/>'
            '</Relationships>')
        archive.writestr("xl/workbook.xml",
            '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
            '<workbook xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main"'
            ' xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships">'
            '<sheets><sheet name="in" sheetId="1" r:id="rId1"/>'
            '<sheet name="About AI concepts" sheetId="2" r:id="rId2"/></sheets></workbook>')
        archive.writestr("xl/_rels/workbook.xml.rels",
            '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
            '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
            '<Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/worksheet" Target="worksheets/sheet1.xml"/>'
            '<Relationship Id="rId2" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/worksheet" Target="worksheets/sheet2.xml"/>'
            '</Relationships>')
        archive.writestr("xl/worksheets/sheet1.xml", sheet)
        archive.writestr("xl/worksheets/sheet2.xml", about)
    return memory.getvalue()