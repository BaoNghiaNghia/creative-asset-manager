# Step 14 — Search query parser

The parser emits a small provider-neutral query model for terms, phrases,
qualified clauses, soft AND, strict AND, and explicit OR. It uses the same NFKC,
case, punctuation, and whitespace normalization as the projection pipeline.

Qualified fields are resolved only through explicit stable aliases, configured
facet names, or configured logical paths. Unknown or malformed syntax falls
back to safe plain-text fields and can never create a dynamic Elasticsearch
field name.

The query builder always applies `tenant_id` as a filter. Boosts descend from
exact number, exact phrase, exact term, configured path, normalized term,
filename, search text, to folder path.

No route uses the parser while `SEARCH_QUERY_PARSER_V2_ENABLED=false`.
