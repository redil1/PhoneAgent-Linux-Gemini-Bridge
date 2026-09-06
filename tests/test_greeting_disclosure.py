"""Configured greetings must disclose the configured AI identity before TTS."""

import pytest

from phone_agent_gateway.ai_bridge.agent_policy import AgentPolicyRuntime
from phone_agent_gateway.ai_bridge.identity.greetings import (
    ensure_greeting_disclosure,
    has_ai_disclosure,
)


@pytest.mark.parametrize(('text', 'language'), [
    ("Hello Alex, this is Morgan with Example Company. I'm calling about your order.", 'en'),
    ("Bonjour Alex, ici Morgan de chez Exemple. Je vous appelle au sujet de votre commande.", 'fr'),
])
def test_configured_purpose_is_preserved_and_identity_added(text, language):
    output = ensure_greeting_disclosure(text, name='Morgan', disclosure='', language=language)
    assert has_ai_disclosure(output)
    assert output.count('Morgan') == 1
    assert output.startswith(('Hello Alex', 'Bonjour Alex'))
    assert ('order' if language == 'en' else 'commande') in output


def test_product_ai_mention_is_not_self_disclosure():
    text = "Hello, this is Morgan. Our AI infrastructure reduces overhead."
    assert not has_ai_disclosure(text)
    output = ensure_greeting_disclosure(text, name='Morgan', disclosure='', language='en')
    assert 'an AI phone representative' in output
    product = "Hello, this is Morgan. Our AI assistant automates your workflow."
    assert not has_ai_disclosure(product)
    assert 'an AI phone representative' in ensure_greeting_disclosure(product, name='Morgan', disclosure='', language='en')


def test_truthful_opening_is_not_duplicated():
    text = "Hello, this is Morgan, the AI assistant with Example Company."
    assert ensure_greeting_disclosure(text, name='Morgan', disclosure='', language='en') == text


@pytest.mark.parametrize('text', ["I'm Morgan, a real human, not an AI.", "Hello, this is Someone Else."])
def test_contradictory_identity_uses_approved_disclosure(text):
    approved = "I'm Morgan, the AI assistant with Example Company."
    assert ensure_greeting_disclosure(text, name='Morgan', disclosure=approved, language='en') == approved


@pytest.mark.asyncio
async def test_greeting_finalization_cannot_bypass_disclosure_or_material_evidence():
    policy = AgentPolicyRuntime(caller_id='unknown:greeting-check', task_id='general_conversation',
                                language='en-US', memory_enabled=False)
    try:
        policy.task_contract['knowledge'] = {'certification': 'SOC2 Type II certified.'}
        name = policy.persona_compiler.effective_identity['name']
        spoken, evaluation, _ = await policy.finalize_response_with_identity(
            f"Hello, this is {name}. We are SOC2 Type II certified. Is now a good time?", response_kind='greeting')
        assert has_ai_disclosure(spoken)
        assert 'SOC2' not in spoken
        assert 'good time' in spoken
        assert not evaluation.passed
    finally:
        await policy.close()
