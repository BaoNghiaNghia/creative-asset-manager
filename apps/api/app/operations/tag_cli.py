from __future__ import annotations

import argparse
import json
from collections.abc import Callable

from sqlalchemy.orm import Session

from app.core.database import (
    SessionLocal,
    validate_alembic_head,
    validate_database_connection,
)
from app.modules.tag.repository import SYSTEM_TAGS, TagRepository


def seed_system_tags(
    session_factory: Callable[[], Session] = SessionLocal,
) -> dict[str, object]:
    with session_factory.begin() as session:
        TagRepository(session).seed_system_tags()
    return {
        "status": "ok",
        "ensured": len(SYSTEM_TAGS),
        "tag_ids": [values["id"] for values in SYSTEM_TAGS],
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="System tag operations")
    commands = parser.add_subparsers(dest="command", required=True)
    commands.add_parser(
        "seed-system-tags",
        help="Idempotently create or update the built-in system tags",
    )
    args = parser.parse_args(argv)

    if args.command == "seed-system-tags":
        validate_database_connection()
        validate_alembic_head()
        result = seed_system_tags()
    else:  # pragma: no cover - argparse enforces the command set.
        parser.error("unsupported command")

    print(json.dumps(result, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
