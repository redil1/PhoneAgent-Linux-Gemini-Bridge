"""Small semantic turn guard for fragments that must not trigger an AI reply."""

from __future__ import annotations

import logging
import re

from pipecat.frames.frames import Frame, TranscriptionFrame
from pipecat.processors.frame_processor import FrameDirection, FrameProcessor

logger = logging.getLogger("PhoneAgentTurnContinuity")

# These are grammatical fragments, not useful standalone caller turns. Keep the
# list intentionally small: one-word answers such as yes/no/sport must continue
# to reach the model immediately.
_STANDALONE_CONNECTORS = frozenset(
    {
        "and",
        "because",
        "but",
        "or",
        "the",
        "it",
        "to",
        "at",
        "in",
        "on",
        "with",
        "for",
        "of",
        "avec",
        "car",
        "de",
        "des",
        "donc",
        "du",
        "et",
        "mais",
        "parce",
        "pour",
        "que",
        "ديال",
        "ديالي",
        "ديالو",
        "ديالها",
        "ف",
        "في",
        "من",
        "على",
        "مع",
        "ب",
        "و",
        "حيت",
    }
)

# Trailing words that unambiguously indicate an open, incomplete grammatical clause
_INCOMPLETE_TRAILING_WORDS = frozenset(
    {
        # Prepositions (English)
        "at", "to", "for", "in", "on", "with", "about", "of", "from", "by",
        "into", "through", "over", "under", "towards", "toward", "upon",
        "off", "onto", "against", "between", "among", "during", "without", "within",
        # Prepositions (French)
        "à", "au", "aux", "de", "du", "des", "dans", "par", "pour", "avec",
        "sur", "sous", "vers", "chez", "en", "sans", "entre",
        # Prepositions (Darija / Arabic)
        "ديال", "ف", "في", "من", "على", "مع", "ب",
        # Conjunctions (English)
        "and", "or", "but", "because", "so", "if", "when", "where", "while",
        "although", "though", "unless", "since", "whereas", "whether",
        # Conjunctions (French)
        "et", "ou", "mais", "donc", "car", "parce que", "puisque", "si",
        "quand", "lorsque", "que", "qui", "quoi", "dont", "où", "comme",
        # Conjunctions (Darija / Arabic)
        "و", "ولكن", "حيت", "باش", "واش", "علاش", "كيفاش", "فين", "شكون",
        # Auxiliary & Modal Verbs (English)
        "is", "am", "are", "was", "were", "be", "been", "being",
        "have", "has", "had", "do", "does", "did",
        "can", "could", "will", "would", "shall", "should", "may", "might", "must",
        "wanna", "gonna", "gotta",
        # Auxiliary & Modal Verbs (French)
        "est", "sont", "été", "ai", "as", "a", "avons", "avez", "ont",
        "vais", "va", "vont", "peux", "peut", "veux", "veut", "dois", "doit",
        # Contractions with copula / auxiliaries
        "that's", "it's", "there's", "here's", "what's", "who's", "i'm", "you're",
        "we're", "they're", "he's", "she's", "isn't", "aren't", "wasn't", "weren't",
        "haven't", "hasn't", "hadn't", "don't", "doesn't", "didn't", "can't",
        "couldn't", "won't", "wouldn't", "shouldn't",
        "c'est", "j'ai", "c'était",
        # Transitive verbs that require a direct object / clause
        "buy", "purchase", "acheter",
        # Adverbs & qualifiers that leave a clause hanging
        "really", "actually", "just", "simply", "honestly", "basically",
        "probably", "definitely", "maybe", "perhaps", "almost", "hardly", "barely", "nearly", "quite",
        "vraiment", "juste", "actuellement", "presque",
        # Articles, Determiners & Possessives
        "the", "an", "my", "your", "his", "her", "our", "their",
        "le", "la", "les", "un", "une", "mon", "ma", "mes", "ton", "ta", "tes",
        "notre", "votre", "leur", "ce", "cet", "cette", "ces",
        # Subject pronouns that never terminate a thought
        "i", "we", "he", "she", "they",
        "je", "tu", "il", "elle", "nous", "ils", "elles", # Fillers & Hesitations
        "um", "uh", "er", "ah", "erm", "like",
        "euh", "euhm", "ben", "genre", "يعني", "زعما",
    }
)

