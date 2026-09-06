"""Deterministic Permission & Safety Gates for PhoneAgent.

Checks explicitly modeled action claims and speech constraints before delivery.
"""

from __future__ import annotations

import re

from ..channel_capabilities import promised_messaging_channels
from ..consent import mentioned_channels
from ..speech_identifiers import preserve_speech_identifiers


class PermissionGate:
    """Enforces non-negotiable boundaries, financial limits, and output sanitization."""

    PROHIBITED_PHRASES = (
        r"\b(as an ai language model|as an ai)\b",
        r"\b(en tant que modèle de langage|en tant qu'ia)\b",
    )
    UNVERIFIED_ACTION_CLAIMS = (
        r"\b(?:i|we)(?:\s+have|'ve)?\s+(?:(?:just|already|now)\s+)?(booked|reserved|paid|refunded|sent|deleted|updated|delivered)\b",
        r"\b(j'ai|je vous ai|nous avons|nous vous avons)\s+(?:(?:déjà|deja|bien|juste)\s+)?(réservé|payé|remboursé|envoyé|supprimé|modifié)\b",
        r"\b(?:je viens|nous venons) (?:de\s+(?:vous\s+)?|d')(envoyer|livrer|réserver|payer|rembourser|supprimer|modifier)\b",
        r"\b(?:the (?:message|link)|it) (?:has been|was|is) (sent|delivered)\b",
        r"\b(?:it|that)(?:'s| has) been (sent|delivered)\b",
        r"\bc'est (envoyé|livré)\b",
    )
    # Explicit ongoing/passive action assertions also need execution evidence.
    # Match the action object to avoid e.g. "sending you back to the topic".
    ACTION_PARAPHRASES = (
        ("sent", r"\b(?:i|we)(?:'ve| have)? (?:just )?(?:initiated|started|queued|triggered) "
         r"(?:the |your )?(?:dispatch|delivery|sending|send|email)\b"),
        ("sent", r"\b(?:i am|i'm|we are|we're) (?:now )?(?:sending|emailing|dispatching|forwarding) "
         r"(?:(?:you|to you) )?(?:(?:the|those|these|your|a|an|that|this) )?"
         r"(?:documents?|emails?|messages?|links?|details|summar(?:y|ies)|executive summary|offer|it|them)\b"),
        ("registered", r"\b(?:i|we)(?:'ve| have)? (?:just |already )?(?:registered|enrolled) "
         r"(?:you|your|the|a)\b"),
        ("sent", r"(?:^|[.!?;]\s*)(?:your|the) (?:email|message|link|documents?) "
         r"(?:has been|have been|was|were|is|are) (?:successfully )?(?:dispatched|sent)\b"),
        ("delivered", r"(?:^|[.!?;]\s*)(?:your|the) (?:email|message|link|documents?) "
         r"(?:has been|have been|was|were|is|are) (?:successfully )?delivered\b"),
        ("sent", r"\b(?:j'ai|nous avons) (?:lancé|déclenché|initié) "
         r"(?:l'envoi|la transmission|l'expédition)\b"),
        ("sent", r"\b(?:je|nous) (?:vous |te |lui )?(?:envoie|envoyons|transmets|transmettons) "
         r"(?:le|la|les|un|une|votre|vos|ce|ces)\b"),
        ("registered", r"(?:^|[.!?;]\s*)(?:votre|l')\s*(?:inscription|commande|demande) "
         r"(?:a été|est) (?:enregistrée|créée|validée)\b"),
    )

    @classmethod
    def unsupported_action_assertions(cls, text: str, verified_actions: set[str]) -> bool:
        normalized = text.replace("\u2019", "'")
        return any(
            action not in verified_actions and re.search(pattern, normalized, re.IGNORECASE)
            for action, pattern in cls.ACTION_PARAPHRASES
        )

    @classmethod
    def sanitize_for_telephony(cls, text: str) -> str:
        # Strip fenced code first; an identifier inside code must not restore it.
        prose = re.sub(r"```.*?```", "", text, flags=re.DOTALL)
        return preserve_speech_identifiers(prose, cls._sanitize_prose)

    @classmethod
    def _sanitize_prose(cls, text: str) -> str:
        """Strip markdown, symbols, asterisks, and emojis to produce pure telephony speech."""
        if not text:
            return ""

        # Remove code blocks and markdown headers
        cleaned = re.sub(r"```.*?```", "", text, flags=re.DOTALL)
        cleaned = re.sub(r"[#*`_~>\-•]", " ", cleaned)

        # Remove emojis
        emoji_pattern = re.compile(
            "["
            "\U0001f600-\U0001f64f"  # emoticons
            "\U0001f300-\U0001f5ff"  # symbols & pictographs
            "\U0001f680-\U0001f6ff"  # transport & map
            "\U0001f1e0-\U0001f1ff"  # flags
            "\U00002702-\U000027b0"
            "\U000024c2-\U0001f251"
            "]+",
            flags=re.UNICODE,
        )
        cleaned = emoji_pattern.sub("", cleaned)

        # Normalize whitespace
        cleaned = re.sub(r"\s+", " ", cleaned).strip()
        return cleaned

    @classmethod
    def check_compliance(cls, text: str) -> tuple[bool, list[str]]:
        """Verify output does not contain prohibited robotic expressions."""
        violations: list[str] = []
        for pattern in cls.PROHIBITED_PHRASES:
            if re.search(pattern, text, re.IGNORECASE):
                violations.append(f"Contains prohibited robotic phrase matching {pattern}")
        return len(violations) == 0, violations

    @classmethod
    def verify_financial_limit(cls, amount: float, max_limit: float = 0.0) -> bool:
        """Reject financial commitment if above authorized zero-trust threshold."""
        return 0.0 <= amount <= max_limit

    @classmethod
    def enforce_spoken_response(
        cls,
        text: str,
        *,
        language: str,
        verified_actions: set[str] | None = None,
        messaging_available: bool | None = None,
        available_messaging_channels: frozenset[str] | None = None,
        verified_actions_by_channel: dict[str, set[str]] | None = None,
    ) -> tuple[str, list[str]]:
        """Sanitize speech and prevent unsupported external-action claims."""

        cleaned = cls.sanitize_for_telephony(text)
        missing_channels = (
            promised_messaging_channels(cleaned) - available_messaging_channels
            if available_messaging_channels is not None else frozenset()
        )
        compliant, violations = cls.check_compliance(cleaned)
        if not compliant:
            cleaned = cls._safe_fallback(language)
        if re.search(r"[\u0600-\u06ff]", cleaned):
            violations.append("Response used a language outside the English/French policy")
            cleaned = cls._safe_fallback(language)
        verified = set(verified_actions or set())
        if verified_actions_by_channel is not None:
            channels = mentioned_channels(cleaned)
            if channels:
                # A WhatsApp receipt cannot prove email, even when both senders exist.
                verified = set.intersection(*(set(verified_actions_by_channel.get(channel, set())) for channel in channels))
            else:
                messaging = [actions for channel, actions in verified_actions_by_channel.items()
                             if channel in {'whatsapp', 'email', 'sms'}]
                if messaging:
                    verified.difference_update({'sent', 'delivered', 'read'})
                    verified.update(set.intersection(*(set(actions) for actions in messaging)))
        if missing_channels:
            names = ', '.join(sorted(missing_channels))
            violations.append('Offered an unavailable messaging channel: ' + names)
            cleaned = (
                f"Je ne peux pas envoyer par {names} pendant cet appel."
                if language.lower().startswith('fr') else f"I can't send by {names} from this call."
            )
            if available_messaging_channels:
                alternatives = ', '.join(sorted(available_messaging_channels))
                cleaned += (
                    f" Je peux utiliser {alternatives}." if language.lower().startswith('fr')
                    else f" I can use {alternatives}."
                )
        if cls.unsupported_action_assertions(cleaned, verified):
            violations.append("Claimed an external action without a verified tool result")
            cleaned = cls._safe_fallback(language)
        action_aliases = {"réservé": "reserved", "payé": "paid", "remboursé": "refunded",
                          "envoyé": "sent", "livré": "delivered", "supprimé": "deleted", "modifié": "updated", "envoyer": "sent", "livrer": "delivered",
                          "réserver": "reserved", "payer": "paid", "rembourser": "refunded",
                          "supprimer": "deleted", "modifier": "updated"}
        for pattern in cls.UNVERIFIED_ACTION_CLAIMS:
            for match in re.finditer(pattern, cleaned.replace("\u2019", "'"), re.IGNORECASE):
                action = match.groups()[-1].lower()
                if action_aliases.get(action, action) not in verified:
                    violations.append("Claimed an external action without a verified tool result")
                    cleaned = cls._safe_fallback(language)
                    break
        if messaging_available is False and re.search(
            r"\b(?:i (?:can|will|am going to)(?: certainly)?|i'll|we can|je (?:peux|vais)) "
            r"(?:send|text|envoyer)\b",
            cleaned.replace("\u2019", "'"), re.IGNORECASE,
        ):
            violations.append("Promised messaging without an available messaging tool")
            cleaned = (
                "Je ne peux pas envoyer de message pendant cet appel. Je peux vous expliquer l'offre."
                if language.lower().startswith("fr") else
                "I can't send a message from this call. I can explain the offer to you."
            )
        if "sent" in verified and "delivered" not in verified and re.search(
            r"\byou (?:should|will) (?:now )?(?:see|receive|get)\b"
            r"|\b(?:it|the message) (?:will|should) (?:arrive|appear)\b"
            r"|\bvous (?:devriez|allez) recevoir\b",
            cleaned, re.IGNORECASE,
        ):
            violations.append("Predicted message arrival without delivery confirmation")
            cleaned = (
                "Dites-moi quand vous recevez le message."
                if language.lower().startswith("fr") else "Let me know when the message arrives."
            )
        return cleaned, violations

    @staticmethod
    def _safe_fallback(language: str) -> str:
        if language.lower().startswith("fr"):
            return (
                "Je ne peux pas confirmer cette action sans vérification. "
                "Je peux recueillir les informations nécessaires."
            )
        if language.lower().startswith("en"):
            return (
                "I cannot confirm that action without verification. "
                "I can collect the required information."
            )
        return (
            "I cannot confirm that action without verification. "
            "I can collect the required information."
        )
