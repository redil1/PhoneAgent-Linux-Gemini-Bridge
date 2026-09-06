"""Caller data must not leak across configured packages or product revisions."""

from types import SimpleNamespace

import pytest

from phone_agent_gateway.ai_bridge.identity.memory import (
    AsyncIdentityMemory,
    LocalEpisodeStore,
    MemoryEpisode,
)
from phone_agent_gateway.ai_bridge.memory.memory_manager import LayeredMemoryManager
from phone_agent_gateway.ai_bridge.memory.scoping import memory_namespace


def test_episode_worker_drains_accepted_work_and_rejects_after_close(tmp_path, monkeypatch):
    monkeypatch.delenv("PHONE_AGENT_GRAPHITI_URL", raising=False)
    memory = AsyncIdentityMemory(LocalEpisodeStore(tmp_path / "episodes.sqlite3"))
    args = dict(caller_id="caller", call_id="call", role="caller", content="Remember this preference",
                language="en", task_id="support")
    assert memory.submit_turn(**args)
    assert memory.close(timeout=2)
    assert memory.local.count() == 1
    assert not memory.submit_turn(**args)
    assert memory.diagnostics()["worker_alive"] is False
    assert memory.close(timeout=0)


def test_full_queue_close_is_bounded_and_eventually_drains_after_slow_mirror(tmp_path, monkeypatch):
    import threading

    from phone_agent_gateway.ai_bridge.identity import memory as memory_module

    monkeypatch.setattr(memory_module, "MAX_QUEUE", 1)
    entered, release = threading.Event(), threading.Event()

    class SlowMirror:
        def add(self, episode):
            entered.set()
            release.wait(timeout=3)

    memory = AsyncIdentityMemory(LocalEpisodeStore(tmp_path / "episodes.sqlite3"), mirror=SlowMirror())
    args = dict(caller_id="caller", call_id="call", role="caller", content="First preference",
                language="en", task_id="support")
    try:
        assert memory.submit_turn(**args)
        assert entered.wait(timeout=1)
        assert memory.submit_turn(**{**args, "content": "Second preference"})
        assert memory.close(timeout=0) is False
        assert not memory.submit_turn(**args)
    finally:
        release.set()
        assert memory.close(timeout=2)
    assert memory.local.count() == 2


@pytest.mark.asyncio
@pytest.mark.parametrize("borrowed", [False, True])
async def test_writer_closes_only_its_owned_episode_worker(tmp_path, monkeypatch, borrowed):
    from phone_agent_gateway.ai_bridge.memory.memory_writer import ValidatedMemoryWriter

    monkeypatch.delenv("PHONE_AGENT_GRAPHITI_URL", raising=False)
    manager = LayeredMemoryManager(tmp_path / "caller.json")
    external = AsyncIdentityMemory(LocalEpisodeStore(tmp_path / "borrowed.sqlite3")) if borrowed else None
    writer = ValidatedMemoryWriter(manager, identity_memory=external)
    worker = writer._long_term_memory()
    try:
        await writer.close()
        assert worker._worker.is_alive() is borrowed
    finally:
        assert worker.close(timeout=2)


@pytest.mark.asyncio
async def test_closing_during_memory_initialization_cannot_revive_a_worker(tmp_path, monkeypatch):
    import asyncio
    import threading

    from phone_agent_gateway.ai_bridge.memory import memory_writer as writer_module

    monkeypatch.delenv("PHONE_AGENT_GRAPHITI_URL", raising=False)
    entered, release = threading.Event(), threading.Event()
    created = []

    def delayed_memory(*args, **kwargs):
        entered.set()
        release.wait(timeout=3)
        memory = AsyncIdentityMemory(*args, **kwargs)
        created.append(memory)
        return memory

    monkeypatch.setattr(writer_module, "AsyncIdentityMemory", delayed_memory)
    writer = writer_module.ValidatedMemoryWriter(LayeredMemoryManager(tmp_path / "caller.json"))
    pending = asyncio.create_task(asyncio.to_thread(writer._long_term_memory))
    try:
        assert await asyncio.to_thread(entered.wait, 1)
        await asyncio.wait_for(writer.close(), timeout=1)
    finally:
        release.set()
    with pytest.raises(RuntimeError, match="closed during initialization"):
        await pending
    assert writer._identity_memory is None
    assert len(created) == 1
    assert created[0].close(timeout=2)


def profile():
    return SimpleNamespace(identity_id='agent', core=SimpleNamespace(name='Alex', role='AI representative',
                                                                   organization='Example', mission='Help callers'))


def test_package_product_and_persona_changes_isolate_memory_but_voice_does_not():
    identity = profile()
    task = {'id': 'support', 'objective': 'Help with Product A', 'knowledge': {'product': 'A'}}
    baseline = memory_namespace(identity, task, 'package_a')
    assert baseline != memory_namespace(identity, task, 'package_b')
    assert baseline != memory_namespace(identity, {**task, 'knowledge': {'product': 'B'}}, 'package_a')
    identity.voice = 'another voice'
    assert baseline == memory_namespace(identity, task, 'package_a')
    identity.core.name = 'Robin'
    assert baseline != memory_namespace(identity, task, 'package_a')


def test_legacy_and_other_scopes_are_preserved_but_never_implicitly_imported(tmp_path):
    base = LayeredMemoryManager(tmp_path / 'memory.json')
    base.complete_call_session('+15555550123', 'Legacy product details')
    before = base.storage_path.read_bytes()
    a = base.scoped('a' * 64)
    b = base.scoped('b' * 64)
    a.complete_call_session('+15555550123', 'Product A summary')
    a.update_preferences('+15555550123', {'plan': 'A'})
    assert not b.get_caller_memory('+15555550123')['past_call_summary']
    assert not b.get_caller_memory('+15555550123')['preferences']
    assert base.storage_path.read_bytes() == before
    assert base.scoped('a' * 64).get_caller_memory('+15555550123')['past_call_summary'] == 'Product A summary'
    assert a.scoped('b' * 64).storage_path == b.storage_path


def test_shared_semantic_database_search_still_respects_namespace(tmp_path):
    store = LocalEpisodeStore(tmp_path / 'episodes.sqlite3')
    a = AsyncIdentityMemory.__new__(AsyncIdentityMemory)
    b = AsyncIdentityMemory.__new__(AsyncIdentityMemory)
    a.namespace, b.namespace = 'package_a', 'package_b'
    a.local = b.local = store
    store.add(MemoryEpisode(episode_id='episode1', group_id=a._caller_scope('caller'), role='caller',
                            content='ProductA preference', language='en', task_id='support',
                            reference_time='2026-09-06T00:00:00+00:00'))
    assert a.search_local('caller', 'ProductA')
    assert not b.search_local('caller', 'ProductA')
