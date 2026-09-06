"""Preserve literal contact/link identifiers across prose normalization."""

import re
from collections.abc import Callable

_IDENTIFIER = re.compile(
    r"https?://[^\s<>\[\]{}\"()]+"
    r"|[A-Za-z0-9.!#$%&'*+/=?^_`{|}~-]+@[A-Za-z0-9](?:[A-Za-z0-9.-]*[A-Za-z0-9])?\.[A-Za-z]{2,}",
    re.IGNORECASE,
)


def preserve_speech_identifiers(text: str, transform: Callable[[str], str]) -> str:
    """Shield identifiers from generic punctuation/acronym/number rewriting.

    Placeholders contain no numeric or markup characters. The prefix is chosen
    outside the input so caller text cannot collide with a restoration token.
    """
    prefix = "PHONESPEECHIDENTIFIER"
    while prefix in text:
        prefix += "Q"
    protected: dict[str, str] = {}

    def protect(match: re.Match[str]) -> str:
        value = match.group().rstrip('.,;:!?')
        suffix = match.group()[len(value):]
        token = prefix + 'Z' * (len(protected) + 1) + 'END'
        protected[token] = value
        return token + suffix

    transformed = transform(_IDENTIFIER.sub(protect, text))
    for token, value in protected.items():
        transformed = transformed.replace(token, value)
    return transformed
