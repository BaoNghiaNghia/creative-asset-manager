"""Safe provisioning metadata parser; provider credentials are never accepted."""
from __future__ import annotations
import json
from typing import TextIO
def read_provisioning_payload(stream: TextIO) -> dict[str, str]:
    payload=json.load(stream)
    if not isinstance(payload, dict): raise ValueError("Provisioning input must be a JSON object.")
    account=str(payload.get("account", "")).strip()
    if not account: raise ValueError("Provisioning input must include account.")
    email=str(payload.get("email", "")).strip()
    return {"account":account,"email":email}
