"""Consent belongs to an explicit action or an actually delivered proposal."""

import pytest

from phone_agent_gateway.ai_bridge.agent_policy import AgentPolicyRuntime
from phone_agent_gateway.ai_bridge.consent import affirmative, proposal_from_speech, resolve_consent
from phone_agent_gateway.ai_bridge.guardrails.permission_gate import PermissionGate
from phone_agent_gateway.ai_bridge.tasks.call_state import TaskRuntime
from phone_agent_gateway.ai_bridge.tasks.task_engine import TaskEngine


@pytest.mark.parametrize('text', ['Yes, of course, go ahead.', 'Yes, please.', "Oui, bien sûr, allez-y."])
def test_natural_affirmative_is_understood(text):
    assert affirmative(text)


@pytest.mark.parametrize("text", ["Can you register me in the database?", "Register me, please.",
                                   "Pouvez-vous m\u2019inscrire dans votre base ?", "Enregistrez-moi, s'il vous plaît."])
def test_registration_request_has_only_registration_scope(text):
    assert resolve_consent(text, None) == {"registration"}


@pytest.mark.parametrize("text", ["Can you not register me?", "Do not register me.",
                                   "Ne m'inscrivez pas.", "He said register me."])
def test_negated_or_reported_registration_is_not_consent(text):
    assert not resolve_consent(text, None)


@pytest.mark.parametrize(('proposal', 'expected'), [
    ('Do you have a moment to discuss this?', 'continue'),
    ('The investment is 3500 dollars. Does that fit your budget?', 'budget'),
    ('Would you like the details sent by email?', 'email'),
    ('Is your email address correct?', None),
    ('What is your budget?', None),
    ('Quel budget avez-vous ?', None),
    ('May I send the details on WhatsApp?', 'whatsapp'),
    ('Puis-je envoyer le lien par courriel ?', 'email'),
    ('Would you like to buy this package?', 'purchase'),
])
def test_yes_is_bound_to_a_single_proposal(proposal, expected):
    pending = proposal_from_speech(proposal, 'response-1')
    assert resolve_consent('Yes, of course, go ahead.', pending) == (frozenset({expected}) if expected else frozenset())


@pytest.mark.parametrize('text', [
    "Don't send anything to WhatsApp.", 'Please do not send it.',
    'I have not received anything on WhatsApp.', 'WhatsApp?',
    'He said send the email.', "N'envoyez rien sur WhatsApp.",
    'Yes, if you can guarantee it.', 'Say yes to that.',
])
def test_negation_reports_and_conditions_are_not_consent(text):
    pending = proposal_from_speech('May I send the details on WhatsApp?', 'r-1')
    assert resolve_consent(text, pending, frozenset({'whatsapp'})) == frozenset()


def test_channel_switch_and_alternatives_do_not_leak_authority():
    email = proposal_from_speech('May I send it by email?', 'r-1')
    assert resolve_consent('Yes, please.', email) == {'email'}
    assert resolve_consent('Send it by WhatsApp.', email) == {'whatsapp'}
    either = proposal_from_speech('Should I send it by email or WhatsApp?', 'r-2')
    assert resolve_consent('Yes.', either) == frozenset()


def test_generic_send_uses_only_unambiguous_context():
    assert resolve_consent('Send me the offer.', None, frozenset({'whatsapp'})) == {'whatsapp'}
    assert not resolve_consent('Send me the offer.', None, frozenset({'whatsapp', 'email'}))
    assert resolve_consent('Please resend it on WhatsApp.', None) == {'whatsapp'}
    assert resolve_consent('Renvoyez le lien par courriel.', None) == {'email'}


def test_a_failed_send_statement_does_not_turn_a_listening_question_into_consent():
    proposal = proposal_from_speech('I cannot send on WhatsApp. Do you have a moment to discuss this?', 'r-1')
    assert resolve_consent('Yes.', proposal) == {'continue'}


def test_scoped_task_slot_roundtrips_and_never_uses_a_broad_keyword_as_consent():
    contract = TaskEngine.validate_contract({
        'id': 'any_business', 'title': 'Any business', 'objective': 'Help with a request.',
        'inputs_required': [{'id': 'send_summary', 'consent_scope': 'whatsapp', 'detect': ['yes|whatsapp']}],
    })
    assert contract['inputs_required'][0]['consent_scope'] == 'whatsapp'
    task = TaskRuntime(contract)
    assert not task.observe_caller_turn('Yes, go ahead.').changed
    assert not task.observe_caller_turn('WhatsApp?').changed
    assert task.observe_caller_turn('Yes, please.', consent_scopes=frozenset({'whatsapp'})).state_delta == {'send_summary': 'Yes, please.'}


