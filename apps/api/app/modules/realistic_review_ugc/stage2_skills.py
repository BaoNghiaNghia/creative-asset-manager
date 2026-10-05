from __future__ import annotations

import hashlib
import json
import logging
import mimetypes
import os
import re
import shutil
import tempfile
import time
import zipfile
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from openai import OpenAI, OpenAIError

from app.core.config import Settings, get_settings
from app.providers.ai.codex_image import (
    CodexImageProviderError,
    CodexSkillManifest,
    list_codex_skill_manifests,
    load_codex_skill_manifest,
)

logger = logging.getLogger(__name__)

CACHE_TTL_SECONDS = 300.0
MAX_DOWNLOAD_BYTES = 50 * 1024 * 1024
MAX_FILES = 500
MAX_FILE_BYTES = 25 * 1024 * 1024
MAX_TOTAL_BYTES = 250 * 1024 * 1024
SAFE_SKILL_RE = re.compile(r"^[a-z0-9][a-z0-9-]{0,63}$")
VERSION_RE = re.compile(r"(?m)^version:\s*([^\n#]+?)\s*$")
_catalog_cache: dict[str, tuple[float, "Stage2SkillCatalog"]] = {}


class Stage2SkillRegistryError(RuntimeError):
    def __init__(self, code: str, message: str, *, status_code: int = 409):
        super().__init__(message)
        self.code = code
        self.message = message
        self.status_code = status_code


@dataclass(frozen=True, slots=True)
class Stage2SkillItem:
    source: str
    skill_id: str | None
    skill_name: str
    display_name: str
    description: str
    default_version: str | None
    latest_version: str | None
    local_version: str | None
    synced_version: str | None
    ready: bool
    sync_state: str
    version_options: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class Stage2SkillCatalog:
    openai_configured: bool
    openai_status: str
    error_code: str | None
    items: tuple[Stage2SkillItem, ...]


@dataclass(frozen=True, slots=True)
class ResolvedStage2Skill:
    source: str
    skill_id: str | None
    skill_name: str
    skill_version: str | None


@dataclass(frozen=True, slots=True)
class UploadedStage2SkillBundle:
    skill_name: str
    display_name: str
    description: str
    version: str | None
    bundle_sha256: str
    files: tuple[tuple[str, bytes, str], ...]


def _codex_home(settings: Settings) -> Path:
    return Path(
        str(getattr(settings, "CODEX_IMAGE_HOME", "/var/lib/creative-asset-manager/codex"))
    ).expanduser().resolve()


def _client(settings: Settings) -> OpenAI:
    kwargs: dict[str, Any] = {
        "api_key": settings.OPENAI_API_KEY,
        "timeout": min(max(float(settings.OPENAI_TIMEOUT_SECONDS or 15.0), 3.0), 60.0),
        "max_retries": max(0, int(settings.OPENAI_MAX_RETRIES or 0)),
    }
    if settings.OPENAI_BASE_URL:
        kwargs["base_url"] = settings.OPENAI_BASE_URL
    if settings.OPENAI_ORGANIZATION:
        kwargs["organization"] = settings.OPENAI_ORGANIZATION
    if settings.OPENAI_PROJECT:
        kwargs["project"] = settings.OPENAI_PROJECT
    return OpenAI(**kwargs)





