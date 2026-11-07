"""Agent Harness phase-1 regression tests; stdlib only for bootstrap environments."""
from __future__ import annotations

import unittest

from app.modules.agent_harness.registry import (
    AgentDefinition, AgentRegistry, AgentRegistryError, ExecutorDefinition,
)


def make_agent(version: str = "v1", **overrides: object) -> AgentDefinition:
    args = dict(
        agent_id="gatorhats.typography", version=version,
        capabilities=frozenset({"generate_text", "image_edit"}),
        allowed_tools=frozenset({"brief.read", "design.write"}),
        preferred_executor="codex", fallback_executors=("browser",),
        max_concurrent=2, max_retries=1,
    )
    args.update(overrides)
    return AgentDefinition(**args)


class TestAgentRegistry(unittest.TestCase):
    def test_version_and_fallback(self) -> None:
        registry = AgentRegistry()
        registry.register_agent(make_agent())
        registry.register_agent(make_agent("v2"))
        registry.register_executor(ExecutorDefinition("codex", frozenset({"generate_text"})))
        registry.register_executor(ExecutorDefinition("browser", frozenset({"image_edit"})))
        self.assertEqual(registry.route("gatorhats.typography", "v1", ["generate_text"]), "codex")
        self.assertEqual(registry.route("gatorhats.typography", "v1", ["image_edit"]), "browser")
        self.assertEqual(registry.agent("gatorhats.typography", "v2").version, "v2")

    def test_unhealthy_and_offline_fail_closed(self) -> None:
        registry = AgentRegistry()
        registry.register_agent(make_agent())
        registry.register_executor(ExecutorDefinition("codex", frozenset({"image_edit"}), healthy=False))
        registry.register_executor(ExecutorDefinition("browser", frozenset({"image_edit"}), available=False))
        with self.assertRaisesRegex(AgentRegistryError, "no healthy"):
            registry.route("gatorhats.typography", "v1", ["image_edit"])

    def test_no_unapproved_capability(self) -> None:
        registry = AgentRegistry()
        registry.register_agent(make_agent())
        for requirements in ([], ["shell.write"], ["image_edit", "shell.write"]):
            with self.subTest(requirements=requirements):
                with self.assertRaisesRegex(AgentRegistryError, "not permitted"):
                    registry.route("gatorhats.typography", "v1", requirements)

    def test_pinned_agent_versions_not_overwritten(self) -> None:
        registry = AgentRegistry()
        registry.register_agent(make_agent())
        with self.assertRaisesRegex(AgentRegistryError, "already registered"):
            registry.register_agent(make_agent())
        with self.assertRaisesRegex(AgentRegistryError, "unknown"):
            registry.agent("gatorhats.typography", "latest")

    def test_tool_allowlist_is_exact(self) -> None:
        registry = AgentRegistry()
        registry.register_agent(make_agent())
        self.assertTrue(registry.permitted_tool("gatorhats.typography", "v1", "brief.read"))
        self.assertFalse(registry.permitted_tool("gatorhats.typography", "v1", "admin.delete"))
        self.assertFalse(registry.permitted_tool("gatorhats.typography", "v1", ""))

    def test_invalid_definitions(self) -> None:
        candidates = [
            {"capabilities": frozenset()},
            {"fallback_executors": ("codex",)},
            {"fallback_executors": ("browser", "browser")},
            {"max_concurrent": 0},
            {"max_retries": -1},
            {"version": "latest"},
            {"agent_id": "../bad"},
            {"max_concurrent": True},
            {"capabilities": "shell"},
            {"allowed_tools": frozenset({"unsafe tool"})},
        ]
        for item in candidates:
            with self.subTest(item=item):
                with self.assertRaises(AgentRegistryError):
                    make_agent(**item)

    def test_permission_sets_cannot_be_mutated(self) -> None:
        source = {"read"}
        agent = make_agent(capabilities=source)
        source.add("admin")
        self.assertEqual(agent.capabilities, frozenset({"read"}))
        self.assertIsInstance(agent.capabilities, frozenset)

    def test_executor_validation(self) -> None:
        with self.assertRaises(AgentRegistryError):
            ExecutorDefinition("UPPER", frozenset({"read"}))
        with self.assertRaises(AgentRegistryError):
            ExecutorDefinition("local", frozenset({"read"}), healthy="yes")
        registry = AgentRegistry()
        registry.register_executor(ExecutorDefinition("local", frozenset({"read"})))
        with self.assertRaisesRegex(AgentRegistryError, "already"):
            registry.register_executor(ExecutorDefinition("local", frozenset({"read"})))

    def test_requires_registered_executor_even_if_configured(self) -> None:
        registry = AgentRegistry()
        registry.register_agent(make_agent())
        with self.assertRaisesRegex(AgentRegistryError, "no healthy"):
            registry.route("gatorhats.typography", "v1", ["generate_text"])


if __name__ == "__main__":
    unittest.main()
