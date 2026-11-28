"""Read-only evidence for the central Image V6.1 production gate.

A code release and a compatible schema are necessary, not sufficient, for
execution. Never infer adapter availability or restore proof from these fields.
"""
from pathlib import Path
import re

from sqlalchemy import text
from sqlalchemy.exc import SQLAlchemyError

from . import model
from .qa_submission import ImageQARecordSubmission
from app.modules.render_resources.model import RenderResource

EXPECTED_REVISION = "0221_cam_image_qa_records"
TABLES = (
    model.ImageFlowControl, model.ImageBatch, model.ImagePack, model.ImageRole,
    model.ImageCommand, model.ImageCapabilityState, model.ImageUploadIntent,
    model.ImagePlanApproval, model.ImageQAApproval, ImageQARecordSubmission,
    RenderResource,
)
REQUIRED_COLUMNS = {
    cls.__table__.name: frozenset(col.name for col in cls.__table__.columns)
    for cls in TABLES
}


def release_commit():
    # Resolve from the running code, not the mutable current symlink.
    try:
        value = (Path(__file__).resolve().parents[5] / ".cam-release").read_text(
            encoding="ascii").strip()
    except (OSError, UnicodeError):
        return None
    return value if re.fullmatch(r"[a-f0-9]{40}", value) else None


def schema_evidence(db):
    result = {
        "releaseCommit": release_commit(),
        "schemaExpectedRevision": EXPECTED_REVISION,
        "migrationRevision": None,
        "centralSchemaReady": False,
        "releasePhase": "CENTRAL_SCHEMA_UNVERIFIED",
        "imageSchemaTablesVerified": 0,
    }
    # Production is PostgreSQL. SQLite fixtures must not masquerade as live.
    if db.get_bind().dialect.name != "postgresql":
        return result
    try:
        with db.begin_nested():
            versions = list(db.scalars(text("SELECT version_num FROM alembic_version")))
            if len(versions) != 1:
                return result
            result["migrationRevision"] = versions[0]
            rows = db.execute(text(
                "SELECT table_name, column_name FROM information_schema.columns "
                "WHERE table_schema = current_schema() AND "
                "(table_name LIKE 'cam_image_%' OR table_name = 'cam_render_resources')"
            )).all()
        found = {}
        for table, column in rows:
            found.setdefault(table, set()).add(column)
        count = sum(columns <= found.get(table, set())
                    for table, columns in REQUIRED_COLUMNS.items())
        result["imageSchemaTablesVerified"] = count
        if versions[0] == EXPECTED_REVISION and count == len(REQUIRED_COLUMNS):
            result.update(centralSchemaReady=True,
                          releasePhase="CENTRAL_SCHEMA_READY_DISABLED")
    except SQLAlchemyError:
        # Do not expose SQL, credentials or provider exceptions in readiness.
        pass
    return result
