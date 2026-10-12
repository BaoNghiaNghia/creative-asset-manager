"""Minimal, standards-compliant XLSX of Stage 5 synthetic review concepts.

Fixed eight-column Loox template shape. No macros, formulas, customer emails,
or claims of verified purchases. Do not represent synthetic content as reviews
from actual customers.
"""
from __future__ import annotations

import csv
import re
import unicodedata
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
        if host in {"localhost", "creative-assets.ddns.net"} or host.endswith(
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
    memory = BytesIO()
    with ZipFile(memory, "w", compression=ZIP_DEFLATED) as archive:
        archive.writestr("[Content_Types].xml",
            '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
            '<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">'
            '<Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/>'
            '<Default Extension="xml" ContentType="application/xml"/>'
            '<Override PartName="/xl/workbook.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet.main+xml"/>'
            '<Override PartName="/xl/worksheets/sheet1.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.worksheet+xml"/>'
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
            '<sheets><sheet name="in" sheetId="1" r:id="rId1"/></sheets></workbook>')
        archive.writestr("xl/_rels/workbook.xml.rels",
            '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
            '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
            '<Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/worksheet" Target="worksheets/sheet1.xml"/>'
            '</Relationships>')
        archive.writestr("xl/worksheets/sheet1.xml", sheet)
    return memory.getvalue()
