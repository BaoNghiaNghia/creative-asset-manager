"""Dependency-free functional smoke for the actual Muse long-poll function.

Executes the parsed repository function body in a fake environment. This
validates control flow without pretending to test FastAPI, PostgreSQL or Muse.
"""
from __future__ import annotations

import ast
import asyncio
from copy import deepcopy
from pathlib import Path
from types import SimpleNamespace
import unittest


ROOT = Path(__file__).resolve().parents[3]
ROUTER = ROOT / "app/modules/muse_bridge/router.py"


def isolated_callable(name: str, namespace: dict):
    tree = ast.parse(ROUTER.read_text(encoding="utf-8"))
    node = next(node for node in tree.body
                if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
                and node.name == name)
    node = deepcopy(node)
    node.decorator_list = []
    node.returns = None
    for arg in [*node.args.posonlyargs, *node.args.args, *node.args.kwonlyargs]:
        arg.annotation = None
    node.args.defaults = []
    node.args.kw_defaults = [None] * len(node.args.kwonlyargs)
    code = compile(ast.fix_missing_locations(ast.Module(body=[node], type_ignores=[])),
                   str(ROUTER), "exec")
    exec(code, namespace)
    return namespace[name]


class LongPollStdlibTests(unittest.TestCase):
    def run_case(self, data, *, wait_seconds=0, limit=1):
        calls = []

        def read(tenant, limit):
            calls.append((tenant, limit))
            return data

        async def in_thread(fn, *args):
            return fn(*args)

        namespace = {
            "asyncio": asyncio,
            "run_in_threadpool": in_thread,
            "_read_muse_commands_for_long_poll": read,
        }
        fn = isolated_callable("wait_muse_commands", namespace)
        response = SimpleNamespace(headers={})
        result = asyncio.run(fn(response, limit, wait_seconds, "tenant-a"))
        return result, response.headers, calls

    def test_ready_job_returned_at_once(self):
        data = {"items": [{"commandId": "job-1"}], "count": 1}
        result, headers, calls = self.run_case(data, wait_seconds=20)
        self.assertEqual(result["items"], data["items"])
        self.assertTrue(result["longPoll"])
        self.assertEqual(headers.get("Cache-Control"), "private, no-store")
        self.assertEqual(calls, [("tenant-a", 1)])

    def test_empty_queue_zero_wait_never_claims(self):
        result, headers, calls = self.run_case({"items": [], "count": 0}, limit=3)
        self.assertEqual(result["items"], [])
        self.assertNotIn("lease", result)
        self.assertEqual(calls, [("tenant-a", 3)])
        self.assertEqual(headers.get("Cache-Control"), "private, no-store")

    def test_short_poll_wrapper_no_cache(self):
        namespace = {"_muse_command_list_payload": lambda n, t, d: (n, t, d)}
        fn = isolated_callable("list_muse_commands", namespace)
        response = SimpleNamespace(headers={})
        self.assertEqual(fn(response, 2, "tenant-a", "db"), (2, "tenant-a", "db"))
        self.assertEqual(response.headers.get("Cache-Control"), "private, no-store")

    def test_route_uses_existing_scoped_muse_dependency(self):
        tree = ast.parse(ROUTER.read_text(encoding="utf-8"))
        fn = next(node for node in tree.body
                  if isinstance(node, ast.AsyncFunctionDef)
                  and node.name == "wait_muse_commands")
        self.assertIn("/commands/wait", ast.unparse(fn.decorator_list))
        self.assertIn("Depends(_muse)", ast.unparse(fn.args.defaults))
