"""Future-safe account provisioning input helpers.

The supported CAM runtime never accepts passwords or TOTP secrets through argv.
A later operational task may call read_provisioning_payload() with protected stdin.
"""
from __future__ import annotations

import json
from typing import TextIO


def read_provisioning_payload(stream: TextIO) -> dict[str, str]:
    payload = json.load(stream)
    if not isinstance(payload, dict):
        raise ValueError("Provisioning input must be a JSON object.")
    required = ("account", "email", "password", "totp")
    if any(not str(payload.get(field, "")).strip() for field in required):
        raise ValueError("Provisioning input must include account, email, password, and totp.")
    return {field: str(payload[field]) for field in required}
