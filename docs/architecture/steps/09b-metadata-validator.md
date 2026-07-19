# Step 09B — Metadata safety validator

The persistence boundary accepts JSON text, UTF-8 bytes, or a mapping and
returns a structured validation result. It requires an object root and enforces
configurable byte size, nesting depth, node count, array length, and string
length limits. Non-finite numbers and non-JSON values are rejected.

Optional profile JSON Schema validation uses the schema declared draft. Errors
contain a stable code, message, document path, limit, and actual value where
applicable. Accepted input is deep-copied and never aliases the caller object.
Recursion-heavy payloads are safely rejected.

The validator has no feature flag because it is a mandatory safety boundary
whenever dynamic metadata persistence is used.
