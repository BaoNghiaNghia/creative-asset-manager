"""Codex Inventory provider contract and fail-closed tool-loop tests (offline)."""
from __future__ import annotations

from contextlib import nullcontext
import json
import os
from pathlib import Path
from types import SimpleNamespace

import pytest

from app.modules.inventory.ai.codex_cli_gateway import (
    DEFAULT_MODEL, TOOL_OUTPUT_SCHEMA, RuntimeInventoryCodexGateway,
    CodexCliConfig, _safe_tool_calls, _safe_response, build_codex_inventory_gateway,
)
from app.modules.inventory.ai.gateway import InventoryAiGatewayError
from app.modules.inventory.daily_sheet.agent_v4.tools import function_declarations as v4_declarations
from app.modules.inventory.daily.carry_forward_planner import function_declarations as carry_declarations


def gateway(tmp_path: Path) -> RuntimeInventoryCodexGateway:
    home = tmp_path / "codex"
    home.mkdir(exist_ok=True)
    binary = tmp_path / "bin"
    binary.write_text("mock")
    binary.chmod(0o700)
    return RuntimeInventoryCodexGateway(CodexCliConfig(str(home), str(binary)))


def test_accepts_real_inventory_v4_and_carry_forward_tool_specs():
    for declarations in (v4_declarations(), carry_declarations()):
        assert 1 <= len(declarations) <= 36
        first = declarations[0]
        result = {"calls": [{"name": first["name"], "arguments_json": "{}"}], "reason": ""}
        # Validate allowlisting independently of tool parameter requirements.
        assert _safe_tool_calls(result, [{"name": first["name"], "parameters": {"type": "object"}}])[0].name == first["name"]


def test_forbids_unlisted_tool_and_bypassed_force():
    declarations = [{"name": "get_workbook_metadata", "parameters": {"type": "object"}}]
    with pytest.raises(InventoryAiGatewayError, match="inventory_codex_unapproved_tool"):
        _safe_tool_calls({"calls": [{"name": "write_sheet", "arguments_json": "{}"}], "reason": ""},
                         declarations)
    with pytest.raises(InventoryAiGatewayError, match="inventory_codex_unknown_forced_tool"):
        _safe_tool_calls({"calls": [{"name": "get_workbook_metadata", "arguments_json": "{}"}], "reason": ""},
                         declarations, force="submit_workbook_plan")
    with pytest.raises(InventoryAiGatewayError, match="inventory_codex_forced_tool_missing"):
        _safe_tool_calls({"calls": [], "reason": ""}, declarations, force="get_workbook_metadata")


@pytest.mark.parametrize("encoded", ['[]', 'null', '{"unexpected":true}', 'broken'])
def test_rejects_invalid_arguments(encoded):
    declarations = [{"name": "inspect", "parameters": {
        "type": "object", "properties": {"id": {"type": "string"}},
        "required": ["id"], "additionalProperties": False,
    }}]
    with pytest.raises(InventoryAiGatewayError):
        _safe_tool_calls({"calls": [{"name": "inspect", "arguments_json": encoded}], "reason": ""}, declarations)


def test_structured_text_schema_is_checked(tmp_path, monkeypatch):
    client = gateway(tmp_path)
    payload = {"status": "review_required", "count": 3}
    monkeypatch.setattr(client, "_invoke", lambda *a, **k: {"payload_json": json.dumps(payload)})
    schema = {"type": "object", "properties": {
        "status": {"type": "string"}, "count": {"type": "integer"},
    }, "required": ["status", "count"], "additionalProperties": False}
    result = client.analyze_structured_text(tenant_id="t", prompt="review", schema=schema,
                                            model=DEFAULT_MODEL, provider="codex_cli")
    assert result.extracted_json == payload
    assert result.raw_response_json["provider"] == "codex_cli"
    assert result.estimated_cost_micros == 0
    monkeypatch.setattr(client, "_invoke", lambda *a, **k: {"payload_json": '{"count":"oops","status":"ok"}'})
    with pytest.raises(InventoryAiGatewayError, match="inventory_codex_schema_mismatch"):
        client.analyze_structured_text(tenant_id="t", prompt="review", schema=schema,
                                       model=DEFAULT_MODEL, provider="codex_cli")


