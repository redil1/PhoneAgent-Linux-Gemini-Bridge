"""Behavioral regressions from the September 6 00:09 call; no external actions."""

import pytest

from phone_agent_gateway.ai_bridge.agent_policy import AgentPolicyRuntime
from phone_agent_gateway.ai_bridge.guardrails.permission_gate import PermissionGate
from phone_agent_gateway.ai_bridge.guardrails.personality_judge import PersonalityFidelityJudge
from phone_agent_gateway.ai_bridge.human_speech import normalize_for_speech
from phone_agent_gateway.ai_bridge.turn_continuity import looks_semantically_incomplete


@pytest.mark.parametrize('text', [
    'Yes, yes, it seems very', 'It seems very.', 'That sounds extremely',
    'Oui, ça semble très', 'Cela paraît particulièrement.',
])
def test_unfinished_evaluation_keeps_the_turn_open(text):
    assert looks_semantically_incomplete(text)


@pytest.mark.parametrize('text', [
    'It seems very expensive.', 'Yes, it sounds good.', 'Oui, cela paraît très cher.',
    'Yes, please.', 'No, thank you.', 'Oui.',
])
def test_complete_evaluation_and_short_answers_are_not_held(text):
    assert not looks_semantically_incomplete(text)


@pytest.mark.asyncio
@pytest.mark.parametrize('text', ['Yes, yes, it seems very', 'Oui, ça semble très'])
async def test_exhausted_incomplete_evaluation_cannot_become_budget_acceptance(text):
    policy = AgentPolicyRuntime(caller_id='unknown:call-0009', task_id='general_conversation',
                                language='en-US', memory_enabled=False)
    try:
        await policy.observe_transcription(text)
        spoken, stop = policy.guard_sentence('Since the investment aligns, shall we proceed?', is_first=True)
        assert stop
        assert 'investment aligns' not in spoken
        assert '?' in spoken
    finally:
        await policy.close()


@pytest.mark.parametrize('text', [
    "I've initiated the dispatch of your executive summary.",
    'I am sending those documents to your email address now.',
    "I've registered you in the database.",
    'Your email has been dispatched.',
    "J'ai lancé l'envoi de votre récapitulatif.",
    'Je vous envoie le lien maintenant.',
    'Votre inscription a été enregistrée.',
])
def test_unsupported_action_paraphrases_are_blocked(text):
    spoken, violations = PermissionGate.enforce_spoken_response(text, language='en', messaging_available=False)
    assert violations
    assert spoken != text


@pytest.mark.parametrize('text', [
    "I haven't sent the email yet.", "I have not initiated the dispatch.",
    'Would you like me to send it?', 'I am sending you back to the main topic.',
    "Je n'ai pas envoyé le message.", "Je ne vous envoie rien pour le moment.",
    'You said the email was sent.',
])
def test_questions_denials_and_reported_speech_are_not_action_claims(text):
    spoken, violations = PermissionGate.enforce_spoken_response(text, language='en')
    assert not violations
    assert spoken == text


def test_dispatch_acceptance_does_not_prove_delivery():
    _, violations = PermissionGate.enforce_spoken_response(
        'Your email has been delivered.', language='en', verified_actions={'sent'})
    assert violations
    spoken, violations = PermissionGate.enforce_spoken_response(
        'Your email has been dispatched.', language='en', verified_actions={'sent'})
    assert not violations and 'dispatched' in spoken


@pytest.mark.parametrize(('text', 'language', 'expected'), [
    ('3,500.00 dollars', 'en', 'three thousand five hundred dollars'),
    ('$3,500.25', 'en', 'three thousand five hundred dollars and twenty-five cents'),
    ('3 500,25 euros', 'fr', 'trois mille cinq cents euros et vingt-cinq centimes'),
    ('3\u202f500,00 EUR', 'fr', 'trois mille cinq cents euros'),
    ('71 euros', 'fr', 'soixante et onze euros'),
    ('81 euros', 'fr', 'quatre-vingt-un euros'),
    ('17 dollars', 'en', 'seventeen dollars'),
])
def test_currency_is_spoken_without_numeric_corruption(text, language, expected):
    assert normalize_for_speech(text, language) == expected


@pytest.mark.parametrize('identifier', [
    'first-last@example-company.test', 'first_last@example.test',
    'https://example.test/plan-25?order_id=123&token=a_b',
])
def test_identifiers_survive_sanitization_and_number_normalization(identifier):
    cleaned = PermissionGate.sanitize_for_telephony('Use ' + identifier)
    assert identifier in cleaned
    assert identifier in normalize_for_speech(cleaned, 'en')


def test_standalone_evaluation_flags_dispatch_without_tool_evidence():
    result = PersonalityFidelityJudge().evaluate_turn(
        caller_input='Please send it.', ai_response="I've initiated the dispatch.",
        task_contract={'id': 'demo'})
    assert not result.passed
    assert result.overall_score < 100


@pytest.mark.asyncio
async def test_streamed_evaluation_reports_limited_scope_and_actual_evidence():
    events = []
    policy = AgentPolicyRuntime(caller_id='unknown:call-0009', task_id='general_conversation',
                                language='en-US', memory_enabled=False, event_sink=events.append)
    try:
        await policy.observe_transcription('Please send it.')
        rid = policy.begin_streamed_response()
        spoken, stop = policy.guard_sentence("I've initiated the dispatch.", is_first=True, response_id=rid)
        assert stop
        await policy.finalize_streamed_response(rid, spoken)
        result = next(e for e in events if e['type'] == 'evaluation')
        assert result['evaluation_scope'] == 'heuristic_text_checks'
        assert not result['passed']
        assert result['verified_actions'] == []
    finally:
        await policy.close()
