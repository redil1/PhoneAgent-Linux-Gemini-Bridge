from __future__ import annotations

import json
import os
from pathlib import Path

import pytest
from aiohttp import web
from aiohttp.test_utils import TestServer

from phone_agent_gateway.ai_bridge.frappe_integration import (
    FrappeConfig,
    FrappeConfigStore,
    FrappeToolRuntime,
)
from phone_agent_gateway.ai_bridge.tasks.tool_catalog import execute_tool
from phone_agent_gateway.ai_bridge.tool_control import MASKED_SECRET


def _config(base_url: str, *enabled: str) -> FrappeConfig:
    tools = [
        policy.model_copy(
            update={"enabled": policy.name in enabled, "task_ids": ["iptv"]}
        )
        for policy in FrappeConfig().tools
    ]
    return FrappeConfig(
        enabled=True,
        base_url=base_url,
        api_key="integration-key",
        api_secret="integration-secret",
        tools=tools,
    )


def test_config_masks_both_credentials_and_preserves_them(tmp_path: Path) -> None:
    store = FrappeConfigStore(tmp_path / "frappe.json")
    saved = store.save(
        FrappeConfig(
            api_key="private-key",
            api_secret="private-secret",
        ).model_dump(mode="json")
    )

    assert saved.revision == 1
    assert os.stat(store.path).st_mode & 0o777 == 0o600
    public = store.public_state()
    assert public["api_key"] == MASKED_SECRET
    assert public["api_secret"] == MASKED_SECRET
    assert "private" not in json.dumps(public)

    public["enabled"] = True
    updated = store.save(public)
    assert updated.api_key == "private-key"
    assert updated.api_secret == "private-secret"


def test_remote_plain_http_and_incomplete_activation_fail_closed() -> None:
    with pytest.raises(ValueError, match="must use HTTPS"):
        FrappeConfig(base_url="http://erp.example.com")
    with pytest.raises(ValueError, match="requires an API key"):
        FrappeConfig(enabled=True)


def test_tool_enablement_can_leave_automatic_outcome_writes_disabled():
    assert FrappeConfig().record_call_outcomes_enabled is False
    legacy = FrappeConfig.model_validate({"enabled": True, "api_key": "fixture", "api_secret": "fixture"})
    assert legacy.record_call_outcomes_enabled is True
    explicit = FrappeConfig(enabled=True, api_key="fixture", api_secret="fixture", record_call_outcomes_enabled=False)
    assert explicit.record_call_outcomes_enabled is False
    with pytest.raises(ValueError):
        FrappeConfig(record_call_outcomes_enabled="false")


@pytest.mark.asyncio
@pytest.mark.parametrize("caller", ["unknown:test", "anonymous", ""])
async def test_unknown_caller_cannot_use_arguments_as_customer_binding(caller):
    calls = []
    runtime = FrappeToolRuntime(_config("http://127.0.0.1:8080", "business_upsert_current_lead"),
                                caller_id=caller, task_id="iptv", call_id="fixture-call", call_direction="inbound")

    class Client:
        async def call(self, method, payload):
            calls.append((method, payload))
            return {"verified": True}

    runtime.client = Client()
    with pytest.raises(RuntimeError, match="bound current caller"):
        await runtime._execute("business_upsert_current_lead", {"phone": "+15555550123"})
    assert calls == []


@pytest.mark.asyncio
async def test_business_readers_and_writers_have_matching_execution_metadata(monkeypatch):
    from phone_agent_gateway.ai_bridge.frappe_integration import FrappeClient

    async def healthy(self):
        return {"status": "ok", "required_ready": True}

    monkeypatch.setattr(FrappeClient, "health", healthy)
    config = _config("http://127.0.0.1:8080", "business_get_customer_context", "business_upsert_current_lead", "business_search_catalog")
    for caller, expected in [("+15555550123", {"business_get_customer_context", "business_upsert_current_lead", "business_search_catalog"}),
                             ("unknown:test", {"business_search_catalog"})]:
        runtime = FrappeToolRuntime(config, caller_id=caller, task_id="iptv", call_id="fixture-call", call_direction="inbound")
        try:
            catalog = await runtime.start()
            assert set(catalog) == expected
            for name, tool in catalog.items():
                assert tool.read_only is (name != "business_upsert_current_lead")
                assert tool.spec.read_only is tool.read_only
        finally:
            await runtime.close()


@pytest.mark.asyncio
@pytest.mark.parametrize("ready", [False, None])
async def test_partial_business_health_is_not_advertised_as_ready(monkeypatch, ready):
    from phone_agent_gateway.ai_bridge.frappe_integration import FrappeClient

    async def response(self, method, arguments=None):
        return {"status": "ok", "required_ready": ready}

    monkeypatch.setattr(FrappeClient, "call", response)
    client = FrappeClient(FrappeConfig(), None)
    with pytest.raises(RuntimeError, match="not confirmed ready"):
        await client.health()


