from datetime import datetime, timezone
from hashlib import sha256
from uuid import uuid4
from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.dialects.sqlite import insert as sqlite_insert
from app.modules.public_review.model import PublicReviewRateLimitModel

class PublicRateLimitExceeded(Exception): pass

def consume(session, *, operation: str, client_identity: str, limit: int, now=None):
    now = (now or datetime.now(timezone.utc)).astimezone(timezone.utc)
    window = now.replace(second=0, microsecond=0)
    digest = sha256(client_identity.encode()).hexdigest()
    factory = pg_insert if session.get_bind().dialect.name == "postgresql" else sqlite_insert
    statement = factory(PublicReviewRateLimitModel).values(id=str(uuid4()), operation=operation, client_digest=digest, window_start=window, request_count=1)
    statement = statement.on_conflict_do_update(index_elements=["operation", "client_digest", "window_start"], set_={"request_count": PublicReviewRateLimitModel.request_count + 1}, where=PublicReviewRateLimitModel.request_count < limit).returning(PublicReviewRateLimitModel.request_count)
    if session.scalar(statement) is None: raise PublicRateLimitExceeded()