def test_tool_round_returns_existing_gemini_part_envelope_without_gemini_rest(tmp_path, monkeypatch):
    client = gateway(tmp_path)
    observed = {}
    def fake(prompt, schema, *, model, **kwargs):
        observed["prompt"], observed["schema"], observed["model"] = prompt, schema, model
        return {"calls": [{"name": "search_material_catalog", "arguments_json": '{"query":"milk"}'}],
                "reason": "find evidence"}
    monkeypatch.setattr(client, "_invoke", fake)
    calls = [{"name": "search_material_catalog", "parameters": {
        "type": "object", "properties": {"query": {"type": "string"}},
        "required": ["query"], "additionalProperties": False,
    }}]
    turn = client.generate_tool_turn(tenant_id="t", contents=[
        {"role": "user", "parts": [{"text": "only search evidence"}]},
    ], function_declarations=calls, model=DEFAULT_MODEL, provider="codex_cli")
    assert [c.name for c in turn.calls] == ["search_material_catalog"]
    assert turn.calls[0].arguments == {"query": "milk"}
    assert turn.content["parts"][0]["functionCall"]["name"] == "search_material_catalog"
    assert "PRIOR_TURNS_JSON" in observed["prompt"]
    assert observed["model"] == DEFAULT_MODEL


def test_image_input_only_uses_supported_mime(tmp_path, monkeypatch):
    client = gateway(tmp_path)
    seen = {}
    def fake(prompt, schema, *, model, image_bytes=None, image_mime_type=""):
        seen["image"], seen["mime"] = image_bytes, image_mime_type
        return {"payload_json": '{"document_type":"sheet","page_number":1,"page_count":1,"raw_item_lines":[]}'}
    monkeypatch.setattr(client, "_invoke", fake)
    schema = {"type":"object", "properties": {
        "document_type":{"type":"string"},"page_number":{"type":"integer"},
        "page_count":{"type":"integer"},"raw_item_lines":{"type":"array"}},
        "required":["document_type","page_number","page_count","raw_item_lines"],
    }
    client.analyze(tenant_id="t", image_bytes=b"fake-image", image_mime_type="image/jpeg",
                   prompt="read", schema=schema, provider="codex_cli", model=DEFAULT_MODEL)
    assert seen == {"image": b"fake-image", "mime": "image/jpeg"}


def test_real_cli_command_enforces_shared_profile_readonly_no_secrets(tmp_path, monkeypatch):
    client = gateway(tmp_path)
    home = Path(client.config.home)
    monkeypatch.setattr(os, "geteuid", lambda: home.stat().st_uid or 77)
    # Simulate a non-root service identity and authorized filesystem ownership.
    uid = 77
    orig_stat = Path.stat
    def stat(self, *args, **kwargs):
        res = orig_stat(self, *args, **kwargs)
        if self == home:
            return SimpleNamespace(st_uid=uid, st_mode=res.st_mode)
        return res
    monkeypatch.setattr(Path, "stat", stat)
    monkeypatch.setattr(os, "geteuid", lambda: uid)
    import app.modules.inventory.ai.codex_cli_gateway as module
    monkeypatch.setattr(module, "_managed_profile_slot", lambda home: nullcontext())
    monkeypatch.setenv("DATABASE_URL", "super-secret-do-not-copy")
    monkeypatch.setenv("OPENAI_API_KEY", "do-not-copy")
    recorded = {}

    class Popen:
        returncode = 0
        pid = 12345
        def __init__(self, args, *, cwd, env, stdin, stdout, stderr, start_new_session):
            recorded["args"], recorded["env"], recorded["cwd"] = args, env, cwd
            output = Path(args[args.index("--output-last-message") + 1])
            output.write_text('{"payload_json":"{\\"ok\\":true}"}')
        def communicate(self, input, **kw):
            recorded["prompt"] = input
            return b"", b""

    monkeypatch.setattr(module.subprocess, "Popen", Popen)
    result = client._invoke("sample", module.TEXT_OUTPUT_SCHEMA, model=DEFAULT_MODEL)
    assert "payload_json" in result
    assert "--sandbox" in recorded["args"] and recorded["args"][recorded["args"].index("--sandbox") + 1] == "read-only"
    assert "--ignore-user-config" in recorded["args"] and "--ephemeral" in recorded["args"]
    assert "--dangerously-bypass-approvals-and-sandbox" not in recorded["args"]
    assert recorded["env"]["CODEX_HOME"] == str(home)
    assert "OPENAI_API_KEY" not in recorded["env"] and "DATABASE_URL" not in recorded["env"]


def test_factory_uses_existing_account_01_and_disallows_gemini():
    settings = SimpleNamespace(
        INVENTORY_AI_PROVIDER="codex_cli", INVENTORY_CODEX_ACCOUNT="account-1",
        INVENTORY_CODEX_TIMEOUT_SECONDS=150, CODEX_IMAGE_BINARY="codex",
        CODEX_IMAGE_HOME="/var/lib/creative-asset-manager/codex",
    )
    client=build_codex_inventory_gateway(settings)
    assert client.config.home.endswith("/codex")
    assert client.config.account == "account-1"
    settings.INVENTORY_AI_PROVIDER = "gemini"
    with pytest.raises(InventoryAiGatewayError, match="inventory_codex_provider_not_enabled"):
        build_codex_inventory_gateway(settings)