def inspect_uploaded_stage2_skill_bundle(bundle: bytes) -> UploadedStage2SkillBundle:
    if not bundle:
        raise Stage2SkillRegistryError(
            "stage2_skill_bundle_empty",
            "Upload a non-empty skill ZIP.",
            status_code=422,
        )
    temp_root = Path(tempfile.mkdtemp(prefix=".stage2-skill-upload-"))
    try:
        root = _safe_extract_bundle(bundle, temp_root)
        matches = [
            path
            for path in root.rglob("*")
            if path.is_file() and path.name.casefold() == "skill.md"
        ]
        if len(matches) != 1:
            raise Stage2SkillRegistryError(
                "stage2_skill_bundle_invalid",
                "Skill bundle must contain exactly one SKILL.md.",
                status_code=422,
            )
        skill_md = matches[0]
        text = skill_md.read_text(encoding="utf-8")
        name_match = re.search(r"(?m)^name:\s*([^\n#]+?)\s*$", text)
        skill_name = (
            name_match.group(1).strip().strip('"').strip("'")
            if name_match
            else ""
        )
        if not SAFE_SKILL_RE.fullmatch(skill_name):
            raise Stage2SkillRegistryError(
                "stage2_skill_name_invalid",
                "SKILL.md must declare a lowercase kebab-case name.",
                status_code=422,
            )
        description_match = re.search(r"(?m)^description:\s*([^\n#]+?)\s*$", text)
        description = (
            description_match.group(1).strip().strip('"').strip("'")
            if description_match
            else ""
        )
        version_match = VERSION_RE.search(text)
        version = (
            version_match.group(1).strip().strip('"').strip("'")
            if version_match
            else None
        )
        manifest_path = root / "manifest.json"
        display_name = skill_name
        if manifest_path.is_file():
            try:
                manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError) as exc:
                raise Stage2SkillRegistryError(
                    "stage2_skill_manifest_invalid",
                    "manifest.json is invalid JSON.",
                    status_code=422,
                ) from exc
            if not isinstance(manifest, dict):
                raise Stage2SkillRegistryError(
                    "stage2_skill_manifest_invalid",
                    "manifest.json must contain an object.",
                    status_code=422,
                )
            workflows = manifest.get("workflows") or []
            if "image_studio" not in workflows:
                raise Stage2SkillRegistryError(
                    "stage2_skill_workflow_invalid",
                    "Stage 2 skills must declare the image_studio workflow.",
                    status_code=422,
                )
            manifest_name = str(manifest.get("skill_name") or skill_name).strip()
            if manifest_name != skill_name:
                raise Stage2SkillRegistryError(
                    "stage2_skill_name_mismatch",
                    "manifest.json skill_name must match SKILL.md.",
                    status_code=422,
                )
            display_name = str(manifest.get("display_name") or skill_name).strip() or skill_name
            description = str(manifest.get("description") or description).strip()

        files: list[tuple[str, bytes, str]] = []
        for path in sorted(root.rglob("*")):
            if not path.is_file():
                continue
            relative = path.relative_to(root).as_posix()
            content_type = mimetypes.guess_type(relative)[0] or "application/octet-stream"
            files.append((relative, path.read_bytes(), content_type))
        return UploadedStage2SkillBundle(
            skill_name=skill_name,
            display_name=display_name,
            description=description,
            version=version,
            bundle_sha256=hashlib.sha256(bundle).hexdigest(),
            files=tuple(files),
        )
    except UnicodeDecodeError as exc:
        raise Stage2SkillRegistryError(
            "stage2_skill_bundle_invalid",
            "SKILL.md must be UTF-8 text.",
            status_code=422,
        ) from exc
    finally:
        shutil.rmtree(temp_root, ignore_errors=True)


