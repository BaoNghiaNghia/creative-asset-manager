from __future__ import annotations

import asyncio
import hashlib
import json
import os
import re
import signal
import shutil
from dataclasses import dataclass
from pathlib import Path

from PIL import Image

from app.providers.ai.codex_execution_log import CodexExecutionLog, stream_codex_process, prune_codex_execution_logs

from app.modules.image_generation.providers import (
    GeneratedImageResult,
    PreparedImage,
    ReferenceImageInput,
)


_SAFE_SKILL_RE = re.compile(r"^[a-z0-9][a-z0-9-]{0,63}$")
_SAFE_LABEL_RE = re.compile(r"[^a-z0-9]+")
_ALLOWED_OUTPUT_MIME = {
    "PNG": "image/png",
    "JPEG": "image/jpeg",
    "WEBP": "image/webp",
}


class CodexImageProviderError(RuntimeError):
    def __init__(
        self,
        code: str,
        message: str,
        *,
        retryable: bool = False,
        defer_seconds: int | None = None,
    ) -> None:
        super().__init__(message)
        self.code = code
        self.retryable = retryable
        self.defer_seconds = defer_seconds


@dataclass(frozen=True, slots=True)
class CodexImageRunnerConfig:
    binary: str = "codex"
    codex_home: str = "/var/lib/creative-asset-manager/codex"
    staging_root: str = "/var/lib/creative-asset-manager/image-generation"
    skill_name: str = "worker-hat-v1"
    timeout_seconds: int = 900
    model: str | None = None
    output_contract: str = "single_png"
    expected_quote: str | None = None
    execution_log_id: str | None = None


@dataclass(frozen=True, slots=True)
class CodexSkillManifest:
    schema_version: int
    skill_name: str
    display_name: str
    description: str
    workflows: tuple[str, ...]
    product_types: tuple[str, ...]
    required_reference_roles: tuple[str, ...]
    optional_reference_roles: tuple[str, ...]
    max_references: int

    def matches_product_type(self, product_type: str | None) -> bool:
        normalized = _normalize_manifest_token(product_type)
        return bool(normalized and normalized in self.product_types)

    def missing_reference_roles(self, roles: list[str] | tuple[str, ...]) -> list[str]:
        present = {_normalize_manifest_token(role) for role in roles}
        return [
            role for role in self.required_reference_roles
            if role not in present
        ]


def _normalize_manifest_token(value: object) -> str:
    return re.sub(r"[^a-z0-9]+", "_", str(value or "").strip().lower()).strip("_")


def _manifest_string_list(payload: dict, key: str) -> tuple[str, ...]:
    values = payload.get(key, [])
    if not isinstance(values, list):
        raise ValueError(f"{key} must be a list")
    normalized: list[str] = []
    for value in values:
        token = _normalize_manifest_token(value)
        if not token:
            raise ValueError(f"{key} contains an empty token")
        if token not in normalized:
            normalized.append(token)
    return tuple(normalized)


def load_codex_skill_manifest(
    codex_home: str | Path,
    skill_name: str,
) -> CodexSkillManifest | None:
    if not _SAFE_SKILL_RE.fullmatch(skill_name):
        return None
    manifest_path = Path(codex_home).resolve() / "skills" / skill_name / "manifest.json"
    if not manifest_path.is_file():
        return None
    try:
        payload = json.loads(manifest_path.read_text(encoding="utf-8"))
        if not isinstance(payload, dict):
            raise ValueError("manifest root must be an object")
        schema_version = int(payload.get("schema_version", 0))
        declared_skill = str(payload.get("skill_name") or "").strip()
        display_name = str(payload.get("display_name") or "").strip()
        description = str(payload.get("description") or "").strip()
        workflows = _manifest_string_list(payload, "workflows")
        product_types = _manifest_string_list(payload, "product_types")
        required_roles = _manifest_string_list(payload, "required_reference_roles")
        optional_roles = _manifest_string_list(payload, "optional_reference_roles")
        max_references = int(payload.get("max_references", 32))
        if schema_version != 1:
            raise ValueError("unsupported schema_version")
        if declared_skill != skill_name:
            raise ValueError("skill_name does not match directory")
        if not display_name:
            raise ValueError("display_name is required")
        if not workflows:
            raise ValueError("workflows must not be empty")
        if not 1 <= max_references <= 32:
            raise ValueError("max_references must be between 1 and 32")
    except (OSError, TypeError, ValueError, json.JSONDecodeError) as exc:
        raise CodexImageProviderError(
            "codex_skill_manifest_invalid",
            "Codex skill $" + skill_name + " has an invalid manifest.",
        ) from exc
    return CodexSkillManifest(
        schema_version=schema_version,
        skill_name=skill_name,
        display_name=display_name,
        description=description,
        workflows=workflows,
        product_types=product_types,
        required_reference_roles=required_roles,
        optional_reference_roles=optional_roles,
        max_references=max_references,
    )


