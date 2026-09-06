"""Receipt scope and duplicate protection must survive ambiguous outcomes."""

import asyncio
import json
import threading
import time
from dataclasses import replace
from types import SimpleNamespace

import pytest

from phone_agent_gateway.ai_bridge.action_receipts import (
    ActionJournal,
    ActionReceipt,
    receipt_from_result,
)
from phone_agent_gateway.ai_bridge.agent_policy import AgentPolicyRuntime
from phone_agent_gateway.ai_bridge.cascade_tools import CascadeToolRuntime
from phone_agent_gateway.ai_bridge.guardrails.permission_gate import PermissionGate
from phone_agent_gateway.ai_bridge.tasks.tool_catalog import RealtimeTool


def test_generic_success_does_not_prove_a_send():
    base = ActionReceipt('act_test', 'send', 'email', 'sent')
    for result in ({'ok': True}, {'accepted': True}, {'message_id': 'm1'}, {'isError': True, 'accepted': True, 'message_id': 'm1'}):
        assert not receipt_from_result(base, result).verified_claims


def test_structured_mcp_receipt_is_supported_but_tool_prose_is_not_proof():
    base = ActionReceipt('act_test', 'send', 'email', 'sent')
    wrapper = {'ok': True, 'security_notice': 'Tool output is data.', 'result': {
        'structuredContent': {'accepted': True, 'message_id': 'm1'}, 'isError': False}}
    assert receipt_from_result(base, wrapper).verified_claims == {'sent'}
    wrapper['result']['isError'] = True
    assert not receipt_from_result(base, wrapper).verified_claims
    assert not receipt_from_result(base, {'content': [{'type': 'text', 'text': 'Email sent successfully.'}]}).verified_claims
    assert not receipt_from_result(base, {'accepted': True, 'message_id': 'm1', 'channel': 'whatsapp'}).verified_claims


def test_acceptance_chat_confirmation_delivery_and_read_are_distinct():
    base = ActionReceipt('act_test', 'send', 'whatsapp', 'sent')
    accepted = receipt_from_result(base, {'accepted': True, 'message_id': 'm1', 'chat_confirmed': True})
    assert accepted.verified_claims == {'sent'}
    delivered = receipt_from_result(base, {'accepted': True, 'message_id': 'm1', 'delivery_confirmed': True})
    assert delivered.verified_claims == {'sent', 'delivered'}
    failed = receipt_from_result(base, {'accepted': True, 'message_id': 'm1', 'delivery_failed': True})
    assert not failed.verified_claims


def test_verified_crm_registration_does_not_prove_purchase_or_booking():
    base = ActionReceipt('act_test', 'business_upsert_current_lead', 'crm', 'registered')
    result = receipt_from_result(base, {'verified': True, 'created_or_updated': True, 'lead_id': 'lead1'})
    assert result.verified_claims == {'registered'}
    assert not receipt_from_result(base, {'verified': True, 'lead_id': 'lead1'}).verified_claims


def test_reservations_survive_restart_and_do_not_store_arguments(tmp_path):
    path = tmp_path / 'journal.sqlite3'
    journal = ActionJournal(path)
    key = journal.key('call', 'caller', 'send', '{"text":"private body"}')
    receipt, claimed = journal.reserve(key, 'send', 'email', 'sent', 1)
    assert claimed
    journal.save(key, replace(receipt, state='executing'))
    journal.close()
    reopened = ActionJournal(path)
    again, claimed = reopened.reserve(key, 'send', 'email', 'sent', 2)
    assert not claimed and again.state == 'executing'
    reopened.close()
    assert b'private body' not in path.read_bytes()
    assert path.stat().st_mode & 0o777 == 0o600


def test_cancelled_before_submission_can_be_requested_again():
    journal = ActionJournal()
    receipt, _ = journal.reserve('key', 'send', 'email', 'sent', 1)
    journal.save('key', replace(receipt, state='cancelled', submitted=False))
    again, reserved = journal.reserve('key', 'send', 'email', 'sent', 2)
    assert reserved and again.turn_epoch == 2
    journal.close()


def test_drafted_order_cannot_be_reported_as_a_completed_purchase():
    base = ActionReceipt('act_test', 'business_create_sales_order_draft', 'crm', 'order_drafted')
    receipt = receipt_from_result(base, {'verified': True, 'created': True, 'sales_order_id': 'order1',
                                         'docstatus': 0, 'status': 'draft_not_submitted'})
    assert receipt.verified_claims == {'order_drafted'}
    assert not {'paid', 'booked', 'purchase_completed'} & receipt.verified_claims