def _ensure_uploaded_local_manifest(
    root: Path,
    bundle: UploadedStage2SkillBundle,
) -> None:
    path = root / "manifest.json"
    if path.is_file():
        return
    path.write_text(
        json.dumps(
            {
                "schema_version": 1,
                "skill_name": bundle.skill_name,
                "display_name": bundle.display_name or bundle.skill_name,
                "description": bundle.description or f"Stage 2 skill {bundle.skill_name}",
                "workflows": ["image_studio"],
                "product_types": [],
                "required_reference_roles": [],
                "optional_reference_roles": [],
                "max_references": 10,
            },
            ensure_ascii=False,
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )


def install_local_stage2_skill_bundle(
    bundle: UploadedStage2SkillBundle,
    *,
    settings: Settings | None = None,
    replace: bool = False,
) -> Stage2SkillItem:
    """Install an uploaded Stage 2 skill into CAM's persistent Codex runtime.

    This path is intentionally independent of OPENAI_API_KEY. OpenAI-hosted
    skill discovery/sync remains a separate optional integration.
    """

    settings = settings or get_settings()
    home = _codex_home(settings)
    skills_root = home / "skills"
    try:
        skills_root.mkdir(parents=True, exist_ok=True)
    except OSError as exc:
        raise Stage2SkillRegistryError(
            "stage2_skill_install_failed",
            "Unable to prepare the Stage 2 skill runtime.",
            status_code=500,
        ) from exc

    target = skills_root / bundle.skill_name
    if target.exists() and not replace:
        raise Stage2SkillRegistryError(
            "stage2_skill_already_exists",
            "A Stage 2 skill with this name is already installed.",
            status_code=409,
        )
    if target.exists() and _sync_metadata(home, bundle.skill_name) is not None:
        raise Stage2SkillRegistryError(
            "stage2_skill_local_conflict",
            "An OpenAI-synced skill already uses this name. Remove that sync before uploading a local skill.",
            status_code=409,
        )

    temp_root = Path(
        tempfile.mkdtemp(
            prefix=".stage2-skill-local-",
            dir=str(skills_root),
        )
    )
    staged = temp_root / "staged"
    backup = temp_root / "backup"
    replaced_existing = False
    try:
        staged.mkdir(parents=True, exist_ok=False)
        for relative, body, _content_type in bundle.files:
            relative_path = Path(relative)
            if (
                relative_path.is_absolute()
                or ".." in relative_path.parts
                or not relative_path.parts
            ):
                raise Stage2SkillRegistryError(
                    "stage2_skill_bundle_invalid",
                    "Skill bundle contains an unsafe path.",
                    status_code=422,
                )
            destination = staged / relative_path
            destination.parent.mkdir(parents=True, exist_ok=True)
            destination.write_bytes(body)

        # Uploaded local skills must never be able to impersonate an OpenAI
        # synced runtime entry.
        (staged / ".openai-skill.json").unlink(missing_ok=True)
        if not (staged / "SKILL.md").is_file():
            raise Stage2SkillRegistryError(
                "stage2_skill_bundle_invalid",
                "SKILL.md must be at the root of the uploaded skill ZIP.",
                status_code=422,
            )
        _ensure_uploaded_local_manifest(staged, bundle)

        verify_home = temp_root / "verify"
        verify_target = verify_home / "skills" / bundle.skill_name
        verify_target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copytree(staged, verify_target)
        try:
            manifest = load_codex_skill_manifest(verify_home, bundle.skill_name)
        except CodexImageProviderError as exc:
            raise Stage2SkillRegistryError(
                "stage2_skill_manifest_invalid",
                "The uploaded skill manifest is not valid for Stage 2.",
                status_code=422,
            ) from exc
        if manifest is None or "image_studio" not in manifest.workflows:
            raise Stage2SkillRegistryError(
                "stage2_skill_workflow_invalid",
                "Stage 2 skills must declare the image_studio workflow.",
                status_code=422,
            )

        if target.exists():
            os.replace(target, backup)
            replaced_existing = True
        os.replace(staged, target)

        try:
            installed = load_codex_skill_manifest(home, bundle.skill_name)
            if installed is None or "image_studio" not in installed.workflows:
                raise Stage2SkillRegistryError(
                    "stage2_skill_install_verification_failed",
                    "The uploaded skill could not be verified after installation.",
                    status_code=500,
                )
        except (CodexImageProviderError, Stage2SkillRegistryError):
            if target.exists():
                shutil.rmtree(target)
            if replaced_existing and backup.exists():
                os.replace(backup, target)
            raise

        item = _local_item(settings, installed)
        if item is None or not item.ready:
            if target.exists():
                shutil.rmtree(target)
            if replaced_existing and backup.exists():
                os.replace(backup, target)
            raise Stage2SkillRegistryError(
                "stage2_skill_install_verification_failed",
                "The uploaded skill could not be loaded by the Stage 2 runtime.",
                status_code=500,
            )
        _catalog_cache.clear()
        logger.info(
            "stage2_local_skill_installed skill_name=%s version=%s sha256=%s",
            bundle.skill_name,
            bundle.version or "",
            bundle.bundle_sha256,
        )
        return item
    except Stage2SkillRegistryError:
        raise
    except OSError as exc:
        if target.exists() and replaced_existing:
            shutil.rmtree(target, ignore_errors=True)
            if backup.exists():
                os.replace(backup, target)
        raise Stage2SkillRegistryError(
            "stage2_skill_install_failed",
            "Unable to install the uploaded Stage 2 skill.",
            status_code=500,
        ) from exc
    finally:
        shutil.rmtree(temp_root, ignore_errors=True)


def _require_openai_skills(settings: Settings) -> OpenAI:
    if not settings.OPENAI_API_KEY:
        raise Stage2SkillRegistryError(
            "openai_skills_not_configured",
            "OPENAI_API_KEY is required to manage hosted skills.",
            status_code=503,
        )
    return _client(settings)


def create_openai_stage2_skill(
    bundle: UploadedStage2SkillBundle,
    *,
    settings: Settings | None = None,
) -> dict[str, Any]:
    settings = settings or get_settings()
    try:
        skill = _require_openai_skills(settings).skills.create(files=list(bundle.files))
    except (OpenAIError, ValueError, AttributeError) as exc:
        logger.exception("stage2_skill_create_failed skill_name=%s", bundle.skill_name)
        raise Stage2SkillRegistryError(
            "openai_skill_create_failed",
            "Unable to create the OpenAI skill.",
            status_code=503,
        ) from exc
    _catalog_cache.clear()
    return {
        "id": str(skill.id),
        "name": str(skill.name),
        "description": str(getattr(skill, "description", "") or ""),
        "default_version": str(getattr(skill, "default_version", "") or "") or None,
        "latest_version": str(getattr(skill, "latest_version", "") or "") or None,
    }


def create_openai_stage2_skill_version(
    skill_id: str,
    bundle: UploadedStage2SkillBundle,
    *,
    make_default: bool = False,
    settings: Settings | None = None,
) -> str:
    settings = settings or get_settings()
    try:
        version = _require_openai_skills(settings).skills.versions.create(
            skill_id,
            default=bool(make_default),
            files=list(bundle.files),
        )
    except (OpenAIError, ValueError, AttributeError) as exc:
        logger.exception("stage2_skill_version_create_failed skill_id=%s", skill_id)
        raise Stage2SkillRegistryError(
            "openai_skill_version_create_failed",
            "Unable to create the OpenAI skill version.",
            status_code=503,
        ) from exc
    _catalog_cache.clear()
    return str(version.version)


def list_openai_stage2_skill_versions(
    skill_id: str,
    *,
    settings: Settings | None = None,
) -> tuple[str, ...]:
    settings = settings or get_settings()
    try:
        page = _require_openai_skills(settings).skills.versions.list(
            skill_id,
            limit=100,
            order="desc",
        )
        versions: list[str] = []
        while True:
            for item in page.data:
                value = str(getattr(item, "version", "") or "").strip()
                if value and value not in versions:
                    versions.append(value)
            if not page.has_next_page():
                break
            page = page.get_next_page()
        return tuple(versions)
    except (OpenAIError, ValueError, AttributeError) as exc:
        logger.exception("stage2_skill_versions_list_failed skill_id=%s", skill_id)
        raise Stage2SkillRegistryError(
            "openai_skill_versions_unavailable",
            "Unable to list OpenAI skill versions.",
            status_code=503,
        ) from exc


def set_openai_stage2_skill_default(
    skill_id: str,
    version: str,
    *,
    settings: Settings | None = None,
) -> None:
    settings = settings or get_settings()
    try:
        _require_openai_skills(settings).skills.update(
            skill_id,
            default_version=str(version),
        )
    except (OpenAIError, ValueError, AttributeError) as exc:
        logger.exception(
            "stage2_skill_default_update_failed skill_id=%s version=%s",
            skill_id,
            version,
        )
        raise Stage2SkillRegistryError(
            "openai_skill_default_update_failed",
            "Unable to change the OpenAI skill default version.",
            status_code=503,
        ) from exc
    _catalog_cache.clear()


def delete_openai_stage2_skill(
    skill_id: str,
    *,
    settings: Settings | None = None,
) -> None:
    settings = settings or get_settings()
    try:
        _require_openai_skills(settings).skills.delete(skill_id)
    except (OpenAIError, ValueError, AttributeError) as exc:
        logger.exception("stage2_skill_delete_failed skill_id=%s", skill_id)
        raise Stage2SkillRegistryError(
            "openai_skill_delete_failed",
            "Unable to delete the OpenAI skill.",
            status_code=503,
        ) from exc
    _catalog_cache.clear()


def delete_openai_stage2_skill_version(
    skill_id: str,
    version: str,
    *,
    settings: Settings | None = None,
) -> None:
    settings = settings or get_settings()
    try:
        _require_openai_skills(settings).skills.versions.delete(
            str(version),
            skill_id=skill_id,
        )
    except (OpenAIError, ValueError, AttributeError) as exc:
        logger.exception(
            "stage2_skill_version_delete_failed skill_id=%s version=%s",
            skill_id,
            version,
        )
        raise Stage2SkillRegistryError(
            "openai_skill_version_delete_failed",
            "Unable to delete the OpenAI skill version.",
            status_code=503,
        ) from exc
    _catalog_cache.clear()


def download_openai_stage2_skill_version(
    skill_id: str,
    version: str,
    *,
    settings: Settings | None = None,
) -> bytes:
    settings = settings or get_settings()
    try:
        content = _require_openai_skills(settings).skills.versions.content.retrieve(
            str(version),
            skill_id=skill_id,
        )
        return content.read()
    except (OpenAIError, ValueError, AttributeError) as exc:
        logger.exception(
            "stage2_skill_download_failed skill_id=%s version=%s",
            skill_id,
            version,
        )
        raise Stage2SkillRegistryError(
            "openai_skill_download_failed",
            "Unable to download the selected OpenAI skill version.",
            status_code=503,
        ) from exc


def installed_stage2_skill_sha256(
    skill_name: str,
    *,
    settings: Settings | None = None,
) -> str | None:
    settings = settings or get_settings()
    root = _codex_home(settings) / "skills" / skill_name
    if not root.is_dir():
        return None
    digest = hashlib.sha256()
    for path in sorted(root.rglob("*")):
        if not path.is_file():
            continue
        relative = path.relative_to(root).as_posix()
        if relative == ".openai-skill.json":
            continue
        digest.update(relative.encode("utf-8"))
        digest.update(b"\0")
        digest.update(path.read_bytes())
        digest.update(b"\0")
    return digest.hexdigest()


def _cache_key(settings: Settings) -> str:
    return "|".join(
        [
            str(settings.OPENAI_BASE_URL or "default"),
            str(settings.OPENAI_PROJECT or ""),
            str(settings.OPENAI_ORGANIZATION or ""),
            str(_codex_home(settings)),
            "configured" if settings.OPENAI_API_KEY else "local",
        ]
    )


def _skill_md_version(home: Path, skill_name: str) -> str | None:
    path = home / "skills" / skill_name / "SKILL.md"
    try:
        text = path.read_text(encoding="utf-8")
    except OSError:
        return None
    match = VERSION_RE.search(text)
    if match is None:
        return None
    return match.group(1).strip().strip('"').strip("'") or None


def _sync_metadata(home: Path, skill_name: str) -> dict[str, Any] | None:
    path = home / "skills" / skill_name / ".openai-skill.json"
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError, TypeError):
        return None
    return payload if isinstance(payload, dict) else None


