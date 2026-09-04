from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class SourceCredentialContract:
    source_type: str
    provider: str
    connection_purpose: str
    adapter_key: str


_CONTRACTS = {
    "google_drive": SourceCredentialContract("google_drive", "google", "google_drive_source", "google-drive"),
    "onedrive": SourceCredentialContract("onedrive", "microsoft", "onedrive_source", "onedrive"),
    "sharepoint": SourceCredentialContract("sharepoint", "microsoft", "sharepoint_source", "sharepoint"),
}


def source_credential_contract(source_type: str) -> SourceCredentialContract:
    try:
        return _CONTRACTS[source_type]
    except KeyError as exc:
        raise ValueError("unsupported source type") from exc