_INCOMPLETE_TRAILING_PHRASES = frozenset(
    {
        "looking at", "looking for", "looking to", "looking to buy",
        "thinking of", "thinking about", "talking about", "waiting for",
        "listening to", "trying to", "trying to buy", "going to",
        "want to", "want to buy", "need to", "like to", "used to",
        "supposed to", "planning to", "planning to buy", "hoping to",
        "wondering if", "wondering whether", "asking about", "interested in",
        "not looking at", "not really looking at", "not looking to", "not really looking to",
        "in order to", "as well as", "such as", "due to", "because of",
        "you know", "i mean", "sort of", "kind of",
        "en train de", "tu vois", "tu sais",
    }
)


def _normalized_tokens(text: str) -> list[str]:
    return re.findall(r"[\wÀ-ÿ\u0600-\u06ff']+", text.casefold().replace("\u2019", "'"), flags=re.UNICODE)


def is_unfinished_evaluation(text: str) -> bool:
    """A degree modifier without its adjective cannot express agreement.

    Prosody may sound final and ASR may add a period to "it seems very".
    Neither supplies the absent evaluation (expensive, useful, unsuitable, ...).
    Avoid ambiguous standalone degree words such as "enough" or "trop".
    """
    tokens = _normalized_tokens(text)
    return bool(tokens and tokens[-1] in {
        "very", "extremely", "particularly", "exceptionally",
        "très", "tres", "extrêmement", "extremement", "particulièrement",
        "particulierement", "tellement",
    })


def is_unfinished_preference(text: str) -> bool:
    """A stated intention with its choice missing, never a known preference."""
    normalized = " ".join(_normalized_tokens(text))
    return bool(re.fullmatch(
        r"(?:i|we) (?:(?:would|really|definitely|personally) )?(?:prefer|want|need|choose)"
        r"|je (?:(?:préfère|prefere|voudrais|veux|choisis))"
        r"|nous (?:préférons|preferons|voulons|voudrions)",
        normalized,
    ))


def is_unfinished_request(text: str) -> bool:
    """A bare modal question needs continuation patience, even with ASR punctuation."""
    normalized = " ".join(_normalized_tokens(text))
    return bool(re.fullmatch(
        r"(?:can|could|would|will|should|may|do) (?:you|we|i)(?: please)?"
        r"|(?:est ce que )?(?:vous (?:pouvez|pourriez|voudriez|voulez|allez|devez)"
        r"|tu (?:peux|pourrais|voudrais|veux|vas|dois)"
        r"|nous (?:pouvons|pourrions|voulons|devons))"
        r"|(?:pouvez|pourriez|voudriez|voulez|allez|devez) vous"
        r"|(?:peux|pourrais|voudrais|veux|vas|dois) tu",
        normalized,
    ))


def is_semantically_incomplete_fragment(text: str) -> bool:
    """Return true only for a high-confidence, non-actionable speech fragment."""

    tokens = _normalized_tokens(text)
    if not tokens:
        return True
    if len(tokens) == 1:
        return tokens[0] in _STANDALONE_CONNECTORS
    return False


_CONCISE_TERMINAL = frozenset(
    {
        "yes",
        "no",
        "yeah",
        "yep",
        "nope",
        "okay",
        "ok",
        "sure",
        "fine",
        "right",
        "good",
        "perfect",
        "thanks",
        "oui",
        "non",
        "ouais",
        "d accord",
        "bien",
        "parfait",
        "merci",
        "exactement",
        "voila",
        "voilà",
        "allô",
        "allo",
        "hello",
        "hi",
    }
)


def is_concise_terminal_turn(text: str) -> bool:
    """Return true for brief, unambiguous confirmation/negation turns."""
    cleaned = re.sub(r"[.!?…,:;\-—]+$", "", text.strip().casefold()).strip()
    tokens = _normalized_tokens(cleaned)
    if not tokens:
        return False
    if len(tokens) <= 2 and all(t in _CONCISE_TERMINAL for t in tokens):
        return True
    return False