def _local_manifests(settings: Settings) -> dict[str, CodexSkillManifest]:
    home = _codex_home(settings)
    return {
        item.skill_name: item
        for item in list_codex_skill_manifests(home, workflow="image_studio")
    }


def _local_item(settings: Settings, manifest: CodexSkillManifest) -> Stage2SkillItem | None:
    home = _codex_home(settings)
    if _sync_metadata(home, manifest.skill_name):
        return None
    version = _skill_md_version(home, manifest.skill_name)
    return Stage2SkillItem(
        source="local",
        skill_id=None,
        skill_name=manifest.skill_name,
        display_name=manifest.display_name,
        description=manifest.description,
        default_version=version,
        latest_version=version,
        local_version=version,
        synced_version=version,
        ready=True,
        sync_state="ready",
        version_options=(version,) if version else (),
    )


def _fetch_openai_skills(settings: Settings) -> list[dict[str, Any]]:
    if not settings.OPENAI_API_KEY:
        return []
    rows: list[dict[str, Any]] = []
    page = _client(settings).skills.list(limit=100, order="desc")
    while True:
        for skill in page.data:
            rows.append(
                {
                    "id": skill.id,
                    "name": skill.name,
                    "description": skill.description,
                    "default_version": str(skill.default_version),
                    "latest_version": str(skill.latest_version),
                }
            )
        if not page.has_next_page():
            break
        page = page.get_next_page()
    return rows


