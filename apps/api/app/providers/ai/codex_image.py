from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import os
import re
import signal
import shutil
from dataclasses import dataclass
from pathlib import Path

from PIL import Image

from app.providers.ai.codex_execution_log import CodexExecutionLog, stream_codex_process, prune_codex_execution_logs
from app.modules.image_generation.providers import (
    GeneratedImageFile,
    GeneratedImageResult,
    PreparedImage,
    ReferenceImageInput,
)

_cleanup_logger = logging.getLogger(__name__)

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
    expected_output_count: int = 6
    expected_quote: str | None = None
    execution_log_id: str | None = None
    font_plan: tuple[dict, ...] | None = None


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


def _isolated_job_codex_home(source_home: Path, workspace: Path) -> Path:
    """Make imagegen cache job-owned while reusing account login and skills.

    Never copy auth tokens into the staging area. Only explicit read-only
    configuration/credential/skill links are shared with the account home.
    Codex writes generated_images and temporary state under this private
    job directory, which is independently cleaned on job completion.
    """
    root = workspace.resolve()
    if workspace.is_symlink() or not workspace.is_dir():
        raise CodexImageProviderError(
            "codex_job_home_invalid", "Image job workspace is unsafe."
        )
    home = workspace / ".codex-runtime"
    if home.is_symlink() or (home.exists() and not home.is_dir()):
        raise CodexImageProviderError(
            "codex_job_home_invalid", "Isolated Codex runtime is unsafe."
        )
    home.mkdir(mode=0o700, exist_ok=True)
    home.chmod(0o700)
    if not home.resolve().is_relative_to(root):
        raise CodexImageProviderError(
            "codex_job_home_invalid", "Isolated Codex runtime escaped its workspace."
        )
    for name in ("auth.json", "config.toml", "managed_config.toml",
                 "skills", "rules", "prompts", "AGENTS.md", "plugins"):
        source = source_home / name
        dest = home / name
        if dest.is_symlink():
            if dest.resolve() != source.resolve() or not source.exists():
                raise CodexImageProviderError(
                    "codex_job_home_invalid", "Isolated Codex credential link changed."
                )
            continue
        if dest.exists():
            raise CodexImageProviderError(
                "codex_job_home_invalid", "Isolated Codex runtime contains an unexpected shared path."
            )
        if source.exists():
            dest.symlink_to(source, target_is_directory=source.is_dir())
    # Imagegen PNGs must NOT land under source_home/generated_images.
    (home / "generated_images").mkdir(mode=0o700, exist_ok=True)
    return home


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


