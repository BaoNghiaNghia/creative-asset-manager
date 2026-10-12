"""Conservative, offline quote extraction from *unverified* public listing titles.

This extracts only an obvious text/saying from an item explicitly described as
a hat. It does not read product pages, call LLMs, verify embroidery, run paid
AEBrowse, or claim intellectual-property clearance. Ambiguous listings stay in
the Research Inbox for a human to review.
"""
from __future__ import annotations

import re

MAX_QUOTE_LENGTH = 76
VERSION = "agent-reach-quote-extractor-v1"

_CAP = re.compile(r"\b(?:hats?|caps?|trucker|snapback|baseball|dad\s+hat|rope\s+hat)\b", re.I)
_PRODUCT = re.compile(
    r"\s+(?:(?:vintage|washed|retro|funny|sarcastic|christian|faith|motivational|"
    r"embroidered|embroidery|adjustable|classic|minimal|minimalist|custom|cotton|"
    r"unisex)\s+){0,3}"
    r"(?:trucker\s+hat|dad\s+hat|rope\s+(?:cap|hat)|baseball\s+(?:caps?|hats?)|"
    r"snapback(?:\s+(?:cap|hat))?|mesh\s+(?:cap|hat)|(?:cap|hat)s?)\b",
    re.I,
)
_TERMINAL = re.compile(
    r"\b(?:vintage|retro|washed|embroidered|embroidery|adjustable|unisex|"
    r"sarcastic|funny|quote|slogan|saying|custom|personalized|minimalist|"
    r"clothing|apparel|patch|transfer|logo|gift|gifts|size|cotton)\b", re.I,
)
_GENERIC = re.compile(
    r"\b(?:embroider\w*|hats?|caps?|trucker|baseball|snapback|vintage|"
    r"custom|personaliz\w*|logo|patch|quote|quotes|saying|funny|"
    r"shirt|shirts|clothing|shoes|jewelry|adjustable|dad|"
    r"toddler|children|kids|gift|gifts|unisex|rope|mesh|foam|symbol|"
    r"pictures|teenagers|girl)\b", re.I,
)
_BLOCKED = re.compile(
    r"\b(?:balenciaga|dsquared\w*|kermit|yellowstone|amazon|etsy|"
    r"allntrends|d(y)?bickt|mirmaru|raylarnia|nike|adidas|"
    r"disney|marvel|pokemon|nfl|mlb|nba)\b",
    re.I,
)
_QUOTED = re.compile(r'["“”\x27‘’]([^"“”\x27‘’]{6,82})["“”\x27‘’]')
_PREFIX = re.compile(r"^(?:amazon\.com\s*:\s*|etsy\s*:\s*)", re.I)
_PUNCT = re.compile(r"[^\w\s'&-]", re.UNICODE)


def _valid(phrase: str) -> str | None:
    phrase = " ".join(_PUNCT.sub(" ", phrase.replace("’", "'")).split()).strip(" -'")
    words = phrase.split()
    if not (2 <= len(words) <= 12 and 7 <= len(phrase) <= MAX_QUOTE_LENGTH):
        return None
    if _BLOCKED.search(phrase) or _GENERIC.search(phrase):
        return None
    # Numeric merchandising, ID references, empty noise and product copy are
    # not quotes. These should remain review-only.
    if any(sum(ch.isdigit() for ch in word) > 1 for word in words):
        return None
    if all(word.upper() in {"THE", "AND", "FOR", "WITH", "IN", "TO", "MY", "YOUR"} for word in words):
        return None
    return phrase.upper()


def extract_listing_quote(title: str, source: str, channel: str) -> str | None:
    """Return a high-confidence hat phrase or None; never hallucinate text."""
    if channel != "auto-discover" or source not in {"etsy", "amazon", "pinterest"}:
        return None
    title = " ".join(title.split())[:240]
    if not title or not _CAP.search(title):
        return None
    title = _PREFIX.sub("", title)
    # Intact quoted multi-word lettering is the clearest available evidence.
    for match in re.finditer(r'["“]([^"”]{7,80})["”]', title):
        exact = _valid(match.group(1))
        if exact:
            return exact
    # Search snippets with truncation may still start with a complete phrase
    # before the first product noun. Never infer words after ellipses.
    title = re.split(r"\s*(?:\.{3}|…|\s+at\s+Amazon\b|\s+[-–]\s+Etsy\b)", title, maxsplit=1, flags=re.I)[0]
    # Pipe-separated search-snippet content can describe unrelated photos.
    title = title.split("|", 1)[0]
    fragments = [part.strip() for part in re.split(r"\s*:\s*", title) if part.strip()]
    # Prefer a clause whose beginning is the quote and whose suffix is a hat.
    # Later clauses (e.g. 'Faith Made Me Do It ...') can carry the actual quote.
    for fragment in fragments:
        if not _CAP.search(fragment):
            continue
        leading = _PRODUCT.search(fragment)
        if not leading:
            continue
        candidate = fragment[:leading.start()].strip(" -–,")
        # Remove common trailing merchandising adjectives without trimming
        # words from the middle of a saying.
        candidate = re.sub(r"\s+(?:vintage|washed|embroidered|retro|funny|"
                           r"sarcastic|christian|faith|motivational|minimalist)\s*$",
                           "", candidate, flags=re.I)
        exact = _valid(candidate)
        if exact:
            return exact
    return None
