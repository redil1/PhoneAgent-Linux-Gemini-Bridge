"""Broken speech dependencies must not report a ready, silently listening call."""
from __future__ import annotations

import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from pipecat.frames.frames import StartFrame
from pipecat.services.stt_service import STTService

from phone_agent_gateway.ai_bridge.antigravity_live_stt import AntigravityLiveSTTService
from phone_agent_gateway.ai_bridge.local_neural_stt import LocalNeuralSTTService
from phone_agent_gateway.ai_bridge.production_pipeline import ProductionCallPipeline


class Vad:
    def set_sample_rate(self, _):
        pass


@pytest.mark.asyncio
async def test_cloud_discovery_failure_emits_fatal_startup_error(monkeypatch):
    monkeypatch.setattr(STTService,'start',AsyncMock())
    stt = AntigravityLiveSTTService(smart_turn_enabled=False, vad_analyzer=Vad())
    def unavailable():
        raise RuntimeError('controlled missing bridge')
    stt._discover_bridge = unavailable
    stt.push_error = AsyncMock()
    with pytest.raises(RuntimeError, match='missing bridge'):
        await stt.start(StartFrame())
    assert stt.push_error.call_args.kwargs['fatal'] is True
    assert stt._session_id is None and stt._reader_task is None


@pytest.mark.asyncio
async def test_local_load_failure_emits_fatal_startup_error(monkeypatch):
    monkeypatch.setattr(STTService,'start',AsyncMock())
    monkeypatch.setattr('phone_agent_gateway.ai_bridge.local_neural_stt.load_model_async',AsyncMock(side_effect=RuntimeError('model missing')))
    stt = LocalNeuralSTTService(model='test', smart_turn_enabled=False, vad_analyzer=Vad())
    stt.push_error = AsyncMock()
    with pytest.raises(RuntimeError, match='model missing'):
        await stt.start(StartFrame())
    assert stt.push_error.call_args.kwargs['fatal'] is True
    assert stt._watchdog_task is None


@pytest.mark.asyncio
@pytest.mark.parametrize('failure', ['provider','worker_exit'])
async def test_pipeline_start_stops_immediately_on_failure_instead_of_waiting_for_ready(monkeypatch, failure):
    owner = ProductionCallPipeline.__new__(ProductionCallPipeline)
    owner._runner_task = None
    owner._attach_tools = AsyncMock()
    owner.worker = object()
    owner.transport = SimpleNamespace(session=SimpleNamespace(call_id='startup-test'))
    owner._started = asyncio.Event()
    owner._startup_failed = asyncio.Event()
    owner._startup_error = None
    owner.cancel = AsyncMock()
    class Runner:
        def __init__(self, **_):
            pass
        async def add_workers(self, _):
            pass
        async def run(self):
            if failure=='provider':
                owner._startup_error = 'Speech model unavailable'
                owner._startup_failed.set()
                owner._started.set()  # A racing ready event cannot override failure.
    monkeypatch.setattr('phone_agent_gateway.ai_bridge.production_pipeline.WorkerRunner',Runner)
    with pytest.raises(RuntimeError, match='unavailable|stopped before'):
        await asyncio.wait_for(owner.start(timeout_secs=20), 0.5)
    owner.cancel.assert_awaited_once_with('pipeline startup failed')


def test_discovery_waits_for_launched_standalone_bridge(monkeypatch):
    import subprocess
    import urllib.error

    from phone_agent_gateway.ai_bridge import antigravity_live_stt as module
    clock = [0.0]
    launches = []
    stt = AntigravityLiveSTTService(smart_turn_enabled=False, vad_analyzer=Vad())
    monkeypatch.delenv('ANTIGRAVITY_PORT', raising=False)
    monkeypatch.delenv('ANTIGRAVITY_CSRF_TOKEN', raising=False)
    monkeypatch.setattr(module.sys,'platform','darwin')
    monkeypatch.setattr(module.time,'monotonic',lambda:clock[0])
    monkeypatch.setattr(module.time,'sleep',lambda seconds:clock.__setitem__(0,clock[0]+seconds))
    def command(args, **_):
        if args[0]=='ps':
            return '77 language_server --csrf_token abc123' if clock[0]>=2 else ''
        if clock[0]>=2 and '-p' in args:
            return 'language_server 77 TCP 127.0.0.1:55555 (LISTEN)'
        raise subprocess.CalledProcessError(1,args)
    monkeypatch.setattr(module.subprocess,'check_output',command)
    def launch(args, **_):
        launches.append(args)
        return SimpleNamespace(returncode=0)
    monkeypatch.setattr(module.subprocess,'run',launch)
    class Response:
        def __enter__(self):
            return self
        def __exit__(self,*_):
            pass
        def read(self):
            return b'{"csrfToken":"abc123"}'
    def get(request, **_):
        if request.full_url=='https://127.0.0.1:55555/' and clock[0]>=2:
            return Response()
        raise urllib.error.URLError('Not ready')
    monkeypatch.setattr(module.urllib.request,'urlopen',get)
    stt._discover_bridge()
    assert stt._base_url=='https://127.0.0.1:55555'
    assert len(launches)==1 and launches[0][-1]=='Antigravity'
    assert 2<=clock[0]<=6
