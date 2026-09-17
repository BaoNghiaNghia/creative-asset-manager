from datetime import datetime, timezone
import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session
from app.core.database import Base
from app.modules.public_review.rate_limit import PublicRateLimitExceeded, consume

def test_public_rate_limit_is_database_backed_and_hashes_client_identity():
 engine=create_engine("sqlite://")
 Base.metadata.create_all(engine)
 with Session(engine) as first:
  consume(first,operation="session",client_identity="198.51.100.7",limit=1); first.commit()
 with Session(engine) as second:
  with pytest.raises(PublicRateLimitExceeded): consume(second,operation="session",client_identity="198.51.100.7",limit=1)
  second.rollback()
