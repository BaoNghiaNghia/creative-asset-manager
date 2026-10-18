"""Constrained source extraction and grounded *lexical* retrieval.

This is RAG stage 1, not semantic vector retrieval or brand inference. Uploaded
documents are untrusted instructions and never override product facts.
"""
from __future__ import annotations

import csv
import hashlib
import io
import json
import re
import zipfile
from xml.etree import ElementTree as ET

from fastapi import HTTPException

MAX_BYTES = 2 * 1024 * 1024
MAX_CHARS = 80_000
MAX_CHUNKS = 160
SUPPORTED = {"txt", "md", "csv", "json", "docx", "pdf"}
CATEGORIES = frozenset({
    "brand_guidelines", "brand_voice", "audience", "product_specs",
    "creative_guidelines", "campaign", "faq", "policy", "other",
})
WORD = re.compile(r"[^\W_]{2,}", re.UNICODE)


def digest(raw: bytes) -> str:
    return hashlib.sha256(raw).hexdigest()


def extract_text(raw: bytes, extension: str) -> str:
    if not raw or len(raw) > MAX_BYTES:
        raise HTTPException(413, detail="knowledge_file_size_invalid")
    if extension not in SUPPORTED:
        raise HTTPException(415, detail="knowledge_format_unsupported")
    try:
        if extension in {"txt", "md", "csv", "json"}:
            if b"\x00" in raw:
                raise ValueError("Binary content")
            decoded = raw.decode("utf-8-sig", errors="strict")
            if extension == "json":
                # Require valid JSON, but preserve exact text for provenance.
                json.loads(decoded)
            if extension == "csv":
                if "\x00" in decoded:
                    raise ValueError("Binary CSV")
                reader = csv.reader(io.StringIO(decoded))
                for i, row in enumerate(reader):
                    if i > 3500 or len(row) > 120:
                        raise ValueError("CSV excessive rows/columns")
            result = decoded
        elif extension == "docx":
            # OOXML only; never execute macros or load external relationships.
            with zipfile.ZipFile(io.BytesIO(raw)) as archive:
                entries = archive.infolist()
                if len(entries) > 250 or sum(x.file_size for x in entries) > 8_000_000:
                    raise ValueError("DOCX decompression limit")
                item = archive.getinfo("word/document.xml")
                if item.file_size > 4_000_000:
                    raise ValueError("DOCX XML too large")
                root = ET.fromstring(archive.read(item))
                ns = "{http://schemas.openxmlformats.org/wordprocessingml/2006/main}"
                paras = []
                for p in root.iter(ns + "p"):
                    line = " ".join(node.text or "" for node in p.iter(ns + "t"))
                    if line.strip():
                        paras.append(line)
                result = "\n".join(paras)
        else:
            from pypdf import PdfReader
            if not raw.startswith(b"%PDF-"):
                raise ValueError("Invalid PDF signature")
            reader = PdfReader(io.BytesIO(raw), strict=True)
            if reader.is_encrypted or len(reader.pages) > 35:
                raise ValueError("Encrypted or too many PDF pages")
            blocks = []
            for page in reader.pages:
                block = page.extract_text() or ""
                blocks.append(block)
                if sum(map(len, blocks)) > MAX_CHARS:
                    raise ValueError("PDF text too long")
            result = "\n".join(blocks)
    except (UnicodeError, ValueError, KeyError, zipfile.BadZipFile, ET.ParseError,
            json.JSONDecodeError, csv.Error, ImportError) as exc:
        raise HTTPException(422, detail="knowledge_text_extraction_failed") from exc
    except Exception as exc:
        # PDF parser errors are never exposed to users or logs.
        raise HTTPException(422, detail="knowledge_text_extraction_failed") from exc
    normalized = re.sub(r"[\t ]+", " ", result).replace("\r\n", "\n")
    normalized = re.sub(r"\n{3,}", "\n\n", normalized).strip()
    if len(normalized) < 20 or len(normalized) > MAX_CHARS:
        raise HTTPException(422, detail="knowledge_extract_empty_or_exceeds_limit")
    return normalized


def chunks(text: str) -> list[str]:
    """Stable overlapping chunks; retains exact citations and content hashes."""
    output: list[str] = []
    pos = 0
    while pos < len(text):
        end = min(pos + 850, len(text))
        if end < len(text):
            cut = text.rfind(" ", max(pos + 500, end - 130), end)
            if cut > pos:
                end = cut
        piece = text[pos:end].strip()
        if piece:
            output.append(piece)
        if end >= len(text):
            break
        pos = max(pos + 1, end - 100)
        if len(output) >= MAX_CHUNKS:
            raise HTTPException(422, detail="knowledge_too_many_chunks")
    return output


def keywords(query: str) -> list[str]:
    return list(dict.fromkeys(t.casefold() for t in WORD.findall(query)))[:12]


def lexical_score(text: str, query: str) -> int:
    terms = keywords(query)
    normalized = text.casefold()
    return sum(min(normalized.count(term), 4) for term in terms)


def citation_id(document_id: str, ordinal: int) -> str:
    return f"rag:{document_id}:{ordinal}"
