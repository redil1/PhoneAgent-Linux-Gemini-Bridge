"""Open Studio browser tabs must not stall shutdown or leak registrations."""
from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock

import pytest
from aiohttp import WSServerHandshakeError
from aiohttp.test_utils import TestClient, TestServer

from phone_agent_gateway.ai_bridge.web_server import PhoneAgentWebServer


def server_without_voice():
    server = PhoneAgentWebServer()
    server.app.on_startup.clear()  # This fixture exercises only HTTP/WebSocket lifecycle.
    server._stop_inbound_monitor = AsyncMock()
    server._terminate_owned_process = AsyncMock()
    server._websocket_shutdown_timeout_secs = 0.05
    return server


@pytest.mark.asyncio
async def test_shutdown_closes_browser_websocket_without_waiting_for_heartbeat():
    server = server_without_voice()
    transport = TestServer(server.app)
    async with TestClient(transport) as client:
        ws = await client.ws_connect('/ws', autoclose=False)
        await ws.receive_json()
        assert len(server._ws_clients) == 1
        await asyncio.wait_for(transport.close(), 0.7)
        assert not server._ws_clients
        assert server._shutting_down
        await ws.close()


@pytest.mark.asyncio
async def test_websocket_connections_are_rejected_after_shutdown_starts():
    server = server_without_voice()
    async with TestClient(TestServer(server.app)) as client:
        server._shutting_down = True
        with pytest.raises(WSServerHandshakeError) as error:
            await client.ws_connect('/ws')
        assert error.value.status == 503
        assert not server._ws_clients


@pytest.mark.asyncio
async def test_failed_initial_status_send_does_not_leave_registered_client(monkeypatch):
    server = server_without_voice()
    class Socket:
        prepare = AsyncMock()
        send_json = AsyncMock(side_effect=ConnectionResetError('controlled disconnect'))
    socket = Socket()
    monkeypatch.setattr('phone_agent_gateway.ai_bridge.web_server.web.WebSocketResponse', lambda **_: socket)
    with pytest.raises(ConnectionResetError):
        await server.handle_websocket(object())
    assert not server._ws_clients


@pytest.mark.asyncio
async def test_shutdown_racing_handshake_closes_new_connection(monkeypatch):
    server = server_without_voice()
    class Socket:
        close = AsyncMock()
        async def prepare(self, _):
            server._shutting_down = True
    socket = Socket()
    monkeypatch.setattr('phone_agent_gateway.ai_bridge.web_server.web.WebSocketResponse', lambda **_: socket)
    assert await server.handle_websocket(object()) is socket
    socket.close.assert_awaited_once()
    assert not server._ws_clients