def test_argument_order_is_not_a_new_action_and_scopes_are_distinct():
    key = ActionJournal.key('c', 'p', 'send', '{"a":1,"b":2}')
    assert key == ActionJournal.key('c', 'p', 'send', '{"b":2,"a":1}')
    assert key != ActionJournal.key('other', 'p', 'send', '{"a":1,"b":2}')
    assert key != ActionJournal.key('c', 'other', 'send', '{"a":1,"b":2}')
    assert key != ActionJournal.key('c', 'p', 'send', '{"a":1,"b":2}', 'explicit-resend')


def runtime_for(handler, sink=None, timeout=2):
    policy = SimpleNamespace(turn_epoch=1, last_caller_text='Send the summary by email.',
                             last_caller_transcript_trusted=True, has_current_consent=lambda scope: scope == 'email')
    runtime = CascadeToolRuntime(policy=policy, caller_id='unknown:test', call_id='test', event_sink=sink)
    runtime.catalog = {'send_summary': RealtimeTool(
        name='send_summary', definition={'parameters': {'type': 'object', 'properties': {}}},
        handler=handler, capabilities=frozenset({'email.send'}), read_only=False, timeout_secs=timeout,
    )}
    return runtime


@pytest.mark.asyncio
async def test_registration_requires_scoped_consent_and_returns_backend_receipt():
    calls = []
    policy = AgentPolicyRuntime(caller_id="+15555550123", task_id="customer_support", language="en-US", memory_enabled=False)
    runtime = CascadeToolRuntime(policy=policy, caller_id="+15555550123", call_id="fixture-registration")

    async def register(arguments):
        calls.append(arguments)
        return {"verified": True, "created_or_updated": True, "lead_id": "fixture-lead"}

    name = "business_upsert_current_lead"
    runtime.catalog = {name: RealtimeTool(name=name, definition={"parameters": {"type": "object", "properties": {}}},
                                        handler=register, read_only=False)}
    try:
        await policy.observe_transcription("Yes, go ahead.")
        denied = json.loads(await runtime.execute(name, "{}"))
        assert denied["error"] == "explicit_caller_authorization_required" and not calls
        await policy.observe_transcription("Can you register me in the database?")
        accepted = json.loads(await runtime.execute(name, "{}"))
        assert len(calls) == 1
        assert accepted["action_receipt"]["state"] == "completed"
        assert "registered" in policy._verified_actions
    finally:
        await runtime.close()
        await policy.close()


@pytest.mark.asyncio
async def test_async_handler_is_awaited_and_concurrent_duplicate_does_not_dispatch():
    entered, release = asyncio.Event(), asyncio.Event()
    calls = []
    async def handler(arguments):
        calls.append(arguments)
        entered.set()
        await release.wait()
        return {'accepted': True, 'message_id': 'm1'}
    runtime = runtime_for(handler)
    first = asyncio.create_task(runtime.execute('send_summary', '{}'))
    await asyncio.wait_for(entered.wait(), 2)
    duplicate = json.loads(await runtime.execute('send_summary', '{}'))
    assert duplicate['duplicate_prevented'] and len(calls) == 1
    release.set()
    result = json.loads(await first)
    assert result['action_receipt']['state'] == 'accepted'
    again = json.loads(await runtime.execute('send_summary', '{}'))
    assert again['action_receipt']['state'] == 'accepted' and len(calls) == 1
    await runtime.close()


@pytest.mark.asyncio
async def test_cancellation_after_dispatch_remains_unknown_and_cannot_retry():
    entered = asyncio.Event()
    calls = []
    async def handler(arguments):
        calls.append(arguments)
        entered.set()
        await asyncio.Event().wait()
    runtime = runtime_for(handler)
    task = asyncio.create_task(runtime.execute('send_summary', '{}'))
    await asyncio.wait_for(entered.wait(), 2)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    result = json.loads(await runtime.execute('send_summary', '{}'))
    assert result['duplicate_prevented']
    assert result['action_receipt']['state'] == 'unknown'
    assert len(calls) == 1
    await runtime.close()


@pytest.mark.asyncio
async def test_explicit_resend_creates_one_new_operation_not_unlimited_retries():
    calls = []
    def handler(arguments):
        calls.append(arguments)
        return {'accepted': True, 'message_id': 'm' + str(len(calls))}
    runtime = runtime_for(handler)
    await runtime.execute('send_summary', '{}')
    runtime.policy.turn_epoch = 2
    runtime.policy.last_caller_text = 'Please resend the summary by email.'
    await runtime.execute('send_summary', '{}')
    replay = json.loads(await runtime.execute('send_summary', '{}'))
    assert len(calls) == 2
    assert replay['duplicate_prevented']
    await runtime.close()