def list_codex_skill_manifests(
    codex_home: str | Path,
    *,
    workflow: str | None = None,
) -> list[CodexSkillManifest]:
    skills_root = Path(codex_home).resolve() / "skills"
    if not skills_root.is_dir():
        return []
    workflow_token = _normalize_manifest_token(workflow)
    manifests: list[CodexSkillManifest] = []
    for child in sorted(skills_root.iterdir(), key=lambda item: item.name):
        if not child.is_dir() or not (child / "SKILL.md").is_file():
            continue
        try:
            manifest = load_codex_skill_manifest(skills_root.parent, child.name)
        except CodexImageProviderError:
            continue
        if manifest is None:
            continue
        if workflow_token and workflow_token not in manifest.workflows:
            continue
        manifests.append(manifest)
    return manifests


def recommend_codex_skill(
    manifests: list[CodexSkillManifest],
    *,
    product_type: str | None,
    fallback_skill: str | None = None,
) -> CodexSkillManifest | None:
    for manifest in manifests:
        if manifest.matches_product_type(product_type):
            return manifest
    fallback = str(fallback_skill or "").strip()
    if fallback:
        for manifest in manifests:
            if manifest.skill_name == fallback:
                return manifest
    return manifests[0] if manifests else None


def _extension(mime_type: str) -> str:
    return {
        "image/jpeg": ".jpg",
        "image/png": ".png",
        "image/webp": ".webp",
    }.get(mime_type, ".img")


def _safe_stem(value: str, *, fallback: str) -> str:
    normalized = _SAFE_LABEL_RE.sub("-", value.strip().lower()).strip("-")
    return (normalized or fallback)[:64]


def _codex_env(codex_home: Path) -> dict[str, str]:
    env = dict(os.environ)
    env["CODEX_HOME"] = str(codex_home)
    # Production Codex generation intentionally uses ChatGPT/Codex auth.
    # Never inherit API credentials as an implicit paid fallback.
    for key in (
        "OPENAI_API_KEY",
        "OPENAI_ORGANIZATION",
        "OPENAI_PROJECT",
        "OPENAI_BASE_URL",
        "CODEX_API_KEY",
        "OPENAI_EXECUTOR_API_KEY",
    ):
        env.pop(key, None)
    return env


def _classify_failure(stderr: str, stdout: str) -> CodexImageProviderError:
    combined = (stderr + "\n" + stdout).casefold()
    if any(
        token in combined
        for token in (
            "usage limit",
            "rate limit",
            "quota",
            "limit reached",
            "try again later",
            "too many requests",
        )
    ):
        return CodexImageProviderError(
            "codex_image_usage_limited",
            "Codex image generation usage is temporarily limited.",
            defer_seconds=3600,
        )
    if any(
        token in combined
        for token in (
            "not logged in",
            "login required",
            "authentication",
            "unauthorized",
            "sign in",
        )
    ):
        return CodexImageProviderError(
            "codex_image_auth_required",
            "Codex is not authenticated for image generation.",
            defer_seconds=3600,
        )
    if "skill" in combined and any(
        token in combined for token in ("not found", "unknown", "unavailable")
    ):
        return CodexImageProviderError(
            "codex_image_skill_unavailable",
            "The configured Codex image skill is unavailable.",
        )
    return CodexImageProviderError(
        "codex_image_provider_error",
        "Codex image generation did not complete successfully.",
        retryable=True,
    )


def _request_id_from_jsonl(stdout: str) -> str | None:
    for line in stdout.splitlines():
        try:
            event = json.loads(line)
        except (TypeError, ValueError):
            continue
        if not isinstance(event, dict):
            continue
        for key in ("thread_id", "session_id", "id"):
            value = event.get(key)
            if isinstance(value, str) and value:
                event_type = str(event.get("type") or "")
                if key != "id" or "thread" in event_type or "session" in event_type:
                    return value[:255]
    return None


