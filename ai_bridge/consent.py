"""Scoped conversational consent; no task stage or generated draft is authorization."""

from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass

SCOPES = frozenset({'continue', 'interest', 'budget', 'whatsapp', 'email', 'sms', 'purchase', 'registration'})
SLOT_SCOPES = {
    'permission': 'continue', 'permission_to_continue': 'continue',
    'budget_alignment': 'budget', 'budget_acceptance': 'budget',
    'closing_authorization': 'purchase', 'purchase_authorization': 'purchase',
    'explicit_interest': 'interest', 'messaging_consent': 'messaging',
    'whatsapp_consent': 'whatsapp', 'email_consent': 'email', 'sms_consent': 'sms',
}


def normalize(text: str) -> str:
    text = ''.join(c for c in unicodedata.normalize('NFKD', text.casefold()) if not unicodedata.combining(c))
    return ' '.join(re.sub(r"[^\w]+", ' ', text).split())


def affirmative(text: str) -> bool:
    """Whole-turn assent only; quotations, conditions and negation are not assent."""
    normalized = normalize(text)
    return bool(re.fullmatch(
        r"(?:(?:yes|yeah|yep|sure|ok|okay|oui|ouais|bien sur|d accord|of course|"
        r"certainly|absolutely|please|s il vous plait|svp|go ahead|allez y|vas y|"
        r"certainement|volontiers)(?: |$)){1,6}", normalized,
    ))


def mentioned_channels(text: str) -> frozenset[str]:
    normalized = normalize(text)
    patterns = {'whatsapp': r'\bwhats ?app\b', 'email': r'\b(?:e ?mail|courriel|courrier electronique)\b',
                'sms': r'\b(?:sms|text message|texto)\b'}
    return frozenset(name for name, pattern in patterns.items() if re.search(pattern, normalized))


def negated_or_reported(text: str) -> bool:
    normalized = normalize(text)
    return bool(re.search(
        r'\b(?:don t|do not|not yet|never|cancel|stop|no|non|ne|pas|jamais|annulez|'
        r'if|unless|si|said|told|say|example|exemple|suppose|pretend|dit|disait|disent)\b', normalized))


@dataclass(frozen=True)
class ConsentProposal:
    response_id: str
    scope: str
    text: str


def proposal_from_speech(text: str, response_id: str) -> ConsentProposal | None:
    """Identify one unambiguous question, only called on completed playback."""
    if text.count('?') != 1 or not text.rstrip().endswith('?'):
        return None
    question = re.split(r'(?<=[.!])\s+', text.strip())[-1]
    normalized = normalize(question)
    if re.match(r'(?:what|which|why|where|when|who|how|quel|quelle|quels|quelles|combien|comment|pourquoi|ou|quand)\b', normalized):
        return None
    # Alternatives need a channel choice; a generic yes cannot select either.
    channels = mentioned_channels(question)
    sending = bool(re.search(r'\b(?:send|sent|share|forward|envoyer|envoie|transmettre|envoi)\b|\b(?:email|text) (?:you|me|it|the)\b', normalized))
    if sending and len(channels) == 1:
        scope = next(iter(channels))
    elif sending:
        return None
    elif re.search(r'\b(?:buy|purchase|place (?:the|an) order|acheter|commander|passer commande)\b', normalized):
        scope = 'purchase'
    elif re.search(r'\b(?:register|registration|inscrire|inscription|enregistrer)\b', normalized):
        scope = 'registration'
    elif re.search(r'\b(?:price|investment|budget|cost|dollars|euros|prix|tarif|cout|investissement)\b', normalized) or (
        re.search(r'\b(?:price|investment|budget|cost|dollars|euros|prix|tarif|cout|investissement)\b', normalize(text))
        and re.search(r'\b(?:fit|aligned|acceptable|agree|convient|acceptable|accord)\b', normalized)
    ):
        scope = 'budget'
    elif re.search(r'\b(?:interested|interest|worth exploring|interesse|interessant)\b', normalized):
        scope = 'interest'
    elif re.search(r'\b(?:moment|continue|discuss|talk|good time|minute|parler|discuter|continuer|ecouter)\b', normalized):
        scope = 'continue'
    else:
        return None
    return ConsentProposal(response_id=response_id, scope=scope, text=text)


def resolve_consent(text: str, proposal: ConsentProposal | None,
                    available_channels: frozenset[str] = frozenset()) -> frozenset[str]:
    if negated_or_reported(text):
        return frozenset()
    if affirmative(text):
        return frozenset({proposal.scope}) if proposal else frozenset()
    normalized = normalize(text)
    direct_send = re.match(
        r'(?:(?:yes|oui) )?(?:please |s il vous plait )?'
        r'(?:(?:can|could|would|will) you |(?:pouvez|pourriez|voulez) vous )?'
        r'(?:send|resend|text|email|share|forward|envoyez|renvoyez|envoie|transmettez|partagez)\b', normalized)
    if direct_send:
        channels = mentioned_channels(text)
        if not channels and re.match(r'(?:please )?email\b', normalized):
            channels = frozenset({'email'})
        if not channels and re.match(r'(?:please )?text\b', normalized):
            channels = frozenset({'sms'})
        if not channels and proposal and proposal.scope in {'whatsapp', 'email', 'sms'}:
            channels = frozenset({proposal.scope})
        if not channels and len(available_channels) == 1:
            channels = available_channels
        return channels
    if re.fullmatch(r'(?:the |that |this )?(?:price|budget|investment) (?:is |works? )?(?:fine|acceptable|approved|for me)', normalized):
        return frozenset({'budget'})
    if re.fullmatch(r'(?:i am|i m|we are|je suis|nous sommes) (?:interested|interesse|interesses)', normalized):
        return frozenset({'interest'})
    if re.match(r'(?:please |s il vous plait )?(?:(?:can|could|would|will) you |(?:pouvez|pourriez) vous )?'
                r'(?:register me|enroll me|inscrivez moi|enregistrez moi|m inscrire|m enregistrer)\b', normalized):
        return frozenset({'registration'})
    if re.match(r'(?:i want to buy|i would like to buy|je veux acheter|je voudrais acheter|please place (?:the|my) order)\b', normalized):
        return frozenset({'purchase'})
    return frozenset()
