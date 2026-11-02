"""Review Scout leases renew even when browser navigation makes no progress."""
import asyncio

from scout import maintain_scout_run_lease


def test_background_lease_keepalive_renews_and_stops_after_run():
    class Client:
        def __init__(self):
            self.calls = []

        async def heartbeat(self, status, *, run_id):
            self.calls.append((status, run_id))

    async def run():
        client = Client()
        async with maintain_scout_run_lease(client, "run-1", interval_seconds=.005):
            await asyncio.sleep(.026)
        before = len(client.calls)
        await asyncio.sleep(.018)
        assert before >= 3
        assert len(client.calls) == before
        assert all(call == ("busy", "run-1") for call in client.calls)

    asyncio.run(run())


def test_transient_heartbeat_network_error_does_not_kill_browser_run():
    class Flaky:
        def __init__(self):
            self.count = 0

        async def heartbeat(self, status, *, run_id):
            self.count += 1
            if self.count == 1:
                raise OSError("temporary network interruption")

    async def run():
        client = Flaky()
        async with maintain_scout_run_lease(client, "run-2", interval_seconds=.004):
            await asyncio.sleep(.019)
        assert client.count >= 2

    asyncio.run(run())
