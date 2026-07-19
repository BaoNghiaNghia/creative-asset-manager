# Step 11 — Metadata normalizer

MetadataNormalizer applies NFKC Unicode normalization, Unicode case folding,
punctuation-to-space conversion, trimming, and whitespace collapse.

It retains meaningful short terms such as est, mom, mama, and dad because it
does not apply a generic stop-word list. Integer-like tokens, including years,
are collected separately. A normalized multi-token scalar is retained as a
phrase when it fits configured limits.

Tokens are deduplicated in first-seen order within one value. Batch output is
sorted deterministically and bounded by value, term, and phrase limits. Input
metadata and extracted values are never modified.

This step adds no migration or runtime integration.
