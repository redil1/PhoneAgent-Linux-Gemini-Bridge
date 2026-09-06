"""Common completed-action wording must still require a tool receipt."""

import pytest

from phone_agent_gateway.ai_bridge.guardrails.permission_gate import PermissionGate


@pytest.mark.parametrize(
    "text",
    [
        "I've just sent that link to your WhatsApp.",
        "I have already sent the message.",
        "We have now sent the details.",
        "Je vous ai déjà envoyé le lien.",
        "Je viens de vous envoyer le lien.",
        "Je viens d'envoyer le lien.",
    ],
)
def test_completed_send_variants_require_and_accept_the_same_receipt(text):
    _, violations = PermissionGate.enforce_spoken_response(
        text, language="en-US", verified_actions=set()
    )
    assert "Claimed an external action without a verified tool result" in violations
    actual, violations = PermissionGate.enforce_spoken_response(
        text, language="en-US", verified_actions={"sent"}
    )
    assert not violations
    assert actual == text


@pytest.mark.parametrize(
    "text",
    ["I haven't sent the message.", "I have not sent the link.", "Je n'ai pas envoyé le lien."],
)
def test_negative_send_statements_are_not_treated_as_completed_actions(text):
    actual, violations = PermissionGate.enforce_spoken_response(
        text, language="en-US", verified_actions=set()
    )
    assert actual == text and not violations