def _classify_stage1_no_images(stdout: str, stderr: str) -> CodexImageProviderError:
    """Explain a zero-PNG Codex completion without claiming the tool succeeded.

    The CLI can exit 0 after an assistant-only response or a failed turn.
    Inspect bounded JSONL metadata and known error signals, never expose
    model messages or stderr as part of the UI error.
    """
    import re

    events = []
    for line in stdout.splitlines():
        try:
            row = json.loads(line)
        except (TypeError, ValueError):
            continue
        if isinstance(row, dict):
            events.append(row)

    failed_turn = any(str(event.get("type") or "") in {"turn.failed", "error"} for event in events)
    tool_events = 0
    image_calls = 0
    messages = []
    for event in events:
        item = event.get("item")
        if not isinstance(item, dict):
            continue
        event_type = str(event.get("type") or "")
        item_type = str(item.get("type") or "").lower()
        if event_type in {"item.started", "item.completed", "item.updated"}:
            if item_type in {"tool_call", "mcp_tool_call", "dynamic_tool_call", "image_generation"}:
                tool_events += 1
                label = str(item.get("tool_name") or item.get("name") or item.get("server") or "")
                if "imagegen" in label.lower() or "image_generation" in label.lower():
                    image_calls += 1
        if item_type == "agent_message" and event_type == "item.completed":
            message = item.get("text")
            if isinstance(message, str):
                messages.append(message[:2500])
    combined = (stderr[:6000] + "\n" + "\n".join(messages)).casefold()
    missing_image_tool = (
        bool(re.search(r"(?:imagegen|image.gen|image generation)", combined))
        and any(term in combined for term in (
            "unavailable", "not available", "not found", "unknown tool", "not installed",
            "disabled", "not supported", "cannot invoke", "can't invoke",
            "no access to", "cannot access",
        ))
    )
    if missing_image_tool:
        return CodexImageProviderError(
            "stage1_imagegen_unavailable",
            "Codex could not access the image-generation tool. No PNGs were created. "
            "Check tool/Skill availability in the job logs before manually generating again.",
        )
    if any(term in combined for term in (
        "usage limit", "rate limit", "quota exceeded", "too many requests", "limit reached",
    )):
        return CodexImageProviderError(
            "stage1_imagegen_limited",
            "Image-generation capacity was limited. No PNGs were created. "
            "When no image was created, Stage 1 can retry this error automatically within its recovery budget.",
        )
    if failed_turn:
        return CodexImageProviderError(
            "stage1_codex_turn_failed",
            "Codex reported a failed turn despite the CLI exiting normally. "
            "No PNGs were created. Review the job's Skill log before starting a new run.",
        )
    if not tool_events:
        return CodexImageProviderError(
            "stage1_imagegen_not_invoked",
            "Codex finished without running a generation tool and produced no PNGs. "
            "Check Skill execution and image-generation tool access. "
            "Stage 1 can retry this zero-image result automatically within its recovery budget.",
        )
    if image_calls:
        return CodexImageProviderError(
            "stage1_imagegen_no_output",
            "Codex invoked image generation but did not write any PNG finals. "
            "Review the tool log and output-path settings before manually generating again.",
        )
    return CodexImageProviderError(
        "stage1_no_generated_images",
        "The Skill finished without creating any PNG artwork. "
        "The available execution metadata cannot confirm that image generation ran. "
        "Check Skill logs; a new generation requires manual confirmation.",
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

    @staticmethod
    def stage1_missing_final_numbers(workspace: Path, expected_count: int = 6) -> tuple[int, ...] | None:
        """Inspect checkpoint candidates without trusting partial/unknown PNG sets.

        None denotes an unsafe workspace, six numbers denotes no usable
        checkpoint, () denotes all six complete.
        """
        if not 1 <= expected_count <= 24:
            raise ValueError("Expected final image count must be between 1 and 24.")
        root = workspace.resolve()
        finals = workspace / "output" / "final"
        if finals.is_symlink() or not finals.is_dir():
            return tuple(range(1, expected_count + 1))
        expected = {f"design_{number:02}.png": number for number in range(1, expected_count + 1)}
        digests: set[str] = set()
        found: set[int] = set()
        for path in finals.iterdir():
            if not path.is_file() or path.suffix.lower() != ".png":
                continue
            if path.is_symlink() or path.name not in expected or not path.resolve().is_relative_to(root):
                return None
            try:
                with Image.open(path) as image:
                    image.verify()
                    if image.format != "PNG" or min(image.size) < 512:
                        return None
                digest = hashlib.sha256(path.read_bytes()).hexdigest()
                if digest in digests:
                    return None
                digests.add(digest)
            except (OSError, ValueError, Image.DecompressionBombError):
                return None
            found.add(expected[path.name])
        return tuple(sorted(set(range(1, expected_count + 1)) - found))

    async def generate_from_references(
        self,
        *,
        attempt_id: str,
        person: PreparedImage | None,
        references: list[ReferenceImageInput],
        prompt: str,
        resume_missing_numbers: tuple[int, ...] | None = None,
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
        if resume_missing_numbers is None:
            if workspace.exists():
                shutil.rmtree(workspace)
            output_dir.mkdir(parents=True, exist_ok=True)
        else:
            if (workspace.is_symlink() or not output_dir.is_dir()
                    or self.config.output_contract != "stage1_six_final_designs"
                    or not resume_missing_numbers
                    or self.stage1_missing_final_numbers(workspace, self.config.expected_output_count) != tuple(sorted(resume_missing_numbers))):
                raise CodexImageProviderError(
                    "stage1_resume_checkpoint_invalid",
                    "Stage 1 recovery checkpoint is missing or has changed; original PNGs were not overwritten.",
                )
        checkpoint_dir = workspace / ".stage1-preserved-finals"
        checkpoint_hashes: dict[int, str] = {}
        if resume_missing_numbers is not None:
            if checkpoint_dir.exists():
                shutil.rmtree(checkpoint_dir)
            checkpoint_dir.mkdir()
            for number in sorted(set(range(1, self.config.expected_output_count + 1)) - set(resume_missing_numbers)):
                filename = f"design_{number:02}.png"
                current = output_dir / "final" / filename
                checkpoint_hashes[number] = hashlib.sha256(current.read_bytes()).hexdigest()
                shutil.copy2(current, checkpoint_dir / filename)

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

        if (self.config.output_contract == "stage1_six_final_designs"
                and self.config.skill_name == "gatorhats-stage1-six-designs"
                and self.config.expected_quote):
            from app.modules.realistic_review_ugc.stage1_font_library import render_font_guides
            render_font_guides(workspace, self.config.expected_quote,
                               font_plan=self.config.font_plan)

        if resume_missing_numbers is None:
            instruction = self._prompt(
                person_name=person_name,
                references=reference_names,
                user_prompt=prompt,
            )
        else:
            missing_text = ", ".join(f"design_{n:02}.png" for n in resume_missing_numbers)
            existing_text = ", ".join(f"design_{n:02}.png" for n in sorted(checkpoint_hashes))
            instruction = (
                f"Use ${self.config.skill_name} and $imagegen.\n"
                "Stage 1 CONTINUATION of an existing six-design job, NOT a fresh six-design run.\n"
                "Exactly six distinct artworks are required ACROSS ALL ATTEMPTS; "
                "some already exist as valid final PNGs. Do not regenerate, rename, "
                "delete, rewrite or recolor any preserved final.\n"
                f"Existing complete finals (read-only): {existing_text}.\n"
                f"Generate ONLY the {len(resume_missing_numbers)} missing designs: {missing_text}. "
                "Make ONE imagegen call for each missing design only. "
                "Keep the existing designs distinct from these new concepts. "
                "Save only those exact missing filenames under output/final/. "
                "Do not create or retain preview, draft or QA image files.\n"
                f"Original Stage 1 artwork request (quote is literal input): {prompt}\n"
                f"FINAL INSTRUCTION: continue with ONLY {missing_text}. "
                "Do not repeat all six, do not output an image sheet, and do not use an API-key fallback."
            )
        # Use a separate CODEX_HOME for every job. A shared account HOME
        # preserves authentication/skills, while imagegen cache is private.
        execution_home = _isolated_job_codex_home(codex_home, workspace)
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
                env=_codex_env(execution_home),
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
        finally:
            # The isolated imagegen cache contains review/draft images, not
            # final deliverables. Remove it as soon as the Codex CLI exits,
            # including failed/timed-out attempts. Keep output/final/ for
            # checkpointed Drive retries. Never touch the account HOME.
            if "process" not in locals() or process.returncode is not None:
                transient = execution_home / "generated_images"
                try:
                    if not transient.is_symlink() and transient.is_dir():
                        shutil.rmtree(transient)
                except OSError as exc:
                    _cleanup_logger.warning(
                        "codex_job_runtime_cache_cleanup_failed error_type=%s",
                        type(exc).__name__,
                    )
        if checkpoint_hashes:
            preserved_changed = False
            for number, digest in checkpoint_hashes.items():
                filename = f"design_{number:02}.png"
                target = output_dir / "final" / filename
                checkpoint = checkpoint_dir / filename
                try:
                    current_hash = hashlib.sha256(target.read_bytes()).hexdigest() if target.is_file() else None
                    if current_hash != digest:
                        if not checkpoint.is_file() or hashlib.sha256(checkpoint.read_bytes()).hexdigest() != digest:
                            raise CodexImageProviderError(
                                "stage1_resume_checkpoint_corrupt",
                                "A preserved design checkpoint was corrupted; automatic recovery stopped.",
                            )
                        shutil.copy2(checkpoint, target)
                        preserved_changed = True
                except (OSError, ValueError) as exc:
                    raise CodexImageProviderError(
                        "stage1_resume_checkpoint_invalid",
                        "Could not verify preserved Stage 1 PNGs.",
                    ) from exc
            if preserved_changed:
                raise CodexImageProviderError(
                    "stage1_resume_modified_existing",
                    "The Skill tried to modify an existing design; the saved checkpoint was restored.",
                )
        if process.returncode != 0:
            raise _classify_failure(stderr, stdout)

        files: tuple[GeneratedImageFile, ...] = ()
        if self.config.output_contract == "stage1_six_final_designs":
            # Rarely Codex finishes while image files are still being flushed
            # by a child tool. Wait only when *zero* files are present; never
            # rerun the image Skill and never hide an incomplete 1–5 result.
            for wait_seconds in (0, 2, 3):
                if wait_seconds:
                    await asyncio.sleep(wait_seconds)
                try:
                    files = self._collect_six_final_designs(workspace, self.config.expected_output_count)
                    break
                except CodexImageProviderError as exc:
                    if exc.code != "stage1_no_generated_images":
                        raise
                    if wait_seconds == 3:
                        raise _classify_stage1_no_images(stdout, stderr) from exc
            image_bytes = Path(files[0].path).read_bytes()
            extra_images = ()
        elif self.config.output_contract == "all_generated_images":
            files = self._collect_generated_images(workspace)
            if not files:
                raise CodexImageProviderError(
                    "codex_image_output_missing",
                    "Skill returned no generated image files.",
                    retryable=True,
                )
            image_bytes = Path(files[0].path).read_bytes()
            extra_images = ()
        elif self.config.output_contract == "independent_concepts_v4":
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
            output_files=files if self.config.output_contract in ("all_generated_images", "stage1_six_final_designs") else (),
        )

    @staticmethod
    def _collect_six_final_designs(workspace: Path, expected_count: int = 6) -> tuple[GeneratedImageFile, ...]:
        """Read *only* six named final files; never import temporary images.

        Fail closed on missing files, accidental extras or duplicates. Do not
        mark a 100-image job complete by merely taking its first six files.
        """
        if not 1 <= expected_count <= 24:
            raise ValueError("Expected final image count must be between 1 and 24.")
        final_dir = workspace / "output" / "final"
        if final_dir.is_symlink() or (final_dir.exists() and not final_dir.is_dir()):
            raise CodexImageProviderError(
                "stage1_six_outputs_invalid", "Invalid output/final directory."
            )
        # A Skill may place all six correctly numbered PNGs directly in
        # output/. Normalize only when a complete, unambiguous set exists.
        final_dir.mkdir(parents=True, exist_ok=True)
        expected = [f"design_{number:02}.png" for number in range(1, expected_count + 1)]
        # Codex can write harmless README/manifest sidecars in final/, or
        # save the six numbered PNGs under output/ instead. These must not
        # turn a successfully generated set into a failed job.
        # NEVER truncate more than six PNGs to six or silently use drafts.
        output_root = workspace / "output"
        direct_final_pngs = [
            path for path in final_dir.iterdir()
            if path.is_file() and path.suffix.lower() == ".png"
        ]
        canonical_complete = (
            len(direct_final_pngs) == expected_count
            and all((final_dir / name).is_file() for name in expected)
        )
        if canonical_complete:
            # Other PNGs under output/artworks are intermediate drafts and
            # are not part of the final contract.
            candidates = [final_dir / name for name in expected]
        else:
            candidates = [
                path for path in output_root.rglob("*")
                if path.is_file() and path.suffix.lower() == ".png"
                and not path.is_symlink()
                and path.resolve().is_relative_to(output_root.resolve())
                and not any(parent.is_symlink() for parent in path.parents if parent != workspace)
            ]
            if not candidates:
                # Some CLI runs save six *named final* artworks in generated/
                # or results/ instead of output/. Recover only a complete,
                # unequivocally numbered six-PNG set from these dedicated
                # output directories; never pull from working/previews.
                for directory in (workspace / "generated", workspace / "results"):
                    if not directory.is_dir() or directory.is_symlink():
                        continue
                    candidates.extend(
                        path for path in directory.rglob("*")
                        if path.is_file() and path.suffix.lower() == ".png"
                        and not path.is_symlink()
                        and path.resolve().is_relative_to(workspace.resolve())
                        and not any(parent.is_symlink() for parent in path.parents if parent != workspace)
                    )
        number_pattern = re.compile(r"(?i)^(?:design|final|artwork|concept)[_. -]*0?([0-9]{1,2})$")
        numbered: dict[int, Path] = {}
        unmatched = []
        for path in candidates:
            match = number_pattern.fullmatch(path.stem)
            if match is None:
                unmatched.append(path)
                continue
            number = int(match.group(1))
            if not 1 <= number <= expected_count or number in numbered:
                unmatched.append(path)
                continue
            numbered[number] = path
        if len(candidates) == expected_count and not unmatched and len(numbered) == expected_count:
            # Copy instead of moving so temp diagnostic previews can still be
            # collected, and rename only within this isolated attempt.
            from shutil import copyfile
            for number, path in sorted(numbered.items()):
                target = final_dir / f"design_{number:02}.png"
                if path != target:
                    if target.exists():
                        raise CodexImageProviderError(
                            "stage1_six_outputs_invalid", "Conflicting final design filenames."
                        )
                    copyfile(path, target)
        else:
            if not candidates:
                raise CodexImageProviderError(
                    "stage1_no_generated_images",
                    "The Skill finished without creating any PNG artwork. "
                    "Image generation may not have started or a tool may have failed. "
                    "No outputs were discarded. Check this job's Skill logs; "
                    "a new generation requires manual confirmation.",
                )
            missing = sorted(set(range(1, expected_count + 1)) - set(numbered))
            raise CodexImageProviderError(
                "stage1_six_outputs_invalid",
                f"Stage 1 returned {len(candidates)} PNG candidates, "
                f"{len(numbered)}/{expected_count} uniquely numbered finals; "
                f"missing design numbers {','.join(map(str, missing)) or 'none'}. "
                "No outputs were discarded or counted as completed.",
            )
        workspace_root = workspace.resolve()
        found = []
        digests = set()
        for name in expected:
            path = final_dir / name
            if path.is_symlink() or not path.is_file() or not path.resolve().is_relative_to(workspace_root):
                raise CodexImageProviderError("stage1_six_outputs_invalid", "Invalid final design file.")
            try:
                with Image.open(path) as image:
                    image.verify()
                    if image.format != "PNG" or min(image.size) < 512:
                        raise ValueError("Final designs must be PNG with both dimensions at least 512.")
            except (OSError, ValueError, Image.DecompressionBombError) as exc:
                raise CodexImageProviderError(
                    "stage1_six_outputs_invalid", "Stage 1 returned an invalid final PNG."
                ) from exc
            digest = hashlib.sha256(path.read_bytes()).hexdigest()
            if digest in digests:
                raise CodexImageProviderError(
                    "stage1_six_outputs_duplicate", "Stage 1 returned duplicate final images."
                )
            digests.add(digest)
            found.append(GeneratedImageFile(
                path=str(path.resolve()), filename=f"output/final/{name}", mime_type="image/png",
            ))
        return tuple(found)

    @staticmethod
    def _collect_generated_images(workspace: Path) -> tuple[GeneratedImageFile, ...]:
        """Collect ONLY declared final deliverables; never upload review images.

        Intermediate artwork, QA snapshots, draft concept variants and the
        image tool's generated_images cache are not a final output contract.
        Finals are explicitly under output/final/, output/final.png, or the
        two named v4 completed boards in a versioned output/ folder.
        Missing final files must fail closed instead of uploading previews.
        """
        output = workspace / "output"
        root = workspace.resolve()
        if not output.is_dir() or output.is_symlink():
            return ()
        found: dict[Path, GeneratedImageFile] = {}
        formats = {".png": "image/png", ".jpg": "image/jpeg",
                   ".jpeg": "image/jpeg", ".webp": "image/webp"}
        for base, dirs, names in os.walk(output, followlinks=False):
            dirs[:] = [name for name in dirs if not name.startswith(".")
                       and not (Path(base) / name).is_symlink()]
            for name in names:
                path = Path(base) / name
                mime = formats.get(path.suffix.lower())
                if not mime or path.is_symlink() or not path.is_file():
                    continue
                relative = path.relative_to(workspace)
                parts = relative.parts
                stem = path.stem.lower()
                final = (
                    (len(parts) >= 3 and parts[:2] == ("output", "final"))
                    or parts == ("output", "final.png")
                    or (len(parts) == 3
                        and parts[0] == "output"
                        and re.fullmatch(r"v[0-9]{3,}", parts[1])
                        and (stem.endswith("_01_design_concepts")
                             or stem.endswith("_02_13_colorways")))
                )
                if not final:
                    continue
                # Even under output/final/, explicit draft/review/QA labels
                # cannot be mistaken for approved deliverables.
                if any(re.search(r"(?:^|[_-])(preview|draft|review|qa|temp)(?:[_-]|$)",
                                 part.lower()) for part in parts[2:] if parts[:2] == ("output", "final")):
                    continue
                resolved = path.resolve()
                if not resolved.is_relative_to(root):
                    continue
                try:
                    with Image.open(path) as image:
                        image.verify()
                        actual_mime = _ALLOWED_OUTPUT_MIME.get(image.format or "")
                    if actual_mime != mime:
                        continue
                except (OSError, ValueError, Image.DecompressionBombError):
                    continue
                found[resolved] = GeneratedImageFile(
                    path=str(resolved), filename=relative.as_posix(), mime_type=mime,
                )

        def priority(item: GeneratedImageFile) -> tuple[int, str]:
            name = item.filename.lower()
            if name.endswith("/final.png") or name == "output/final.png":
                return (0, name)
            return (1, name) if name.endswith(("_01_design_concepts.png",
                                              "_02_13_colorways.png")) else (2, name)
        return tuple(sorted(found.values(), key=priority))

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

    def cleanup_previews(self, attempt_id: str) -> None:
        """Remove transient review images and private Codex imagegen cache.

        Preserve output/final checkpoints during Drive failure or recovery.
        Never touch the account-level auth HOME or another job workspace.
        """
        if not attempt_id or attempt_id in (".", "..") or "/" in attempt_id or "\\" in attempt_id:
            return
        workspace = Path(self.config.staging_root).resolve() / "codex" / attempt_id
        working = workspace / "working"
        if workspace.is_symlink() or working.is_symlink():
            return
        previews = working / "previews"
        runtime_cache = workspace / ".codex-runtime" / "generated_images"
        for transient in (previews, runtime_cache):
            try:
                if transient.is_symlink():
                    # Remove the link itself; never traverse its external
                    # target. The preview link must not persist across retry.
                    transient.unlink()
                    continue
                if transient.is_dir() and transient.resolve().is_relative_to(workspace.resolve()):
                    shutil.rmtree(transient)
            except (OSError, ValueError) as exc:
                _cleanup_logger.warning(
                    "codex_job_preview_cleanup_failed error_type=%s", type(exc).__name__,
                )

    def cleanup_attempt(self, attempt_id: str) -> None:
        """Only remove a direct job workspace; never follow links or parent traversal."""
        if (not attempt_id or attempt_id in (".", "..")
                or "/" in attempt_id or "\\" in attempt_id):
            _cleanup_logger.warning("codex_job_cleanup_skipped invalid_attempt_id")
            return
        codex_root = Path(self.config.staging_root).resolve() / "codex"
        workspace = codex_root / attempt_id
        if codex_root.is_symlink() or workspace.is_symlink():
            _cleanup_logger.warning("codex_job_cleanup_skipped symlink_workspace")
            return
        try:
            shutil.rmtree(workspace)
        except FileNotFoundError:
            pass
        except OSError as exc:
            _cleanup_logger.warning(
                "codex_job_workspace_cleanup_failed error_type=%s", type(exc).__name__,
            )

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
        if self.config.output_contract == "stage1_six_final_designs":
            n = self.config.expected_output_count
            if not 1 <= n <= 24:
                raise ValueError("Expected final image count must be between 1 and 24.")
            return (
                "Use $" + self.config.skill_name + " and $imagegen.\n"
                f"This is Stage 1: {n} independent embroidery design concepts only.\n"
                f"Generate exactly {n} final artworks, one per image-generation call; no trial "
                "image generations, 10-concept boards, Hero selection, 13 hats, mockups or colorways.\n"
                "Keep the quote's exact text and make each concept truly distinct.\n"
                f"Save exactly {n} PNG files: output/final/design_01.png through design_{n:02}.png.\n"
                "Do not generate, write, upload, or retain any preview/draft files, "
                f"including working/previews/. Persist only the {n} numbered final PNGs.\n"
                "Do not use an API-key-backed image generation fallback.\n"
                "Invoke the actual $imagegen image-generation tool once for EACH "
                "design, then ensure the corresponding numbered PNG exists and "
                "is nonempty BEFORE proceeding to the next design.\n"
                "Do not treat a text response, a preflight command, or a "
                "successful CLI exit as proof that any image was generated.\n"
                "If $imagegen is missing, unavailable, or cannot generate, "
                "report that exact blocker instead of ending as if finished.\n"
                "Use one generated image per final file. Do not print image data.\n"
                + extra_block
            )
        if self.config.skill_name == "hanh-redesign-8869-ver-4" and self.config.output_contract in ("independent_concepts_v4", "all_generated_images"):
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
                "Use intermediate concepts, mockups and colorway renders only inside this "
                "isolated job workspace while assembling final deliverables. "
                "Do not upload/store review, preview, draft or QA imagery. "
                "Persist ONLY the two finished boards from ./output/vNNN/ "
                "as official final outputs. Do not treat intermediates as final.\n"
                "If the full workflow cannot be completed, report the blocker instead of "
                "returning a fake success image.\n"
                "Do not use an API-key-backed image generation fallback.\n"
                "Keep assistant text brief: only report milestones and final file paths. "
                "Write all intermediate artwork, checklists, and diagnostics to workspace files. "
                "Never print image data, whole checklists, or repeated concept descriptions to stdout.\n"
                + extra_block
            )
        output_instruction = (
            "Only completed final deliverables may be saved to ./output/final/. "
            "Do NOT put review images, previews, QA screenshots, work-in-progress "
            "or temporary alternatives in the final folder. Keep drafts temporary "
            "inside this job and do not upload or retain them as outputs. "
            "If there are no validated finals, fail rather than returning drafts.\n"
            if self.config.output_contract == "all_generated_images"
            else "Generate exactly one final image. Save it to output/final.png. "
        )
        return (
            "Use $" + self.config.skill_name + " and $imagegen.\n\n"
            + (f"Edit target:\n{person_name}\n\n" if person_name else "Create a new original image from the keyword instruction.\n\n")
            + f"Role-labeled references:\n{reference_lines}\n"
            + f"{extra_block}\n"
            + output_instruction
            + "Do not use an API-key-backed image generation fallback. "
            + "Do not run extra Python/PIL validation commands; the caller validates the PNG after Codex exits."
        )