def _remote_item(
    settings: Settings,
    raw: dict[str, Any],
    local: dict[str, CodexSkillManifest],
) -> Stage2SkillItem | None:
    skill_id = str(raw.get("id") or "").strip()
    skill_name = str(raw.get("name") or "").strip()
    if not skill_id or not SAFE_SKILL_RE.fullmatch(skill_name):
        return None
    default_version = str(raw.get("default_version") or "").strip() or None
    latest_version = str(raw.get("latest_version") or "").strip() or None
    versions = list(dict.fromkeys(v for v in (default_version, latest_version) if v))
    home = _codex_home(settings)
    manifest = local.get(skill_name)
    meta = _sync_metadata(home, skill_name)
    synced_version: str | None = None
    ready = False
    state = "not_synced"
    if manifest is not None and meta is None:
        state = "local_conflict"
    elif manifest is not None and meta is not None:
        synced_id = str(meta.get("skill_id") or "").strip()
        synced_version = str(meta.get("version") or "").strip() or None
        if synced_id != skill_id:
            state = "local_conflict"
        elif synced_version:
            ready = True
            state = "ready" if synced_version == default_version else "update_available"
            if synced_version not in versions:
                versions.append(synced_version)

    return Stage2SkillItem(
        source="openai",
        skill_id=skill_id,
        skill_name=skill_name,
        display_name=skill_name,
        description=str(raw.get("description") or "").strip(),
        default_version=default_version,
        latest_version=latest_version,
        local_version=_skill_md_version(home, skill_name) if manifest is not None else None,
        synced_version=synced_version,
        ready=ready,
        sync_state=state,
        version_options=tuple(versions),
    )


