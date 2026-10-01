from __future__ import annotations

import asyncio
import json
import os
import re
import shutil
from dataclasses import dataclass
from pathlib import Path

from PIL import Image

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
        person: PreparedImage,
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

        person_name = "person" + _extension(person.mime_type)
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
            "--sandbox",
            "workspace-write",
            "--approve-for-me",
            "-C",
            str(workspace),
        ]
        if self.config.model:
            argv.extend(["--model", self.config.model])
        argv.append(instruction)

        try:
            process = await asyncio.create_subprocess_exec(
                *argv,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                env=_codex_env(codex_home),
            )
            stdout_bytes, stderr_bytes = await asyncio.wait_for(
                process.communicate(),
                timeout=max(30, int(self.config.timeout_seconds)),
            )
        except asyncio.TimeoutError as exc:
            if "process" in locals() and process.returncode is None:
                process.kill()
                await process.communicate()
            raise CodexImageProviderError(
                "codex_image_timeout",
                "Codex image generation exceeded the configured timeout.",
                retryable=True,
            ) from exc
        except OSError as exc:
            raise CodexImageProviderError(
                "codex_image_cli_unavailable",
                "Codex CLI could not be started on this worker.",
                retryable=True,
            ) from exc

        stdout = stdout_bytes.decode("utf-8", errors="replace")
        stderr = stderr_bytes.decode("utf-8", errors="replace")
        if process.returncode != 0:
            raise _classify_failure(stderr, stdout)

        output_path = output_dir / "final.png"
        if not output_path.is_file():
            raise CodexImageProviderError(
                "codex_image_output_missing",
                "Codex completed without creating output/final.png.",
                retryable=True,
            )
        image_bytes = output_path.read_bytes()
        try:
            with Image.open(output_path) as image:
                image.load()
                mime = _ALLOWED_OUTPUT_MIME.get(image.format or "")
        except Exception as exc:
            raise CodexImageProviderError(
                "codex_image_output_invalid",
                "Codex output is not a valid raster image.",
                retryable=True,
            ) from exc
        if mime != "image/png":
            raise CodexImageProviderError(
                "codex_image_output_invalid",
                "Codex output/final.png is not a PNG image.",
                retryable=True,
            )

        return GeneratedImageResult(
            provider="codex",
            model=self.config.model,
            image_bytes=image_bytes,
            mime_type="image/png",
            provider_request_id=_request_id_from_jsonl(stdout),
            provider_metadata={"skill": self.config.skill_name},
        )

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
        person_name: str,
        references: list[tuple[str, str]],
        user_prompt: str,
    ) -> str:
        reference_lines = "\n".join(
            f"- {role}: {filename}" for role, filename in references
        )
        extra = user_prompt.strip()
        extra_block = f"\nAdditional generation instruction:\n{extra}\n" if extra else ""
        return (
            "Use $" + self.config.skill_name + " and $imagegen.\n\n"
            f"Edit target:\n{person_name}\n\n"
            f"Role-labeled references:\n{reference_lines}\n"
            f"{extra_block}\n"
            "Generate exactly one final image. "
            "Save it to output/final.png. "
            "Do not use an API-key-backed image generation fallback. "
            "Before finishing, verify output/final.png exists and is a valid PNG."
        )
