# ADR-006 — Asynchronous Processing

Status: Accepted

Download, hashing, managed storage, AI analysis, and indexing execute in durable workers rather than ingestion HTTP requests. Processing is idempotent and retryable.