def list_stage2_skill_catalog(
    settings: Settings | None = None,
    *,
    refresh: bool = False,
) -> Stage2SkillCatalog:
    settings = settings or get_settings()
    key = _cache_key(settings)
    now = time.monotonic()
    if not refresh and key in _catalog_cache and _catalog_cache[key][0] > now:
        return _catalog_cache[key][1]

    local = _local_manifests(settings)
    items: list[Stage2SkillItem] = []
    status = "not_configured"
    error_code: str | None = None
    remote_rows: list[dict[str, Any]] = []
    if settings.OPENAI_API_KEY:
        try:
            remote_rows = _fetch_openai_skills(settings)
            status = "connected"
        except (OpenAIError, ValueError, AttributeError):
            status = "error"
            error_code = "openai_skills_unavailable"
            logger.exception("stage2_skills_list_failed")

    for raw in remote_rows:
        item = _remote_item(settings, raw, local)
        if item is not None:
            items.append(item)
    for manifest in local.values():
        item = _local_item(settings, manifest)
        if item is not None:
            items.append(item)

    items.sort(
        key=lambda item: (
            0 if item.ready else 1,
            0 if item.source == "openai" else 1,
            item.display_name.casefold(),
        )
    )
    catalog = Stage2SkillCatalog(
        openai_configured=bool(settings.OPENAI_API_KEY),
        openai_status=status,
        error_code=error_code,
        items=tuple(items),
    )
    _catalog_cache[key] = (now + CACHE_TTL_SECONDS, catalog)
    return catalog


def verify_stage2_skill_runtime(
    *,
    settings: Settings | None = None,
    skill_source: str,
    skill_id: str | None,
    skill_name: str,
    skill_version: str | None,
) -> CodexSkillManifest:
    settings = settings or get_settings()
    source = str(skill_source or "local").strip().lower()
    if source not in {"local", "openai"}:
        raise Stage2SkillRegistryError(
            "stage2_skill_source_invalid",
            "The pinned Stage 2 skill source is invalid.",
            status_code=422,
        )

    home = _codex_home(settings)
    manifest = load_codex_skill_manifest(home, skill_name)
    if manifest is None or "image_studio" not in manifest.workflows:
        raise Stage2SkillRegistryError(
            "stage2_skill_runtime_missing",
            "The pinned Stage 2 skill is not installed on this worker.",
            status_code=409,
        )

    pinned_version = str(skill_version or "").strip() or None
    if source == "openai":
        meta = _sync_metadata(home, skill_name)
        if meta is None:
            raise Stage2SkillRegistryError(
                "stage2_skill_runtime_not_synced",
                "The pinned OpenAI skill is no longer synced on this worker.",
                status_code=409,
            )
        synced_id = str(meta.get("skill_id") or "").strip() or None
        synced_version = str(meta.get("version") or "").strip() or None
        if not skill_id or synced_id != skill_id:
            raise Stage2SkillRegistryError(
                "stage2_skill_runtime_id_mismatch",
                "The installed OpenAI skill does not match the skill pinned by this job.",
                status_code=409,
            )
        if pinned_version and synced_version != pinned_version:
            raise Stage2SkillRegistryError(
                "stage2_skill_runtime_version_mismatch",
                "The installed OpenAI skill version changed after this job was queued.",
                status_code=409,
            )
        return manifest

    current_version = _skill_md_version(home, skill_name)
    if pinned_version and current_version != pinned_version:
        raise Stage2SkillRegistryError(
            "stage2_skill_runtime_version_mismatch",
            "The installed local skill version changed after this job was queued.",
            status_code=409,
        )
    return manifest


def resolve_stage2_skill(
    *,
    settings: Settings | None = None,
    skill_source: str | None,
    skill_id: str | None,
    skill_name: str | None,
    skill_version: str | None,
    fallback_skill_name: str,
) -> ResolvedStage2Skill:
    settings = settings or get_settings()
    source = str(skill_source or "").strip().lower()
    requested_id = str(skill_id or "").strip() or None
    requested_name = str(skill_name or fallback_skill_name).strip() or fallback_skill_name
    requested_version = str(skill_version or "").strip() or None
    catalog = list_stage2_skill_catalog(settings)
    candidates = [
        item
        for item in catalog.items
        if (
            (requested_id is not None and item.skill_id == requested_id)
            or (
                requested_id is None
                and item.skill_name == requested_name
                and (not source or item.source == source)
            )
        )
    ]
    if not candidates:
        raise Stage2SkillRegistryError(
            "stage2_skill_not_found",
            "The selected Stage 2 skill is no longer available.",
            status_code=422,
        )
    item = candidates[0]
    if not item.ready:
        raise Stage2SkillRegistryError(
            "stage2_skill_local_conflict"
            if item.sync_state == "local_conflict"
            else "stage2_skill_not_synced",
            "Sync the selected OpenAI skill version to the Stage 2 worker before generating.",
            status_code=409,
        )
    resolved_version = requested_version or item.synced_version or item.default_version
    if item.source == "openai" and resolved_version != item.synced_version:
        raise Stage2SkillRegistryError(
            "stage2_skill_version_not_synced",
            "The selected OpenAI skill version is not synced to the Stage 2 worker.",
            status_code=409,
        )
    return ResolvedStage2Skill(
        source=item.source,
        skill_id=item.skill_id,
        skill_name=item.skill_name,
        skill_version=resolved_version,
    )


