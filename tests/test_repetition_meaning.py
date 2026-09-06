"""Similar wording can convey a materially different answer."""
from __future__ import annotations

import pytest

from phone_agent_gateway.ai_bridge.agent_policy import AgentPolicyRuntime


@pytest.mark.parametrize(('previous','correction'),[
    ('The monthly price is thirty nine dollars.','The monthly price is fifty nine dollars.'),
    ('Support is available on Monday morning.','Support is available on Tuesday morning.'),
    ('You can use this plan on your television.','You cannot use this plan on your television.'),
    ('Your contact name is Marie Dupont.','Your contact name is Maria Dupont.'),
    ('You can cancel the renewal, not the subscription.','You can cancel the subscription, not the renewal.'),
    ('Le prix mensuel est de trente neuf euros.','Le prix mensuel est de cinquante neuf euros.'),
    ('Vous pouvez utiliser ce service sur votre téléviseur.','Vous ne pouvez pas utiliser ce service sur votre téléviseur.'),
    ('You can watch the included films.','You can watch the included films, but that channel is unavailable.'),
])
def test_corrections_and_new_information_are_not_vetoed_by_similarity(previous,correction):
    runtime=AgentPolicyRuntime(caller_id='unknown:meaning-test',task_id='customer_support',language='en-US',memory_enabled=False)
    runtime._remember_spoken(previous)
    assert not runtime._is_repeat(correction)


def test_confirmed_exact_repeat_and_current_draft_loop_are_still_detected():
    runtime=AgentPolicyRuntime(caller_id='unknown:meaning-test',task_id='customer_support',language='en-US',memory_enabled=False)
    sentence='Which programs do you watch most often?'
    runtime._remember_spoken(sentence)
    assert runtime._is_repeat(sentence.upper())
    response=runtime.begin_streamed_response()
    runtime._remember_spoken('A second sentence in this draft.',response)
    assert runtime._is_repeat('A second sentence in this draft.',response)
