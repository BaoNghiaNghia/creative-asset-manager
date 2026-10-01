from __future__ import annotations


class InvalidPipelineContent(ValueError):
    """Permanent source/content problem that retrying will not fix."""


class TransientPipelineContent(RuntimeError):
    """Temporary source/provider problem that may succeed on retry."""