def _safe_extract_bundle(bundle: bytes, destination: Path) -> Path:
    if len(bundle) > MAX_DOWNLOAD_BYTES:
        raise Stage2SkillRegistryError(
            "stage2_skill_bundle_too_large",
            "OpenAI skill bundle exceeds the 50 MB Stage 2 sync limit.",
            status_code=422,
        )
    archive_path = destination / "skill.zip"
    archive_path.write_bytes(bundle)
    extract_root = destination / "extract"
    extract_root.mkdir(parents=True, exist_ok=True)
    try:
        with zipfile.ZipFile(archive_path) as archive:
            members: list[zipfile.ZipInfo] = []
            total = 0
            for member in archive.infolist():
                normalized = member.filename.replace("\\", "/")
                if normalized.startswith("/") or ".." in Path(normalized).parts:
                    raise Stage2SkillRegistryError(
                        "stage2_skill_bundle_invalid",
                        "OpenAI skill bundle contains an unsafe path.",
                        status_code=422,
                    )
                if member.is_dir():
                    continue
                file_type = (member.external_attr >> 16) & 0o170000
                if file_type == 0o120000:
                    raise Stage2SkillRegistryError(
                        "stage2_skill_bundle_invalid",
                        "OpenAI skill bundle contains a symbolic link.",
                        status_code=422,
                    )
                if member.file_size > MAX_FILE_BYTES:
                    raise Stage2SkillRegistryError(
                        "stage2_skill_bundle_too_large",
                        "OpenAI skill bundle contains a file that is too large.",
                        status_code=422,
                    )
                total += member.file_size
                if total > MAX_TOTAL_BYTES:
                    raise Stage2SkillRegistryError(
                        "stage2_skill_bundle_too_large",
                        "OpenAI skill bundle is too large after extraction.",
                        status_code=422,
                    )
                members.append(member)
                if len(members) > MAX_FILES:
                    raise Stage2SkillRegistryError(
                        "stage2_skill_bundle_too_many_files",
                        "OpenAI skill bundle contains too many files.",
                        status_code=422,
                    )
            archive.extractall(extract_root, members=members)
    except zipfile.BadZipFile as exc:
        raise Stage2SkillRegistryError(
            "stage2_skill_bundle_invalid",
            "OpenAI skill content is not a valid zip bundle.",
            status_code=422,
        ) from exc

    entries = [entry for entry in extract_root.iterdir() if entry.name != "__MACOSX"]
    return entries[0] if len(entries) == 1 and entries[0].is_dir() else extract_root


def _validate_bundle(root: Path, item: Stage2SkillItem) -> None:
    matches = [
        path
        for path in root.rglob("*")
        if path.is_file() and path.name.casefold() == "skill.md"
    ]
    if len(matches) != 1:
        raise Stage2SkillRegistryError(
            "stage2_skill_bundle_invalid",
            "OpenAI skill bundle must contain exactly one SKILL.md.",
            status_code=422,
        )
    text = matches[0].read_text(encoding="utf-8")
    match = re.search(r"(?m)^name:\s*([^\n#]+?)\s*$", text)
    declared = match.group(1).strip().strip('"').strip("'") if match else ""
    if declared and declared != item.skill_name:
        raise Stage2SkillRegistryError(
            "stage2_skill_name_mismatch",
            "OpenAI skill bundle name does not match the selected skill.",
            status_code=422,
        )


