"""Deterministic, mutually exclusive Etsy listing topic groups.

Groups are assigned from observed listing titles and search keywords, never
generated or guessed from a per-page subset. Lowest rank wins across repeated
Search/Shop/keyword observations of the same Etsy Listing ID.
"""
from __future__ import annotations

import re

from sqlalchemy import case, func, literal, or_

# Higher-signal design themes take precedence over generic product wording.
# Group codes are stable API identifiers; the UI may translate labels.
CATEGORY_GROUPS: tuple[tuple[str, str, tuple[str, ...]], ...] = (
    ("christmas", "Christmas", (
        "christmas", "xmas", "santa", "rudolph", "reindeer", "grinch",
        "north pole", "merry and bright", "elf on the shelf", "sleigh")),
    ("thanksgiving", "Thanksgiving", (
        "thanksgiving", "turkey day", "gobble gobble", "friendsgiving")),
    ("halloween", "Halloween", (
        "halloween", "spooky", "pumpkin", "trick or treat", "witchy",
        "haunted", "ghost", "hocus pocus")),
    ("sports", "Sports", (
        "football", "baseball", "basketball", "soccer", "hockey", "rugby",
        "volleyball", "buffalo bills", "bulldogs")),
    ("books", "Books", (
        "bookworm", "bookish", "books", "reading", "reader", "librarian",
        "book lover", "book club", "library")),
    ("gaming", "Gaming", (
        "video game", "gaming", "gamer", "game day", "level up",
        "dungeons and dragons", "dnd", "rpg", "xbox")),
    ("movies", "Movies & TV", (
        "movie", "film", "cinema", "tv show", "snape", "harry potter",
        "hobbit", "lord of the rings", "bobs burgers", "bob s burgers",
        "aragorn", "basgiath")),
    ("music", "Music", (
        "music", "concert", "singer", "rock band", "stevie nicks",
        "tour shirt", "guitar", "song lyric")),
    ("animals", "Animals", (
        "frog", "silly goose", "goose", "cat", "dog", "puppy", "kitten",
        "pet", "bird", "cow", "horse", "duck", "chicken", "otter")),
    ("crafts", "Crafts", (
        "sewing", "crochet", "knitting", "quilt", "quilter",
        "embroidery", "embroider", "crafting")),
    ("faith", "Faith", (
        "christian", "jesus", "bible", "scripture", "church", "prayer",
        "holy spirit", "faith over fear")),
    ("family", "Family", (
        "mama", "mom", "mother", "dad", "father", "grandma", "grandpa",
        "nana", "papa", "family reunion")),
    ("motivational", "Motivation", (
        "you can do hard things", "keep going", "be brave", "stay strong",
        "you are enough", "be kind", "choose joy")),
)
OTHER_GROUP_KEY = "other"
GROUP_LABELS = {code: label for code, label, _ in CATEGORY_GROUPS} | {
    OTHER_GROUP_KEY: "Khác",
}
GROUP_RANKS = {code: index for index, (code, _, _) in enumerate(CATEGORY_GROUPS)}


def _words(value):
    # Word boundaries also prevent "cat" matching "educate".
    for punctuation in ("-", "_", ".", "/", "&", ",", "'", "’", ":", "!", "?", "(", ")", "#"):
        value = func.replace(value, punctuation, " ")
    for _ in range(2):
        value = func.replace(value, "  ", " ")
    return literal(" ") + func.trim(value) + literal(" ")


def category_phrase_condition(listing, group_code: str):
    """Match the same word-bounded phrases used by the topic badge.

    This intentionally ignores other groups' precedence: if the title or
    search keyword contains a requested season, it must route to Later even
    when another group appears earlier in the daily category CASE.
    """
    phrases = next(
        (phrases for code, _label, phrases in CATEGORY_GROUPS
         if code == group_code), ()
    )
    if not phrases:
        return literal(False)
    # A compact word-boundary regex reuses the existing group vocabulary but
    # avoids embedding the sixteen nested punctuation REPLACE operations once
    # per phrase inside every distinct/day-group query. SQLAlchemy compiles
    # regexp_match for PostgreSQL (~) and SQLite (REGEXP via re.search).
    phrases_regex = "|".join(
        re.escape(phrase).replace(r"\ ", "[^a-z0-9]+") for phrase in phrases
    )
    pattern = r"(^|[^a-z0-9])(" + phrases_regex + r")([^a-z0-9]|$)"
    source = func.lower(func.coalesce(listing.title, "") + literal(" ") +
                        func.coalesce(listing.keyword, ""))
    return source.regexp_match(pattern)


def category_rank_expression(listing):
    """Portable SQLite/PostgreSQL CASE; one stable precedence rank per row."""
    source = _words(func.lower(func.coalesce(listing.title, "") + literal(" ") +
                               func.coalesce(listing.keyword, "")))
    rules = []
    for index, (_, _, phrases) in enumerate(CATEGORY_GROUPS):
        phrases_sql = [source.like("% " + phrase + " %") for phrase in phrases]
        rules.append((or_(*phrases_sql), index))
    return case(*rules, else_=len(CATEGORY_GROUPS))
