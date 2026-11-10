"""Low-cost explainable Etsy design-topic matching for Later.

No paid model calls, no scraping, no external API requests and no async workers.
Conservative matching: apparel/brand/SEO words never create relationships.
"""
from __future__ import annotations

import html
import re
import unicodedata
from dataclasses import dataclass

STOPWORDS = frozenset("""
a an the and or of for with from on in at to by my your our their his her
you yours i im me we us they it is its this that these those are was be have has
got get too so can couldnt should would i m s t shirt shirts tshirt tshirts
tee tees sweater sweaters sweatshirt sweatshirts hooded hoodie hoodies
top tops tank tanks graphic graphics design designs print printed printing
vintage retro distressed trendy aesthetic outfit apparel clothing clothes
comfort colors gildan hanes bella canvas unisex oversized loose fit
size sizes cotton soft cozy cute funny fun humor joke memes meme
birthday birthdays gift gifts gifting idea ideas present forhim forher
men man women woman adult youth toddler kids kid boy girl baby babies
mommy mama mother daddy father everyone family matching matchingfamily
party parties perfect best favorite popular handmade custom personalized
christmas xmas holiday holidays halloween valentines valentine
black white pink blue red orange yellow beige gray grey green purple
""".split())

# Broad season/occasion terms should not match on their own.
# Specific design themes *can*: plant/botanical, goose/geese, reading/books, etc.
THEMES: dict[str, tuple[str, ...]] = {
    "plant": ("plant", "plants", "houseplant", "houseplants", "botanical",
              "botanicals", "gardening", "gardener", "garden", "gardens",
              "succulent", "succulents", "monstera", "pothos", "cactus", "cacti"),
    "goose": ("goose", "geese", "gosling", "goslings"),
    "frog": ("frog", "frogs", "toad", "toads", "froggy"),
    "duck": ("duck", "ducks", "duckling", "ducklings"),
    "cat": ("cat", "cats", "kitty", "kitties", "kitten", "kittens", "feline"),
    "dog": ("dog", "dogs", "puppy", "puppies", "canine", "doggo"),
    "cow": ("cow", "cows", "cowgirl", "heifer", "highland", "cattle"),
    "owl": ("owl", "owls"),
    "tiger": ("tiger", "tigers"),
    "raccoon": ("raccoon", "raccoons", "racoons", "raccoons"),
    "reindeer": ("reindeer", "rudolph", "rudolf", "rednose"),
    "hockey": ("hockey", "puck", "pucks", "icehockey"),
    "reading": ("reading", "reader", "readers", "books", "book", "bookish",
                "bibliophile", "bookworm", "bookworms", "bookshelf"),
    "coffee": ("coffee", "latte", "espresso", "caffeine", "cappuccino"),
    "teacher": ("teacher", "teachers", "educator", "educators", "teaching"),
    "nurse": ("nurse", "nurses", "nursing"),
    "camping": ("camping", "camper", "campers", "campfire", "tent"),
    "fishing": ("fishing", "fisherman", "fishermen", "angler", "anglers"),
    "pickleball": ("pickleball",),
    "hiking": ("hiking", "hiker", "hikers", "hike", "mountaineer"),
    "witch": ("witch", "witches", "witchy"),
    "ghost": ("ghost", "ghosts", "ghostie"),
    "boop": ("boop", "booping", "booped"),
    "overthinking": ("overthinking", "overthink", "overthinker", "overthinkers",
                     "anxious", "anxiety", "worry", "worrying"),
}
THEME_LABELS = {
    "plant": "plant / botanical", "goose": "goose / geese",
    "frog": "frog / toad", "reindeer": "Rudolph / reindeer",
    "reading": "book / reading", "overthinking": "overthinking / anxiety",
}
ALIASES = {word: theme for theme, words in THEMES.items() for word in words}
# Common thematic terms like 'family' or 'teacher' alone may be misleading.
# Broad animal categories are not designs by themselves. A cat/dog listing
# needs additional shared design evidence; never route it to Later solely
# because an operator previously selected an unrelated cat/dog illustration.
BROAD_THEMES_REQUIRING_DETAIL = frozenset({"cat", "dog"})

SINGLE_THEME_ALLOWED = frozenset({
    "plant", "goose", "frog", "duck", "cat", "dog", "cow", "owl", "tiger",
    "raccoon", "reindeer", "hockey", "reading", "coffee", "pickleball",
    "camping", "fishing", "witch", "ghost", "boop", "overthinking",
})


def _words(text: str) -> tuple[str, ...]:
    clean = unicodedata.normalize("NFKD", html.unescape(text or ""))
    clean = "".join(char for char in clean if not unicodedata.combining(char))
    return tuple(re.findall(r"[a-z0-9]+", clean.lower()[:1800]))


@dataclass(frozen=True)
class Topic:
    terms: frozenset[str]
    themes: frozenset[str]


def analyze_title(title: str) -> Topic:
    words = _words(title)
    themes = {ALIASES[word] for word in words if word in ALIASES}
    # Intent-oriented phrase equivalence for the design, not product/brand type.
    if "critical" in words and "thinking" in words:
        themes.add("overthinking")
    terms = {ALIASES.get(word, word) for word in words
             if word not in STOPWORDS and len(word) >= 3 and
             not (word.isdigit() and len(word) > 4)}
    return Topic(frozenset(terms), frozenset(themes))


@dataclass(frozen=True)
class Match:
    score: int
    reason: str


def match_title(seed_title: str, candidate_title: str) -> Match | None:
    """Detect one clearly shared specific theme, or >=2 design-defining tokens.

    A single high-level occasion, product name, material, brand, apparel or
    merchant keyword is insufficient. No fuzzy edit distance / false closeness.
    """
    seed = analyze_title(seed_title)
    candidate = analyze_title(candidate_title)
    if not seed.terms or not candidate.terms:
        return None

    high_signal = sorted((seed.themes & candidate.themes & SINGLE_THEME_ALLOWED)
                         - BROAD_THEMES_REQUIRING_DETAIL)
    if high_signal:
        theme = high_signal[0]
        return Match(88, "Cùng chủ đề: " + THEME_LABELS.get(theme, theme))

    shared = seed.terms & candidate.terms
    # Distinctive phrase words, not trivial product type or marketing fragments.
    if len(shared) < 2:
        return None
    coverage = min(len(shared) / len(seed.terms),
                   len(shared) / len(candidate.terms))
    if len(shared) < 3 and coverage < 0.40:
        return None
    if len(shared) >= 3 and coverage < 0.28:
        return None
    score = min(94, int(68 + 24 * coverage + min(4, len(shared) - 2) * 2))
    return Match(score, "Keyword thiết kế: " + ", ".join(sorted(shared)[:4]))
