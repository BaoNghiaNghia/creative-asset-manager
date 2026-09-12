import asyncio
from pathlib import Path
import sys
import pytest

UPSTREAM = Path(__file__).parents[1] / "upstream"
sys.path.insert(0, str(UPSTREAM))
from interactive_login import InteractiveLoginManager

class Pool:
    def __init__(self): self.accounts=["a","b"]; self.owners={}; self.status={}
    def reserve_account(self, name, owner):
        if name in self.owners: raise RuntimeError("Account is busy")
        self.owners[name]=owner
    def release_account(self, name, owner):
        if self.owners.get(name)==owner: self.owners.pop(name)
    def set_login_status(self,name,ok): self.status[name]=ok
    async def verify_account_owned(self,name): return False

def test_same_account_login_is_exclusive_and_stop_releases_owner():
    async def run():
        pool=Pool(); manager=InteractiveLoginManager(pool, timeout=60)
    # avoid launching a real browser; ownership is acquired before launch.
        async def stalled(session):
            try: await asyncio.Future()
            finally: pool.release_account(session.account, "INTERACTIVE_LOGIN")
        manager._run=stalled
        await manager.start("a")
        with pytest.raises(RuntimeError): await manager.start("a")
        assert pool.owners["a"]=="INTERACTIVE_LOGIN"
        await manager.stop("a")
        assert "a" not in pool.owners
    asyncio.run(run())

def test_global_interactive_login_limit_rejects_second_account():
    async def run():
        pool=Pool(); manager=InteractiveLoginManager(pool, timeout=60, max_sessions=1)
        async def stalled(session):
            try: await asyncio.Future()
            finally: pool.release_account(session.account, "INTERACTIVE_LOGIN")
        manager._run=stalled
        await manager.start("a")
        with pytest.raises(RuntimeError): await manager.start("b")
        await manager.shutdown()
    asyncio.run(run())
