"""Optional corroboration must survive Studio saves and voice-child configuration."""
from __future__ import annotations

from unittest.mock import AsyncMock

import pytest
from aiohttp.test_utils import TestClient, TestServer

from phone_agent_gateway.ai_bridge.corroborated_cloud_stt import CorroboratedCloudSTTService
from phone_agent_gateway.ai_bridge.production_pipeline import create_provider_services
from phone_agent_gateway.ai_bridge.runtime_config import ProviderConfig
from phone_agent_gateway.ai_bridge.web_server import PhoneAgentWebServer


def test_corroboration_is_explicitly_opt_in_and_child_environment_roundtrips(monkeypatch):
    monkeypatch.delenv('PHONE_AGENT_ANTIGRAVITY_LOCAL_CORROBORATION', raising=False)
    assert not ProviderConfig.from_env().antigravity_live_local_corroboration
    server = PhoneAgentWebServer(config=ProviderConfig(antigravity_live_local_corroboration=True))
    environment = server._child_environment()
    assert environment['PHONE_AGENT_ANTIGRAVITY_LOCAL_CORROBORATION']=='true'
    monkeypatch.setenv('PHONE_AGENT_ANTIGRAVITY_LOCAL_CORROBORATION', environment['PHONE_AGENT_ANTIGRAVITY_LOCAL_CORROBORATION'])
    assert ProviderConfig.from_env().antigravity_live_local_corroboration


@pytest.mark.asyncio
async def test_factory_selects_corroborated_service_only_when_enabled():
    services = create_provider_services(ProviderConfig(antigravity_live_local_corroboration=True), 16000)
    assert isinstance(services.stt, CorroboratedCloudSTTService)
    await services.stt.cleanup()
    await services.tts.cleanup()


@pytest.mark.asyncio
async def test_studio_can_enable_disable_and_restore_saved_corroboration(tmp_path):
    settings = tmp_path/'studio.json'
    server = PhoneAgentWebServer(config=ProviderConfig(), settings_path=settings)
    server.app.on_startup.clear()
    server._stop_inbound_monitor = AsyncMock()
    server._start_inbound_monitor = AsyncMock()
    async with TestClient(TestServer(server.app)) as client:
        response = await client.post('/api/config', json={'antigravity_live_local_corroboration':True})
        assert response.status==200
        assert server.config.antigravity_live_local_corroboration
        restored = PhoneAgentWebServer(settings_path=settings)
        assert restored.config.antigravity_live_local_corroboration
        assert restored._public_config()['antigravity_live_local_corroboration'] is True
        response = await client.post('/api/config', json={'antigravity_live_local_corroboration':False})
        assert response.status==200
        assert not server.config.antigravity_live_local_corroboration


def test_untyped_false_string_cannot_enable_corroboration():
    from phone_agent_gateway.ai_bridge.runtime_config import ConfigurationError
    with pytest.raises(ConfigurationError, match='true or false'):
        ProviderConfig(**{'antigravity_live_local_corroboration':'false'}).validate(require_credentials=False)