def test_custom_unscoped_slot_cannot_capture_a_bare_yes_as_business_data():
    task = TaskRuntime({'inputs_required': [{'id': 'custom_deal', 'detect': ['yes|go ahead']}]})
    assert not task.observe_caller_turn('Yes, of course, go ahead.').changed


def test_whatsapp_availability_does_not_enable_email_promises():
    spoken, violations = PermissionGate.enforce_spoken_response(
        'I can send the summary to your email.', language='en', messaging_available=True,
        available_messaging_channels=frozenset({'whatsapp'}))
    assert violations
    assert "can't send by email" in spoken
    assert 'whatsapp' in spoken


def test_unavailable_whatsapp_does_not_offer_an_unavailable_email_fallback():
    spoken, violations = PermissionGate.enforce_spoken_response(
        'May I send you an email instead?', language='en', messaging_available=False,
        available_messaging_channels=frozenset())
    assert violations
    assert "can't send by email" in spoken
    assert 'I can use' not in spoken


@pytest.mark.asyncio
async def test_playback_bound_yes_does_not_advance_budget_or_checkout():
    policy = AgentPolicyRuntime(caller_id='unknown:consent', task_id='general_conversation',
                                language='en-US', memory_enabled=False)
    policy.available_tools = {'whatsapp_send_text_current_customer'}
    policy.task = TaskRuntime({'inputs_required': [
        {'id': 'budget_alignment', 'detect': ['yes|go ahead']},
        {'id': 'closing_authorization', 'detect': ['yes|go ahead|whatsapp']},
    ]})
    try:
        _, _, rid = await policy.finalize_response_with_identity('Do you have a moment to discuss your goals?')
        await policy.playback_started(response_id=rid)
        await policy.playback_stopped(delivered_frames=100)
        policy.observe_speech_started()
        await policy.observe_transcription('Yes, of course, go ahead.')
        assert policy._current_consent == {'continue'}
        assert not policy.task.state
        assert not policy.has_current_consent('whatsapp')
    finally:
        await policy.close()


@pytest.mark.asyncio
@pytest.mark.parametrize('delivery', ['generated', 'playing', 'interrupted', 'unverified', 'completed'])
async def test_short_yes_requires_a_completed_send_proposal(delivery):
    policy = AgentPolicyRuntime(caller_id='unknown:consent', task_id='general_conversation',
                                language='en-US', memory_enabled=False)
    policy.available_tools = {'whatsapp_send_text_current_customer'}
    try:
        _, _, rid = await policy.finalize_response_with_identity('May I send the details on WhatsApp?')
        if delivery != 'generated':
            await policy.playback_started(response_id=rid)
        if delivery == 'interrupted':
            await policy.mark_playback_interrupted()
        if delivery in {'completed', 'interrupted'}:
            await policy.playback_stopped(delivered_frames=100)
        if delivery == 'unverified':
            await policy.playback_stopped(delivered_frames=None)
        policy.observe_speech_started()
        await policy.observe_transcription('Yes, please.')
        assert policy.has_current_consent('whatsapp') is (delivery == 'completed')
        await policy.observe_transcription('Yes.')
        assert not policy.has_current_consent('whatsapp'), 'A proposal cannot authorize a later unrelated yes'
    finally:
        await policy.close()


@pytest.mark.asyncio
async def test_old_playback_completion_cannot_authorize_a_new_turn():
    policy = AgentPolicyRuntime(caller_id='unknown:consent', task_id='general_conversation',
                                language='en-US', memory_enabled=False)
    try:
        _, _, rid = await policy.finalize_response_with_identity('May I send the details on WhatsApp?')
        await policy.playback_started(response_id=rid)
        await policy.observe_transcription('I want to discuss something else.')
        await policy.playback_stopped(delivered_frames=100)
        await policy.observe_transcription('Yes.')
        assert not policy.has_current_consent('whatsapp')
    finally:
        await policy.close()
