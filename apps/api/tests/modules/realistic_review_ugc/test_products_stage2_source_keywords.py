"""The Products Stage 2 navigator uses the original Stage 1 keyword, not PNG names."""
from types import SimpleNamespace

from sqlalchemy import create_engine
from sqlalchemy.orm import Session
from sqlalchemy.pool import StaticPool

from app.core.database import Base
from app.modules.realistic_review_ugc.model import (
    RrugcImageOutputVersionModel, RrugcKeywordImageJobModel,
)
from app.modules.realistic_review_ugc.router import _products_stage1_source_keywords


def test_source_plan_keywords_join_stored_stage1_version_and_respect_tenant():
    engine = create_engine(
        "sqlite+pysqlite:///:memory:",
        poolclass=StaticPool,
        connect_args={"check_same_thread": False},
    )
    Base.metadata.create_all(engine, tables=[
        RrugcKeywordImageJobModel.__table__,
        RrugcImageOutputVersionModel.__table__,
    ])
    with Session(engine) as session:
        for tenant, job_id, keyword in [
            ("shop-a", "stage1-one", "COLD BE MEANER"),
            ("shop-b", "stage1-two", "DO NOT DISTURB"),
        ]:
            session.add(RrugcKeywordImageJobModel(
                id=job_id, tenant_id=tenant, keyword_id="kw-" + tenant,
                keyword_text=keyword, skill_source="local",
                skill_name="concept-short-v1", prompt_text=keyword,
                created_by_user_id="operator",
            ))
            session.add(RrugcImageOutputVersionModel(
                id="version-" + tenant, tenant_id=tenant,
                stage="stage1", job_id=job_id, version=2,
                remote_file_id="remote-" + tenant,
            ))
        session.add(RrugcImageOutputVersionModel(
            id="unrelated-stage2", tenant_id="shop-a", stage="ecommerce",
            job_id="stage1-one", version=3, remote_file_id="other",
        ))
        session.commit()
        versions = [
            SimpleNamespace(source_revision="version-shop-a", source_name="design_02.png"),
            SimpleNamespace(source_revision="version-shop-b", source_name="design_04.png"),
            SimpleNamespace(source_revision="unrelated-stage2", source_name="design_05.png"),
            SimpleNamespace(source_revision="no-source", source_name="design_06.png"),
        ]
        assert _products_stage1_source_keywords(session, "shop-a", versions) == {
            "version-shop-a": "COLD BE MEANER",
        }
        assert _products_stage1_source_keywords(session, "shop-b", versions) == {
            "version-shop-b": "DO NOT DISTURB",
        }
        assert _products_stage1_source_keywords(session, "shop-a", []) == {}
