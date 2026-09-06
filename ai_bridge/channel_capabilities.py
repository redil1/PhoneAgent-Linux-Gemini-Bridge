"""Channel-specific declared capabilities shared by package and live runtime checks."""

from __future__ import annotations

import re
from typing import Any

from .consent import mentioned_channels, normalize

MESSAGE_CAPABILITIES = frozenset({'whatsapp.send', 'email.send', 'sms.send'})


def builtin_capabilities(name: str) -> frozenset[str]:
    if name in {
        'whatsapp_send_text_current_customer', 'whatsapp_reply_current_customer',
        'whatsapp_send_media_current_customer', 'whatsapp_send_contact_current_customer',
        'whatsapp_send_location_current_customer',
    }:
        return frozenset({'whatsapp.send'})
    return frozenset()


def assigned_to_task(task_ids: list[str], task_id: str) -> bool:
    return not task_ids or '*' in task_ids or task_id in task_ids


def promised_messaging_channels(text: str) -> frozenset[str]:
    """Detect explicit positive send/delivery requirements, not channel mentions.

    This bounded text check complements typed required_capabilities. It is not
    proof that arbitrary natural-language promises have all been understood.
    """
    required: set[str] = set()
    for sentence in re.split(r'\n|(?<=[.!?])\s+', text):
        normalized = normalize(sentence)
        if re.search(r'\b(?:never|not|cannot|can t|unable|unavailable|disabled|no|pas|jamais|indisponible)\b', normalized):
            continue
        if re.search(r'\b(?:send|sent|sending|dispatch|delivery|deliver|forward|emailing|envoyer|envoie|envoi|envoyez|transmettre|livraison)\b|\bemail (?:you|it|the|those)\b', normalized):
            required.update(mentioned_channels(sentence))
    return frozenset(required)


def package_messaging_requirements(package: Any) -> frozenset[str]:
    """Read authored positive instructions, excluding adversarial/bad examples."""
    task = package.task
    texts: list[str] = [package.objective, package.runtime.system_prompt,
                       package.identity.core.mission]

    def visit(value: Any) -> None:
        if isinstance(value, str):
            texts.append(value)
        elif isinstance(value, list):
            for child in value:
                visit(child)
        elif isinstance(value, dict):
            for key, child in value.items():
                if key not in {'anti_response', 'bad', 'detect', 'forbidden_contains'}:
                    visit(child)

    visit(task)
    texts.extend(example.ideal_response for example in package.identity.examples)
    texts.extend(skill.instructions for skill in package.skills)
    texts.extend(block.content for block in package.memory_blocks)
    declared = set(task.get('required_capabilities', []))
    for text in texts:
        declared.update(channel + '.send' for channel in promised_messaging_channels(text))
    return frozenset(declared)
