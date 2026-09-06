"""Keep configured openings truthful about the speaking agent's identity."""

from __future__ import annotations

import re


def has_ai_disclosure(text: str) -> bool:
    normalized = text.replace('\u2019', "'")
    if re.search(r"\b(?:not|pas)\s+(?:an? |une? )?(?:AI|IA|artificial intelligence|intelligence artificielle)\b", normalized, re.I):
        return False
    role = (
        r"\b(?:AI|artificial intelligence)\s+(?:phone |telephone |voice )?(?:assistant|agent|representative|advisor|adviser)\b"
        r"|\b(?:assistant|agent|conseiller|représentant)(?:e)?(?:\s+téléphonique)?\s+(?:IA|intelligence artificielle)\b"
    )
    for clause in re.split(r'[.!?\n]', normalized):
        if re.search(r"\b(?:I'm|I am|this is|it's|je suis|ici|c'est)\b", clause, re.I) and re.search(role, clause, re.I):
            return True
        if re.search(r"\b(?:I'm|I am)\s+(?:an? )?(?:AI|artificial intelligence)\b|\bje suis\s+(?:une? )?(?:IA|intelligence artificielle)\b", clause, re.I):
            return True
    return False


def ensure_greeting_disclosure(text: str, *, name: str, disclosure: str, language: str) -> str:
    """Add a short appositive to the configured introduction, preserving purpose.

    The word AI in a product name or pitch is not an identity disclosure. A
    greeting with a contradictory identity falls back to the approved profile.
    """
    normalized = text.replace('\u2019', "'")
    french = language.lower().startswith('fr')
    approved = disclosure.strip() or (
        f'Je suis {name}, le représentant téléphonique IA.' if french
        else f"I'm {name}, the AI phone representative."
    )
    if re.search(r"\b(?:not|pas)\s+(?:an? |une? )?(?:AI|IA)\b|\b(?:I'm|I am|je suis)\s+(?:a |un |une )?(?:real human|human|humain|personne réelle)\b", normalized, re.I):
        return approved
    introduction = re.search(r"\b(?:this is|it's|i am|i'm|je suis|ici|c'est)\s+" + re.escape(name) + r'\b', normalized, re.I)
    if introduction and has_ai_disclosure(re.split(r'[.!?\n]', normalized[introduction.start():])[0]):
        return text
    if introduction:
        label = ', le représentant téléphonique IA' if french else ', an AI phone representative'
        return text[:introduction.end()] + label + text[introduction.end():]
    # An opening without a recognizable configured introduction still starts
    # with the approved disclosure. Do not retain a different self-identity.
    if re.search(r"\b(?:this is|it's|i am|i'm|je suis|ici|c'est)\s+", normalized, re.I):
        return approved
    return approved + (' ' + text.strip() if text.strip() else '')
