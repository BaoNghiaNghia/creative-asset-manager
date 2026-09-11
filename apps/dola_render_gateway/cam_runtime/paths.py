from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path


class RuntimePathError(ValueError):
    pass


def absolute_directory(raw: str, name: str) -> Path:
    value = str(raw or "").strip()
    if not value:
        raise RuntimePathError(f"{name} must not be empty.")
    candidate = Path(value).expanduser()
    if not candidate.is_absolute():
        raise RuntimePathError(f"{name} must be an absolute path.")
    return candidate.resolve(strict=False)


def is_within(path: Path, parent: Path) -> bool:
    try:
        path.relative_to(parent)
    except ValueError:
        return False
    return True


@dataclass(frozen=True)
class RuntimePaths:
    source_root: Path
    state_root: Path
    runtime_root: Path
    db_dir: Path
    tasks_db: Path
    pool_usage_db: Path
    accounts_dir: Path
    profiles_dir: Path
    downloads_dir: Path
    artifacts_dir: Path
    tmp_dir: Path

    @classmethod
    def from_roots(cls, *, source_root: Path, state_root: str, runtime_root: str) -> "RuntimePaths":
        source = source_root.expanduser().resolve(strict=False)
        state = absolute_directory(state_root, "DOLA_STATE_ROOT")
        runtime = absolute_directory(runtime_root, "DOLA_RUNTIME_ROOT")
        paths = cls(
            source_root=source,
            state_root=state,
            runtime_root=runtime,
            db_dir=state / "db",
            tasks_db=state / "db" / "tasks.db",
            pool_usage_db=state / "db" / "pool_usage.db",
            accounts_dir=state / "accounts",
            profiles_dir=state / "profiles",
            downloads_dir=state / "downloads",
            artifacts_dir=state / "artifacts",
            tmp_dir=runtime / "tmp",
        )
        for persistent in (
            paths.db_dir, paths.tasks_db, paths.pool_usage_db, paths.accounts_dir,
            paths.profiles_dir, paths.downloads_dir, paths.artifacts_dir,
        ):
            if is_within(persistent, source):
                raise RuntimePathError("Persistent Dola state must not resolve under the source checkout.")
        return paths

    def initialize(self) -> None:
        for directory in (
            self.state_root, self.runtime_root, self.db_dir, self.accounts_dir,
            self.profiles_dir, self.downloads_dir, self.artifacts_dir, self.tmp_dir,
        ):
            directory.mkdir(parents=True, exist_ok=True)