def _ensure_manifest(root: Path, item: Stage2SkillItem) -> None:
    path = root / "manifest.json"
    if path.is_file():
        return
    path.write_text(
        json.dumps(
            {
                "schema_version": 1,
                "skill_name": item.skill_name,
                "display_name": item.display_name or item.skill_name,
                "description": item.description or f"OpenAI skill {item.skill_name}",
                "workflows": ["image_studio"],
                "product_types": [],
                "required_reference_roles": [],
                "optional_reference_roles": [],
                "max_references": 10,
            },
            ensure_ascii=False,
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )


def sync_openai_stage2_skill(
    skill_id: str,
    *,
    version: str | None = None,
    settings: Settings | None = None,
) -> Stage2SkillItem:
    settings = settings or get_settings()
    if not settings.OPENAI_API_KEY:
        raise Stage2SkillRegistryError(
            "openai_skills_not_configured",
            "OPENAI_API_KEY is required to sync OpenAI skills.",
            status_code=503,
        )
    catalog = list_stage2_skill_catalog(settings, refresh=True)
    item = next(
        (
            value
            for value in catalog.items
            if value.source == "openai" and value.skill_id == skill_id
        ),
        None,
    )
    if item is None:
        raise Stage2SkillRegistryError(
            "stage2_skill_not_found",
            "The selected OpenAI skill is no longer available.",
            status_code=404,
        )
    if item.sync_state == "local_conflict":
        raise Stage2SkillRegistryError(
            "stage2_skill_local_conflict",
            "A project-managed local skill already uses this name. Rename the OpenAI skill or remove the local conflict before syncing.",
            status_code=409,
        )
    target_version = str(version or item.default_version or item.latest_version or "").strip()
    if not target_version:
        raise Stage2SkillRegistryError(
            "stage2_skill_version_missing",
            "The selected OpenAI skill has no usable version.",
            status_code=422,
        )
    if item.version_options and target_version not in item.version_options:
        raise Stage2SkillRegistryError(
            "stage2_skill_version_invalid",
            "Select the current default or latest OpenAI skill version.",
            status_code=422,
        )

    try:
        content = _client(settings).skills.versions.content.retrieve(
            target_version,
            skill_id=skill_id,
        )
        bundle = content.read()
    except (OpenAIError, ValueError, AttributeError) as exc:
        logger.exception(
            "stage2_skill_download_failed skill_id=%s version=%s",
            skill_id,
            target_version,
        )
        raise Stage2SkillRegistryError(
            "openai_skill_download_failed",
            "Unable to download the selected OpenAI skill version.",
            status_code=503,
        ) from exc

    home = _codex_home(settings)
    skills_root = home / "skills"
    skills_root.mkdir(parents=True, exist_ok=True)
    target = skills_root / item.skill_name
    existing_meta = _sync_metadata(home, item.skill_name)
    if target.exists() and not existing_meta:
        raise Stage2SkillRegistryError(
            "stage2_skill_local_conflict",
            "A project-managed local skill already uses this name.",
            status_code=409,
        )

    temp_root = Path(tempfile.mkdtemp(prefix=".stage2-skill-sync-", dir=str(skills_root)))
    staged = temp_root / "staged"
    backup = skills_root / (".backup-" + item.skill_name)
    try:
        source = _safe_extract_bundle(bundle, temp_root)
        _validate_bundle(source, item)
        shutil.copytree(source, staged)
        _ensure_manifest(staged, item)
        (staged / ".openai-skill.json").write_text(
            json.dumps(
                {
                    "skill_id": skill_id,
                    "skill_name": item.skill_name,
                    "version": target_version,
                    "synced_at": int(time.time()),
                },
                ensure_ascii=False,
                indent=2,
            )
            + "\n",
            encoding="utf-8",
        )
        if backup.exists():
            shutil.rmtree(backup)
        if target.exists():
            os.replace(target, backup)
        os.replace(staged, target)
        try:
            manifest = load_codex_skill_manifest(home, item.skill_name)
            if manifest is None or "image_studio" not in manifest.workflows:
                raise Stage2SkillRegistryError(
                    "stage2_skill_manifest_invalid",
                    "Synced skill is not compatible with the Stage 2 image_studio workflow.",
                    status_code=422,
                )
        except (CodexImageProviderError, Stage2SkillRegistryError):
            if target.exists():
                shutil.rmtree(target)
            if backup.exists():
                os.replace(backup, target)
            raise
        if backup.exists():
            shutil.rmtree(backup)
    finally:
        shutil.rmtree(temp_root, ignore_errors=True)

    _catalog_cache.clear()
    refreshed = list_stage2_skill_catalog(settings, refresh=True)
    synced = next(
        (
            value
            for value in refreshed.items
            if value.source == "openai" and value.skill_id == skill_id
        ),
        None,
    )
    if synced is None:
        raise Stage2SkillRegistryError(
            "stage2_skill_sync_verification_failed",
            "Skill was downloaded but could not be verified after sync.",
            status_code=500,
        )
    logger.info(
        "stage2_skill_synced skill_id=%s skill_name=%s version=%s",
        skill_id,
        item.skill_name,
        target_version,
    )
    return synced
