from pathlib import Path
import tempfile
from sqlalchemy import create_engine, event
from sqlalchemy.orm import sessionmaker

from app.core.database import Base
from app.modules.auth_persistence.model import TenantModel
from app.modules.inventory.daily_sheet.prompts import InventoryPromptResolver
from app.modules.inventory.persistence_model import InventoryPromptVersionModel

def db():
    temp=tempfile.TemporaryDirectory(); engine=create_engine(f"sqlite:///{Path(temp.name)/'db.sqlite'}")
    event.listen(engine,"connect",lambda conn,_:conn.execute("PRAGMA foreign_keys=ON"))
    for name in ("tenants","inventory_prompt_versions"): Base.metadata.tables[name].create(engine)
    sessions=sessionmaker(bind=engine,expire_on_commit=False)
    with sessions.begin() as session: session.add_all([TenantModel(id="a",name="A",slug="a"),TenantModel(id="b",name="B",slug="b")])
    return temp,engine,sessions

def test_custom_overrides_legacy_then_reset_restores_legacy_and_versions_are_immutable():
    temp,engine,sessions=db(); prompts=InventoryPromptResolver(sessions)
    assert prompts.resolve("a","daily_gemini_processing",legacy_goals=["legacy"]).source == "legacy_config"
    draft=prompts.draft("a","daily_gemini_processing","Vietnamese hướng dẫn", "user")
    assert prompts.resolve("a","daily_gemini_processing",legacy_goals=["legacy"]).source == "legacy_config"
    prompts.activate("a","daily_gemini_processing",draft["id"],"user")
    resolved=prompts.resolve("a","daily_gemini_processing",legacy_goals=["legacy"])
    assert (resolved.source,resolved.version,resolved.content)==("custom","custom-v1","Vietnamese hướng dẫn")
    prompts.reset("a","daily_gemini_processing","user")
    assert prompts.resolve("a","daily_gemini_processing",legacy_goals=["legacy"]).source == "legacy_config"
    assert prompts.versions("b","daily_gemini_processing") == []
    engine.dispose();temp.cleanup()

def test_restore_creates_new_monotonic_active_version_and_hash_is_deterministic():
    temp,engine,sessions=db(); prompts=InventoryPromptResolver(sessions)
    first=prompts.draft("a","carry_forward_0900","same",None); prompts.activate("a","carry_forward_0900",first["id"],None)
    second=prompts.draft("a","carry_forward_0900","other",None); prompts.activate("a","carry_forward_0900",second["id"],None)
    restored=prompts.restore("a","carry_forward_0900",first["id"],None)
    assert restored["version"] == 3 and restored["status"] == "active"
    assert prompts.resolve("a","carry_forward_0900").content_hash == first["content_hash"]
    engine.dispose();temp.cleanup()