@pytest.mark.asyncio
async def test_live_business_tools_are_bound_to_authenticated_current_caller() -> None:
    requests: list[dict] = []

    async def method(request: web.Request) -> web.Response:
        assert request.headers["Authorization"] == "token integration-key:integration-secret"
        name = request.match_info["method"]
        payload = await request.json()
        requests.append({"method": name, "payload": payload})
        if name == "health":
            return web.json_response(
                {
                    "message": {
                        "status": "ok",
                        "site": "phoneagent.localhost",
                        "required_ready": True,
                    }
                }
            )
        return web.json_response(
            {"message": {"verified": True, "phone_received": payload["phone"]}}
        )

    app = web.Application()
    app.router.add_post("/api/method/phoneagent_frappe.api.{method}", method)
    async with TestServer(app) as upstream:
        runtime = FrappeToolRuntime(
            _config(
                str(upstream.make_url("/"))[:-1],
                "business_get_customer_context",
                "business_upsert_current_lead",
            ),
            caller_id="+212600123456",
            task_id="iptv",
            call_id="call-1",
            call_direction="inbound",
        )
        try:
            catalog = await runtime.start()
            definition = catalog["business_get_customer_context"].definition
            assert "phone" not in definition["parameters"]["properties"]
            result = json.loads(
                await execute_tool(catalog, "business_get_customer_context", "{}")
            )
        finally:
            await runtime.close()

    assert result == {"verified": True, "phone_received": "<redacted-phone>"}
    assert requests[-1]["payload"] == {
        "phone": "212600123456",
        "call_id": "call-1",
        "task_id": "iptv",
        "call_direction": "inbound",
        "max_items": 10,
    }


@pytest.mark.asyncio
async def test_registration_preserves_receipt_id_and_never_infers_marketing_permission():
    from phone_agent_gateway.ai_bridge.action_receipts import ActionReceipt, receipt_from_result

    requests = []

    async def method(request):
        name = request.match_info["method"]
        payload = await request.json()
        if name == "health":
            return web.json_response({"message": {"status": "ok", "required_ready": True}})
        requests.append(payload)
        return web.json_response({"message": {"verified": True, "created_or_updated": True,
                                                "lead_id": "CRM-LEAD-2026-00001", "phone_received": payload["phone"]}})

    app = web.Application()
    app.router.add_post("/api/method/phoneagent_frappe.api.{method}", method)
    async with TestServer(app) as server:
        runtime = FrappeToolRuntime(_config(str(server.make_url("/"))[:-1], "business_upsert_current_lead"),
                                    caller_id="+15555550123", task_id="iptv", call_id="fixture-call", call_direction="inbound")
        try:
            catalog = await runtime.start()
            assert "consent_status" not in catalog["business_upsert_current_lead"].definition["parameters"]["properties"]
            result = await runtime._execute("business_upsert_current_lead", {"consent_status": "consented"})
            assert requests[0]["consent_status"] == "unknown"
            assert result["lead_id"] == "CRM-LEAD-2026-00001"
            assert result["phone_received"] == "<redacted-phone>"
            receipt = receipt_from_result(ActionReceipt("fixture", "business_upsert_current_lead", "crm", "registered"), result)
            assert receipt.state == "completed" and receipt.verified_claims == {"registered"}
        finally:
            await runtime.close()


def test_unified_compose_is_local_persistent_and_resource_bounded() -> None:
    root = Path(__file__).resolve().parents[2]
    compose = (root / "integrations/business_suite/compose.yaml").read_text()

    assert "phoneagent-frappe-suite:1.0.0" in compose
    assert '127.0.0.1:${FRAPPE_PORT:-8080}:8080' in compose
    assert "frappe-db: {name: phoneagent-frappe-db}" in compose
    assert "no-new-privileges:true" in compose
    assert "pids_limit:" in compose and "mem_limit:" in compose
    assert "frappe_db_root_password:" in compose
    assert "phoneagent-openwa-data" in compose
    assert "@sha256:" in compose


def test_frappe_app_has_campaign_consent_and_draft_commerce_boundaries() -> None:
    root = Path(__file__).resolve().parents[2]
    app = root / "integrations/business_suite/phoneagent_frappe/phoneagent_frappe"
    api = (app / "api.py").read_text()
    campaign = (
        app
        / "phoneagent_automation/doctype/phoneagent_campaign/phoneagent_campaign.json"
    ).read_text()

    assert "def next_campaign_contact" in api
    assert "def mark_do_not_call" in api
    assert "def create_quotation_draft" in api
    assert "def create_sales_order_draft" in api
    assert "draft_not_submitted" in api
    assert "for update skip locked" in api
    assert '"require_explicit_consent"' in campaign
    assert '"max_daily_calls"' in campaign


def test_macos_installer_handles_both_supported_architectures() -> None:
    root = Path(__file__).resolve().parents[2]
    installer = (root / "tools" / "install_macos.sh").read_text()

    assert 'arm64) REQUIRED_BINARY_ARCH="arm64"' in installer
    assert 'x86_64) REQUIRED_BINARY_ARCH="x86_64"' in installer
    assert "cargo build --locked --release" in installer
    assert 'cp "${WHATSAPP_BINARY_SOURCE}"' in installer
