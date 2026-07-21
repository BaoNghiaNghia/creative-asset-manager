from __future__ import annotations

import os
from pathlib import Path

from dotenv import dotenv_values, load_dotenv

API_ENV_FILE = Path(__file__).resolve().parents[2] / ".env"
_PRODUCTION_ENVIRONMENTS = {"prod", "production"}


def load_development_environment(env_file: Path = API_ENV_FILE) -> bool:
    """Load only the fixed API .env file, and never its values in production."""
    process_environment = os.getenv("APP_ENV")
    file_environment: str | None = None
    if process_environment is None and env_file.is_file():
        file_environment = dotenv_values(env_file).get("APP_ENV")
    declared_environment = process_environment or file_environment or "development"
    if declared_environment.strip().lower() in _PRODUCTION_ENVIRONMENTS:
        if process_environment is None:
            os.environ["APP_ENV"] = declared_environment
        return False
    if not env_file.is_file():
        return False
    return bool(load_dotenv(dotenv_path=env_file, override=False))