class CodexImageGenRunner:
    provider_key = "codex"

    def __init__(self, config: CodexImageRunnerConfig):
        self.config = config

    def capability_reason(self) -> str | None:
        if not shutil.which(self.config.binary):
            return "Codex CLI is not installed."
        skill = self._skill_path()
        if not skill.is_file():
            return f"Codex skill ${self.config.skill_name} is not installed."
        return None

    async def generate_from_references(
        self,
        *,
        attempt_id: str,
        person: PreparedImage | None,
        references: list[ReferenceImageInput],
        prompt: str,
    ) -> GeneratedImageResult:
        if not _SAFE_SKILL_RE.fullmatch(self.config.skill_name):
            raise CodexImageProviderError(
                "codex_image_skill_invalid",
                "Configured Codex skill name is invalid.",
            )
        binary = shutil.which(self.config.binary)
        if not binary:
            raise CodexImageProviderError(
                "codex_image_cli_unavailable",
                "Codex CLI is not installed on this worker.",
            )
        codex_home = Path(self.config.codex_home).resolve()
        skill_path = self._skill_path()
        if not skill_path.is_file():
            raise CodexImageProviderError(
                "codex_image_skill_unavailable",
                f"Configured Codex skill ${self.config.skill_name} is unavailable.",
            )

        root = Path(self.config.staging_root).resolve() / "codex"
        workspace = root / attempt_id
        output_dir = workspace / "output"
        if workspace.exists():
            shutil.rmtree(workspace)
        output_dir.mkdir(parents=True, exist_ok=True)

        person_name = "person" + _extension(person.mime_type) if person is not None else None
        if person_name and person is not None:
            (workspace / person_name).write_bytes(person.image_bytes)
        reference_names: list[tuple[str, str]] = []
        seen: dict[str, int] = {}
        for index, item in enumerate(references, start=1):
            stem = _safe_stem(
                f"{item.role}-{item.label}",
                fallback=f"reference-{index}",
            )
            count = seen.get(stem, 0) + 1
            seen[stem] = count
            if count > 1:
                stem = f"{stem}-{count}"
            name = stem + _extension(item.image.mime_type)
            (workspace / name).write_bytes(item.image.image_bytes)
            reference_names.append((item.role, name))

        instruction = self._prompt(
            person_name=person_name,
            references=reference_names,
            user_prompt=prompt,
        )
        argv = [
            binary,
            "exec",
            "--json",
            "--ephemeral",
            "--skip-git-repo-check",
            "--approve-for-me",
            "-C",
            str(workspace),
        ]
        if self.config.model:
            argv.extend(["--model", self.config.model])
        argv.append(instruction)

        # Stream JSONL events; the CLI can produce gigabytes of logs during
        # long-running multi-concept generations. Never communicate() into RAM.
        if self.config.execution_log_id:
            prune_codex_execution_logs(self.config.staging_root)
        log = CodexExecutionLog(self.config.staging_root, self.config.execution_log_id)
        try:
            process = await asyncio.create_subprocess_exec(
                *argv,
                stdin=asyncio.subprocess.DEVNULL,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                env=_codex_env(codex_home),
                start_new_session=True,
            )
            stdout, stderr = await stream_codex_process(
                process, timeout_seconds=self.config.timeout_seconds, log=log,
            )
        except asyncio.TimeoutError as exc:
            if "process" in locals() and process.returncode is None:
                try:
                    if getattr(process, "pid", None):
                        os.killpg(process.pid, signal.SIGKILL)
                    else:
                        process.kill()
                except (OSError, ProcessLookupError):
                    try:
                        process.kill()
                    except ProcessLookupError:
                        pass
                try:
                    await asyncio.wait_for(process.wait(), timeout=8)
                except asyncio.TimeoutError:
                    pass
            raise CodexImageProviderError(
                "codex_image_timeout",
                "Codex exceeded the time limit. Its partial execution progress is retained in Skill logs.",
                retryable=True,
            ) from exc
        except asyncio.CancelledError:
            if "process" in locals() and process.returncode is None:
                try:
                    if getattr(process, "pid", None):
                        os.killpg(process.pid, signal.SIGKILL)
                    else:
                        process.kill()
                except ProcessLookupError:
                    pass
                try:
                    await asyncio.wait_for(process.wait(), timeout=8)
                except asyncio.TimeoutError:
                    pass
            raise
        except OSError as exc:
            log.close("startup_failed")
            raise CodexImageProviderError(
                "codex_image_cli_unavailable",
                "Codex CLI could not be started on this worker.",
                retryable=True,
            ) from exc
        if process.returncode != 0:
            raise _classify_failure(stderr, stdout)

        if self.config.output_contract == "independent_concepts_v4":
            image_bytes, second_image = self._read_independent_boards(workspace)
            extra_images = (second_image,)
        else:
            output_path = output_dir / "final.png"
            if not output_path.is_file():
                raise CodexImageProviderError(
                    "codex_image_output_missing",
                    "Codex completed without creating output/final.png.",
                    retryable=True,
                )
            image_bytes = self._valid_png(output_path)
            extra_images = ()

        return GeneratedImageResult(
            provider="codex",
            model=self.config.model,
            image_bytes=image_bytes,
            mime_type="image/png",
            provider_request_id=_request_id_from_jsonl(stdout),
            provider_metadata={"skill": self.config.skill_name},
            additional_images=extra_images,
        )

    @staticmethod
    def _valid_png(path: Path, *, min_size: int = 1) -> bytes:
        try:
            with Image.open(path) as image:
                image.load()
                if image.format != "PNG" or min(image.size) < min_size:
                    raise ValueError("Expected a complete PNG board, not a thumbnail.")
            return path.read_bytes()
        except (OSError, ValueError) as exc:
            raise CodexImageProviderError(
                "codex_image_output_invalid", "The generated image is not a valid full-size PNG.",
                retryable=True,
            ) from exc

    def _read_independent_boards(self, workspace: Path) -> tuple[bytes, bytes]:
        """Validate both final boards against the versioned v4 assembly audit.

        A single cap render or an incomplete 10-concept job is never success.
        This verifies structure and hashes, not human visual/embroidery approval.
        """
        output_dir = workspace / "output"
        try:
            latest = json.loads((output_dir / "latest.json").read_text(encoding="utf-8"))
            folder = latest["folder"]
            names = latest["outputs"]
            if not isinstance(folder, str) or not re.fullmatch(r"v[0-9]{3,}", folder):
                raise ValueError("Invalid output version")
            if (not isinstance(names, list) or len(names) != 2
                    or not isinstance(names[0], str) or not isinstance(names[1], str)
                    or not names[0].endswith("_01_design_concepts.png")
                    or not names[1].endswith("_02_13_colorways.png")
                    or any(Path(name).name != name for name in names)):
                raise ValueError("Expected the two named v4 boards")
            batch = output_dir / folder
            audit = json.loads((batch / "audit.json").read_text(encoding="utf-8"))
            state = json.loads((workspace / "v4-job" / "job_state.json").read_text(encoding="utf-8"))
            concepts = state["concepts"]
            if (not isinstance(concepts, dict)
                    or set(concepts) != {str(number) for number in range(1, 11)}
                    or any(not concepts[str(number)] for number in range(1, 11))):
                raise ValueError("Ten independent concepts are required")
            concept_hashes: set[str] = set()
            skeletons: dict[str, int] = {}
            for number in range(1, 11):
                item = concepts[str(number)][-1]
                relative = item["image"]
                if (not isinstance(relative, str) or Path(relative).is_absolute()
                        or ".." in Path(relative).parts):
                    raise ValueError("Invalid concept file path")
                artwork = workspace / "v4-job" / relative
                actual_hash = hashlib.sha256(self._valid_png(artwork, min_size=400)).hexdigest()
                if (item.get("sha256") != actual_hash or actual_hash in concept_hashes
                        or not item.get("approved") or not item.get("text_reviewed")
                        or not item.get("stitch_reviewed")):
                    raise ValueError("Unverified or duplicated artwork")
                concept_hashes.add(actual_hash)
                skeleton = item.get("skeleton")
                if not isinstance(skeleton, str) or not skeleton:
                    raise ValueError("Missing concept architecture")
                skeletons[skeleton] = skeletons.get(skeleton, 0) + 1
            if len(skeletons) < 6 or max(skeletons.values()) > 2:
                raise ValueError("Insufficient architecture diversity")
            hero = state.get("hero")
            if (not isinstance(hero, dict) or hero.get("number") not in range(1, 11)
                    or hero.get("sha256") != concepts[str(hero["number"])][-1]["sha256"]):
                raise ValueError("Hero does not match an approved concept")
            if (self.config.expected_quote is not None and state.get("quote") != self.config.expected_quote):
                raise ValueError("Quote mismatch")
            if (state.get("schema_version") != 4
                    or state.get("skill") != self.config.skill_name
                    or set(concepts) != {str(number) for number in range(1, 11)}
                    or any(not concepts[str(number)] or not concepts[str(number)][-1].get("approved")
                           for number in range(1, 11))
                    or len(state.get("color_threads", {})) != 13
                    or not state.get("hero")):
                raise ValueError("Incomplete independently approved concepts or colors")
            if (audit.get("status") != "STRUCTURAL_PASS_VISUAL_QA_REQUIRED"
                    or audit.get("concept_count") != 10 or audit.get("stock_count") != 13):
                raise ValueError("Invalid assembly quality gate")
            files = [batch / name for name in names]
            audited = audit["outputs"]
            if (len(audited) != 2 or any(
                str(Path(row["path"]).resolve()) != str(file.resolve())
                or hashlib.sha256(file.read_bytes()).hexdigest() != row["sha256"]
                for file, row in zip(files, audited)
            )):
                raise ValueError("Board hashes do not match assembly audit")
            return self._valid_png(files[0], min_size=500), self._valid_png(files[1], min_size=500)
        except (KeyError, TypeError, ValueError, OSError, json.JSONDecodeError) as exc:
            raise CodexImageProviderError(
                "codex_v4_boards_incomplete",
                "The Skill did not finish its 10-concept, 13-color, two-board workflow. "
                "The job must not be marked Completed.",
                retryable=False,
            ) from exc

    def cleanup_attempt(self, attempt_id: str) -> None:
        workspace = Path(self.config.staging_root).resolve() / "codex" / attempt_id
        try:
            shutil.rmtree(workspace)
        except FileNotFoundError:
            pass
        except OSError:
            pass

    def _skill_path(self) -> Path:
        return (
            Path(self.config.codex_home).resolve()
            / "skills"
            / self.config.skill_name
            / "SKILL.md"
        )

    def _prompt(
        self,
        *,
        person_name: str | None,
        references: list[tuple[str, str]],
        user_prompt: str,
    ) -> str:
        reference_lines = "\n".join(
            f"- {role}: {filename}" for role, filename in references
        )
        extra = user_prompt.strip()
        extra_block = f"\nAdditional generation instruction:\n{extra}\n" if extra else ""
        if self.config.output_contract == "independent_concepts_v4":
            return (
                "Use $" + self.config.skill_name + " and $imagegen.\n"
                "Follow this Skill's independent-concept pipeline, NOT a single-cap generator.\n"
                "Work only in the current job directory. Keep all source stock and palette files unchanged.\n"
                "Run the Skill's inspect_assets.py preflight. Initialize an EMPTY ./v4-job/ "
                "with independent_pipeline.py init --job-dir ./v4-job and the exact quote.\n"
                "Generate TEN separate, transparent embroidery artwork images, one per tool call.\n"
                "Visually inspect each image and never falsely claim human sign-off.\n"
                "Use the Skill's versioned pipeline and deterministic assembler; do NOT generate "
                "a 10-up or 13-colorways sheet with an image model.\n"
                "Run independent_pipeline.py build --job-dir ./v4-job --out-dir ./output. "
                "Create both distinct final PNG boards in ./output/v001/ (or next version), "
                "and ./output/latest.json with audit.json in the version folder and ./v4-job/job_state.json.\n"
                "The final deliverables MUST contain 10 distinct concepts with a Hero, "
                "and the identical Hero on all 13 ORIGINAL Valucap stock colorways.\n"
                "Do not create output/final.png as a replacement for the two boards.\n"
                "If the full workflow cannot be completed, report the blocker instead of "
                "returning a fake success image.\n"
                "Do not use an API-key-backed image generation fallback.\n"
                "Keep assistant text brief: only report milestones and final file paths. "
                "Write all intermediate artwork, checklists, and diagnostics to workspace files. "
                "Never print image data, whole checklists, or repeated concept descriptions to stdout.\n"
                + extra_block
            )
        return (
            "Use $" + self.config.skill_name + " and $imagegen.\n\n"
            + (f"Edit target:\n{person_name}\n\n" if person_name else "Create a new original image from the keyword instruction.\n\n")
            + f"Role-labeled references:\n{reference_lines}\n"
            + f"{extra_block}\n"
            + "Generate exactly one final image. "
            + "Save it to output/final.png. "
            + "Do not use an API-key-backed image generation fallback. "
            + "Do not run extra Python/PIL validation commands; the caller validates the PNG after Codex exits."
        )