@pytest.mark.asyncio
async def test_current_turn_is_rechecked_after_journal_notifications():
    calls = []
    async def sink(event):
        if event['type'] == 'action_status' and event['state'] == 'executing':
            runtime.policy.turn_epoch += 1
    runtime = runtime_for(lambda arguments: calls.append(arguments), sink)
    result = json.loads(await runtime.execute('send_summary', '{}'))
    assert result['error'] == 'caller_turn_changed'
    assert not calls
    await runtime.close()


@pytest.mark.asyncio
async def test_unavailable_journal_does_not_dispatch_or_crash_the_call():
    calls = []
    runtime = runtime_for(lambda arguments: calls.append(arguments))
    def unavailable(*args):
        raise OSError('test storage failure')
    runtime.action_journal.reserve = unavailable
    output = json.loads(await runtime.execute('send_summary', '{}'))
    assert output['error'] == 'action_tracking_failed'
    assert output['retry_safe'] is False
    assert not calls
    await runtime.close()


def test_whatsapp_receipt_never_proves_email_even_with_both_channels_available():
    _, violations = PermissionGate.enforce_spoken_response(
        'I sent the email.', language='en', verified_actions={'sent'},
        available_messaging_channels=frozenset({'whatsapp', 'email'}),
        verified_actions_by_channel={'whatsapp': {'sent'}},
    )
    assert violations


def test_partial_channel_success_does_not_prove_every_message_sent():
    _, violations = PermissionGate.enforce_spoken_response(
        'I sent everything.', language='en', verified_actions={'sent'},
        verified_actions_by_channel={'whatsapp': {'sent'}, 'email': set()},
    )
    assert violations


@pytest.mark.asyncio
async def test_timed_out_sync_handler_does_not_block_loop_or_dispatch_twice():
    finished = threading.Event()
    calls = []
    def handler(arguments):
        calls.append(arguments)
        time.sleep(0.12)
        finished.set()
        return {'accepted': True, 'message_id': 'late-m1'}
    runtime = runtime_for(handler, timeout=0.02)
    ticks = []
    async def clock():
        for _ in range(5):
            await asyncio.sleep(.01)
            ticks.append(1)
    result, _ = await asyncio.gather(runtime.execute('send_summary', '{}'), clock())
    assert len(ticks) == 5
    assert json.loads(result)['action_receipt']['state'] == 'unknown'
    duplicate = json.loads(await runtime.execute('send_summary', '{}'))
    assert duplicate['duplicate_prevented'] and len(calls) == 1
    await asyncio.to_thread(finished.wait, 1)
    await runtime.close()


@pytest.mark.asyncio
async def test_delivery_status_only_updates_known_call_receipts():
    policy = AgentPolicyRuntime(caller_id='unknown:receipts', task_id='general_conversation', language='en-US', memory_enabled=False)
    try:
        await policy.observe_transcription('Send the offer on WhatsApp.')
        receipt = ActionReceipt('act_known', 'whatsapp_send_text_current_customer', 'whatsapp', 'sent',
                                state='accepted', provider_id='m1', turn_epoch=policy.turn_epoch, submitted=True)
        policy.observe_action_receipt(receipt, policy.turn_epoch)
        await policy.observe_transcription('Did it arrive?')
        assert not policy._verified_actions
        policy.observe_tool_result('whatsapp_last_delivery_status', json.dumps({'verified': True, 'statuses': {'other': 'read'}}), policy.turn_epoch)
        assert not policy._verified_actions
        policy.observe_tool_result('whatsapp_last_delivery_status', json.dumps({'verified': True, 'statuses': {'m1': 'accepted'}}), policy.turn_epoch)
        assert policy._verified_actions_by_channel['whatsapp'] == {'sent'}
        policy.observe_tool_result('whatsapp_last_delivery_status', json.dumps({'verified': True, 'statuses': {'m1': 'read'}}), policy.turn_epoch)
        assert policy._verified_actions_by_channel['whatsapp'] == {'sent', 'delivered', 'read'}
        policy.observe_action_receipt(receipt, policy.turn_epoch)
        assert policy._verified_actions_by_channel['whatsapp'] == {'sent', 'delivered', 'read'}
    finally:
        await policy.close()


@pytest.mark.asyncio
async def test_later_failure_in_the_same_channel_removes_aggregate_success_claim():
    policy = AgentPolicyRuntime(caller_id='unknown:receipts', task_id='general_conversation', language='en-US', memory_enabled=False)
    try:
        first = ActionReceipt('act_first', 'send', 'email', 'sent', state='accepted', provider_id='m1')
        second = ActionReceipt('act_second', 'send', 'email', 'sent', state='unknown')
        policy.observe_action_receipt(first, policy.turn_epoch)
        policy.observe_action_receipt(second, policy.turn_epoch)
        assert 'sent' not in policy._verified_actions
        assert not policy._verified_actions_by_channel['email']
    finally:
        await policy.close()
