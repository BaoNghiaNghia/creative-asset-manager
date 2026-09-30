from __future__ import annotations

import asyncio
import hashlib
import ipaddress
import json
import re
import socket
from dataclasses import dataclass, field, replace
from html import unescape
from html.parser import HTMLParser
from urllib.parse import urljoin, urlsplit, urlunsplit

import httpx


MAX_PRODUCT_PAGE_BYTES = 6 * 1024 * 1024
MAX_PRODUCT_IMAGE_BYTES = 20_000_000
MAX_PRODUCT_IMAGES = 20
MAX_PRODUCT_VARIANTS = 100
MAX_REDIRECTS = 4


class ProductPageImportError(RuntimeError):
    def __init__(self, code: str, message: str, *, retryable: bool = False):
        super().__init__(message)
        self.code = code
        self.retryable = retryable


@dataclass(frozen=True)
class ProductPageData:
    source_url: str
    source_host: str
    name: str
    sku: str | None = None
    brand: str | None = None
    description: str | None = None
    category: str | None = None
    color: str | None = None
    material: str | None = None
    price_text: str | None = None
    currency: str | None = None
    images: list[str] = field(default_factory=list)
    variants: list[dict] = field(default_factory=list)
    metadata: dict = field(default_factory=dict)


def _clean_text(value: object, *, limit: int = 4000) -> str | None:
    if value is None:
        return None
    text = re.sub(r"\s+", " ", unescape(str(value))).strip()
    return text[:limit] or None


def _clean_sku(value: object) -> str | None:
    text = _clean_text(value, limit=120)
    if not text:
        return None
    normalized = re.sub(r"[^A-Za-z0-9._-]+", "-", text).strip("-.").upper()
    return normalized[:120] or None


def generated_sku(source_url: str) -> str:
    host = (urlsplit(source_url).hostname or "URL").split(".")[0]
    prefix = re.sub(r"[^A-Za-z0-9]+", "-", host).strip("-").upper()[:24] or "URL"
    digest = hashlib.sha256(source_url.encode("utf-8")).hexdigest()[:12].upper()
    return f"{prefix}-{digest}"


def infer_product_type(*parts: str | None) -> str:
    text = " ".join(part or "" for part in parts).casefold()
    rules = (
        ("hat", ("baseball cap", "bucket hat", "beanie", "trucker hat", "dad hat", "cap", "hat", "headwear")),
        ("hoodie", ("hoodie", "hooded sweatshirt")),
        ("shirt", ("t-shirt", "tee", "shirt", "sweatshirt")),
        ("tote", ("tote", "canvas bag", "bag")),
        ("towel", ("towel",)),
        ("bodysuit", ("bodysuit", "romper", "onesie")),
    )
    for product_type, terms in rules:
        if any(term in text for term in terms):
            return product_type
    return "product"


def _is_public_address(value: str) -> bool:
    address = ipaddress.ip_address(value)
    return not (
        address.is_private
        or address.is_loopback
        or address.is_link_local
        or address.is_multicast
        or address.is_reserved
        or address.is_unspecified
    )


async def validate_public_https_url(url: str) -> str:
    value = (url or "").strip()
    if len(value) > 2048:
        raise ProductPageImportError(
            "product_url_rejected",
            "Product URL is too long.",
        )
    parsed = urlsplit(value)
    host = (parsed.hostname or "").lower().rstrip(".")
    if (
        parsed.scheme != "https"
        or not host
        or parsed.username
        or parsed.password
        or parsed.port not in (None, 443)
    ):
        raise ProductPageImportError(
            "product_url_rejected",
            "Product URL must be a public HTTPS page.",
        )
    loop = asyncio.get_running_loop()
    try:
        records = await loop.run_in_executor(
            None,
            socket.getaddrinfo,
            host,
            443,
            0,
            socket.SOCK_STREAM,
        )
    except OSError as exc:
        raise ProductPageImportError(
            "product_url_dns_failed",
            "Product host could not be resolved.",
            retryable=True,
        ) from exc
    if not records or any(not _is_public_address(record[4][0]) for record in records):
        raise ProductPageImportError(
            "product_url_rejected",
            "Product URL must resolve only to public addresses.",
        )
    clean_path = parsed.path or "/"
    return urlunsplit(("https", parsed.netloc, clean_path, parsed.query, ""))