def looks_semantically_incomplete(text: str) -> bool:
    """Endpointing hint for fragments or sentences that clearly trail off."""

    normalized = " ".join(text.strip().casefold().split())
    if not normalized:
        return False
    if is_unfinished_preference(text) or is_unfinished_request(text) or is_unfinished_evaluation(text):
        return True
    # An ellipsis is continuation punctuation, not a completed sentence.
    if normalized.endswith("…") or re.search(r"\.{2,}$", normalized):
        return True
    if is_semantically_incomplete_fragment(normalized):
        return True
    # Trailing continuation punctuation
    if normalized.endswith((",", ";", ":", "-", "—")):
        return True

    # Strip synthetic ASR punctuation at the end to inspect true trailing words
    cleaned = re.sub(r"[.!?…,:;\-—]+$", "", normalized).strip()
    if not cleaned:
        return False
    tokens = _normalized_tokens(cleaned)
    if not tokens:
        return False

    # Elliptical answers and stranded-preposition questions can be complete.
    # Do not force three seconds merely because their last word is an auxiliary
    # or preposition. This removes a lexical veto; Smart Turn and received
    # silence still decide when to release the caller's floor.
    if re.fullmatch(
        r"(?:yes|no|yeah|yep|nope|sure) (?:i|we|you|he|she|it|they) "
        r"(?:can|can't|cannot|could|couldn't|will|won't|would|wouldn't|"
        r"do|don't|does|doesn't|did|didn't|have|haven't|has|hasn't|"
        r"am|are|is|was|were)(?: not)?",
        " ".join(tokens),
    ):
        return False
    if (
        len(tokens) >= 4
        and tokens[0] in {"who", "what", "where", "which"}
        and tokens[1] in {"am", "is", "are", "was", "were", "do", "does", "did", "can", "could"}
        and tokens[-1] in {"from", "with", "for", "at", "about"}
    ):
        return False

    last_word = tokens[-1]
    if last_word in _INCOMPLETE_TRAILING_WORDS:
        return True

    if len(tokens) >= 2:
        last_two = f"{tokens[-2]} {tokens[-1]}"
        if last_two in _INCOMPLETE_TRAILING_PHRASES:
            return True

    if re.search(
        r"(?:because|why|if|but|so|when|where|how|parce que|pourquoi|si|mais|"
        r"donc|que|quand|où|comment|and|or|to|for|like|such as|go|aller|"
        r"comme)\s*[,;:]*$",
        cleaned,
    ):
        return True

    if normalized.endswith((".", "!", "?")):
        return False
    return False


def dynamic_endpoint_delay_ms(
    partial_text: str,
    base_silence_ms: int = 650,
    incomplete_silence_ms: int = 1500,
    concise_silence_ms: int = 300,
) -> int:
    """Calculate the optimal silence patience based on clause semantics."""
    if not partial_text:
        return base_silence_ms
    if is_concise_terminal_turn(partial_text):
        return min(concise_silence_ms, base_silence_ms)
    if looks_semantically_incomplete(partial_text):
        return max(incomplete_silence_ms, base_silence_ms + 700)
    return base_silence_ms


class SemanticTurnGuardProcessor(FrameProcessor):
    """Observe incomplete turns while allowing model-led clarification.

    STT backends use :func:`looks_semantically_incomplete` to wait longer
    before committing a turn. If a fragment is still final after that wait, it
    must reach the model: suppressing it produces dead silence, while the model
    can naturally ask what the caller meant using the live turn-quality hint.
    """

    async def process_frame(self, frame: Frame, direction: FrameDirection) -> None:
        await super().process_frame(frame, direction)
        if (
            direction is FrameDirection.DOWNSTREAM
            and isinstance(frame, TranscriptionFrame)
            and looks_semantically_incomplete(frame.text)
        ):
            logger.info(
                "Forwarded incomplete caller turn for model-led clarification chars=%d",
                len(frame.text.strip()),
            )
        await self.push_frame(frame, direction)