class _ProductHtmlParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.meta: dict[str, str] = {}
        self.images: list[str] = []
        self.json_ld: list[str] = []
        self.title_parts: list[str] = []
        self.canonical: str | None = None
        self._capture_title = False
        self._capture_json_ld = False
        self._script_parts: list[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        values = {str(key).lower(): value or "" for key, value in attrs}
        tag = tag.lower()
        if tag == "meta":
            key = (values.get("property") or values.get("name") or values.get("itemprop") or "").lower()
            content = values.get("content", "").strip()
            if key and content and key not in self.meta:
                self.meta[key] = content
            return
        if tag == "link":
            rel = values.get("rel", "").lower()
            href = values.get("href", "").strip()
            if href and "canonical" in rel and not self.canonical:
                self.canonical = href
            return
        if tag == "img":
            for key in ("src", "data-src", "data-original", "data-lazy-src"):
                value = values.get(key, "").strip()
                if value:
                    self.images.append(value)
                    break
            return
        if tag == "title":
            self._capture_title = True
            return
        if tag == "script" and "application/ld+json" in values.get("type", "").lower():
            self._capture_json_ld = True
            self._script_parts = []

    def handle_endtag(self, tag: str) -> None:
        tag = tag.lower()
        if tag == "title":
            self._capture_title = False
        elif tag == "script" and self._capture_json_ld:
            value = "".join(self._script_parts).strip()
            if value:
                self.json_ld.append(value)
            self._capture_json_ld = False
            self._script_parts = []

    def handle_data(self, data: str) -> None:
        if self._capture_title:
            self.title_parts.append(data)
        if self._capture_json_ld:
            self._script_parts.append(data)


def _walk_products(value: object) -> list[dict]:
    found: list[dict] = []
    if isinstance(value, list):
        for item in value:
            found.extend(_walk_products(item))
        return found
    if not isinstance(value, dict):
        return found
    raw_type = value.get("@type")
    types = raw_type if isinstance(raw_type, list) else [raw_type]
    if any(str(item).casefold() == "product" for item in types if item):
        found.append(value)
    for key in ("@graph", "mainEntity", "itemListElement", "hasVariant"):
        child = value.get(key)
        if child is not None:
            found.extend(_walk_products(child))
    return found


def _image_values(value: object) -> list[str]:
    result: list[str] = []
    if isinstance(value, str):
        result.append(value)
    elif isinstance(value, list):
        for item in value:
            result.extend(_image_values(item))
    elif isinstance(value, dict):
        for key in ("url", "contentUrl"):
            if value.get(key):
                result.extend(_image_values(value[key]))
    return result


def _brand_name(value: object) -> str | None:
    if isinstance(value, dict):
        return _clean_text(value.get("name"), limit=200)
    return _clean_text(value, limit=200)


def _offer_fields(value: object) -> tuple[str | None, str | None]:
    offers = value
    if isinstance(offers, list):
        offers = next((item for item in offers if isinstance(item, dict)), None)
    if not isinstance(offers, dict):
        return None, None
    price = offers.get("price")
    if price is None and isinstance(offers.get("priceSpecification"), dict):
        price = offers["priceSpecification"].get("price")
    currency = offers.get("priceCurrency")
    if currency is None and isinstance(offers.get("priceSpecification"), dict):
        currency = offers["priceSpecification"].get("priceCurrency")
    return _clean_text(price, limit=120), _clean_text(currency, limit=16)


def _additional_properties(product: dict) -> dict[str, str]:
    output: dict[str, str] = {}
    values = product.get("additionalProperty")
    if not isinstance(values, list):
        values = [values] if isinstance(values, dict) else []
    for item in values:
        if not isinstance(item, dict):
            continue
        key = _clean_text(item.get("name") or item.get("propertyID"), limit=120)
        value = _clean_text(item.get("value"), limit=500)
        if key and value:
            output[key.casefold()] = value
    return output


def _variant_rows(product: dict) -> list[dict]:
    rows: list[dict] = []
    variants = product.get("hasVariant")
    if not isinstance(variants, list):
        variants = [variants] if isinstance(variants, dict) else []
    for item in variants[:MAX_PRODUCT_VARIANTS]:
        if not isinstance(item, dict):
            continue
        price, currency = _offer_fields(item.get("offers"))
        row = {
            "sku": _clean_sku(item.get("sku")),
            "name": _clean_text(item.get("name"), limit=300),
            "color": _clean_text(item.get("color"), limit=120),
            "size": _clean_text(item.get("size"), limit=120),
            "price": price,
            "currency": currency,
        }
        if any(value for value in row.values()):
            rows.append(row)
    return rows


def parse_product_html(html: str, page_url: str) -> ProductPageData:
    parser = _ProductHtmlParser()
    parser.feed(html)

    products: list[dict] = []
    for raw in parser.json_ld:
        try:
            payload = json.loads(raw)
        except (TypeError, ValueError, json.JSONDecodeError):
            continue
        products.extend(_walk_products(payload))

    product = products[0] if products else {}
    props = _additional_properties(product)

    structured_name = _clean_text(product.get("name"), limit=200)
    social_name = (
        _clean_text(parser.meta.get("og:title"), limit=200)
        or _clean_text(parser.meta.get("twitter:title"), limit=200)
    )
    document_title = _clean_text(" ".join(parser.title_parts), limit=200)
    social_product_signal = bool(
        parser.meta.get("og:image")
        or parser.meta.get("twitter:image")
        or parser.meta.get("product:price:amount")
    )
    name = structured_name or social_name
    if not name and document_title and social_product_signal:
        name = document_title
    if not name:
        raise ProductPageImportError(
            "product_detail_missing",
            "The page was fetched, but it did not expose recognizable product metadata.",
        )

    description = (
        _clean_text(product.get("description"))
        or _clean_text(parser.meta.get("og:description"))
        or _clean_text(parser.meta.get("description"))
    )
    price, currency = _offer_fields(product.get("offers"))
    price = price or _clean_text(
        parser.meta.get("product:price:amount") or parser.meta.get("og:price:amount"),
        limit=120,
    )
    currency = currency or _clean_text(
        parser.meta.get("product:price:currency") or parser.meta.get("og:price:currency"),
        limit=16,
    )

    image_candidates = [
        *_image_values(product.get("image")),
        parser.meta.get("og:image", ""),
        parser.meta.get("twitter:image", ""),
        *parser.images,
    ]
    images: list[str] = []
    seen: set[str] = set()
    for raw in image_candidates:
        if not raw:
            continue
        absolute = urljoin(page_url, str(raw).strip())
        parsed = urlsplit(absolute)
        if parsed.scheme not in {"https"} or not parsed.hostname:
            continue
        clean = urlunsplit((parsed.scheme, parsed.netloc, parsed.path, parsed.query, ""))
        if clean in seen:
            continue
        seen.add(clean)
        images.append(clean)
        if len(images) >= MAX_PRODUCT_IMAGES:
            break

    source_url = page_url
    if parser.canonical:
        candidate = urljoin(page_url, parser.canonical)
        candidate_parts = urlsplit(candidate)
        page_parts = urlsplit(page_url)
        candidate_host = (candidate_parts.hostname or "").lower().removeprefix("www.")
        page_host = (page_parts.hostname or "").lower().removeprefix("www.")
        if candidate_parts.scheme == "https" and candidate_host == page_host:
            source_url = candidate
    parsed_source = urlsplit(source_url)
    source_url = urlunsplit(
        (
            parsed_source.scheme,
            parsed_source.netloc,
            parsed_source.path or "/",
            parsed_source.query,
            "",
        )
    )

    category = _clean_text(product.get("category"), limit=200)
    color = _clean_text(product.get("color"), limit=120) or props.get("color")
    material = _clean_text(product.get("material"), limit=200) or props.get("material")

    return ProductPageData(
        source_url=source_url,
        source_host=(urlsplit(source_url).hostname or "").lower(),
        name=name,
        sku=_clean_sku(product.get("sku") or product.get("mpn") or parser.meta.get("product:retailer_item_id")),
        brand=_brand_name(product.get("brand")) or _clean_text(parser.meta.get("product:brand"), limit=200),
        description=description,
        category=category,
        color=color,
        material=material,
        price_text=price,
        currency=currency,
        images=images,
        variants=_variant_rows(product),
        metadata={
            "json_ld_product": bool(product),
            "gtin": _clean_text(
                product.get("gtin13")
                or product.get("gtin12")
                or product.get("gtin14")
                or product.get("gtin")
            ),
            "mpn": _clean_text(product.get("mpn"), limit=120),
        },
    )


async def _read_bounded(response: httpx.Response, max_bytes: int) -> bytes:
    declared_length = response.headers.get("content-length")
    if declared_length:
        try:
            if int(declared_length) > max_bytes:
                raise ProductPageImportError("product_source_too_large", "Remote content exceeds the size limit.")
        except ValueError:
            pass
    chunks: list[bytes] = []
    size = 0
    async for chunk in response.aiter_bytes():
        size += len(chunk)
        if size > max_bytes:
            raise ProductPageImportError("product_source_too_large", "Remote content exceeds the size limit.")
        chunks.append(chunk)
    return b"".join(chunks)


async def fetch_product_page(client: httpx.AsyncClient, raw_url: str) -> ProductPageData:
    current = await validate_public_https_url(raw_url)
    for redirect_count in range(MAX_REDIRECTS + 1):
        await validate_public_https_url(current)
        try:
            async with client.stream("GET", current, follow_redirects=False) as response:
                if response.status_code in {301, 302, 303, 307, 308}:
                    location = response.headers.get("location")
                    if redirect_count >= MAX_REDIRECTS or not location:
                        raise ProductPageImportError(
                            "product_source_redirect_rejected",
                            "Product page redirected too many times.",
                        )
                    current = urljoin(current, location)
                    continue
                if response.status_code == 429 or response.status_code >= 500:
                    raise ProductPageImportError(
                        "product_source_unavailable",
                        "Product page is temporarily unavailable.",
                        retryable=True,
                    )
                if response.is_error:
                    raise ProductPageImportError(
                        "product_source_fetch_failed",
                        f"Product page returned HTTP {response.status_code}.",
                    )
                content_type = response.headers.get("content-type", "").split(";", 1)[0].strip().lower()
                if content_type and content_type not in {"text/html", "application/xhtml+xml"}:
                    raise ProductPageImportError(
                        "product_source_not_html",
                        "Product URL did not return an HTML page.",
                    )
                data = await _read_bounded(response, MAX_PRODUCT_PAGE_BYTES)
                charset = response.encoding or "utf-8"
                html = data.decode(charset, errors="replace")
                parsed = parse_product_html(html, str(response.url))
                if parsed.images:
                    checked = await asyncio.gather(
                        *(validate_public_https_url(url) for url in parsed.images),
                        return_exceptions=True,
                    )
                    safe_images = [
                        value for value in checked if isinstance(value, str)
                    ]
                    parsed = replace(parsed, images=safe_images)
                return parsed
        except httpx.HTTPError as exc:
            raise ProductPageImportError(
                "product_source_unavailable",
                "Product page could not be reached.",
                retryable=True,
            ) from exc
    raise ProductPageImportError(
        "product_source_redirect_rejected",
        "Product page redirected too many times.",
    )


async def fetch_product_image(client: httpx.AsyncClient, raw_url: str) -> tuple[bytes, str]:
    current = await validate_public_https_url(raw_url)
    for redirect_count in range(MAX_REDIRECTS + 1):
        await validate_public_https_url(current)
        try:
            async with client.stream("GET", current, follow_redirects=False) as response:
                if response.status_code in {301, 302, 303, 307, 308}:
                    location = response.headers.get("location")
                    if redirect_count >= MAX_REDIRECTS or not location:
                        raise ProductPageImportError(
                            "product_image_redirect_rejected",
                            "Product image redirected too many times.",
                        )
                    current = urljoin(current, location)
                    continue
                if response.status_code == 429 or response.status_code >= 500:
                    raise ProductPageImportError(
                        "product_image_unavailable",
                        "Product image is temporarily unavailable.",
                        retryable=True,
                    )
                if response.is_error:
                    raise ProductPageImportError(
                        "product_image_fetch_failed",
                        f"Product image returned HTTP {response.status_code}.",
                    )
                content_type = response.headers.get("content-type", "").split(";", 1)[0].strip().lower()
                if not content_type.startswith("image/"):
                    raise ProductPageImportError(
                        "product_image_invalid",
                        "Product image URL did not return an image.",
                    )
                return await _read_bounded(response, MAX_PRODUCT_IMAGE_BYTES), content_type
        except httpx.HTTPError as exc:
            raise ProductPageImportError(
                "product_image_unavailable",
                "Product image could not be reached.",
                retryable=True,
            ) from exc
    raise ProductPageImportError(
        "product_image_redirect_rejected",
        "Product image redirected too many times.",
    )
