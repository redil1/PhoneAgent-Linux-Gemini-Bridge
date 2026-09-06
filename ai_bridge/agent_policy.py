"""Small provider-independent policy layer for every PhoneAgent call."""

from __future__ import annotations

import asyncio
import contextlib
import copy
import inspect
import json
import logging
import re
import time
from collections import deque
from collections.abc import Awaitable, Callable
from typing import Any

from pipecat.frames.frames import (
    BotStartedSpeakingFrame,
    BotStoppedSpeakingFrame,
    ErrorFrame,
    Frame,
    InterruptionFrame,
    LLMFullResponseEndFrame,
    LLMFullResponseStartFrame,
    LLMTextFrame,
    TranscriptionFrame,
    TTSSpeakFrame,
    TTSStartedFrame,
    UserStartedSpeakingFrame,
)
from pipecat.processors.aggregators.llm_context import LLMContext
from pipecat.processors.frame_processor import FrameDirection, FrameProcessor

from .action_receipts import BUSINESS_RECEIPTS, ActionReceipt, receipt_from_result
from .call_context import CallContextPolicy
from .channel_capabilities import builtin_capabilities
from .consent import ConsentProposal, affirmative, proposal_from_speech, resolve_consent
from .control_plane import ControlPlaneStore
from .conversation_repair import (
    RepairPolicy,
    TurnQuality,
    caller_authorizes_repetition,
    classify_caller_turn,
)
from .generation_recovery import GENERATION_ID, GenerationFailureFrame
from .guardrails.permission_gate import PermissionGate
from .guardrails.personality_judge import PersonalityFidelityJudge, TurnEvaluationResult
from .human_speech import (
    VariedPhrasePicker,
    acknowledgements_for,
    detect_language,
    detect_register,
    normalize_for_speech,
)
from .identity.greetings import ensure_greeting_disclosure
from .knowledge_evidence import unsupported_material_categories
from .memory.memory_manager import LayeredMemoryManager
from .memory.memory_writer import ValidatedMemoryWriter
from .memory.scoping import memory_namespace
from .personality.persona_compiler import PersonaCompiler
from .speech_floor_guard import (
    SPEECH_RESPONSE_ID,
    SPEECH_TURN_EPOCH,
    SYNTHESIS_CONTEXT_ID,
    SpeechFloorPermissionFrame,
)
from .tasks.call_state import CallOutcome, TaskRuntime
from .tasks.task_engine import TaskEngine
from .turn_continuity import (
    is_unfinished_evaluation,
    is_unfinished_preference,
    looks_semantically_incomplete,
)

EventSink = Callable[[dict[str, Any]], Any]
CallCompletionSink = Callable[[str], Awaitable[None] | None]
logger = logging.getLogger("PhoneAgentPolicy")

_DECEPTIVE_SPOKEN_IDENTITY = re.compile(
    r"\b(?:i am|i'm|je suis)\s+(?:a\s+|une?\s+)?(?:real\s+human|human|real\s+person|"
    r"personne\s+r[ée]elle|humain(?:e)?)\b|"
    r"\b(?:i am|i'm|je suis|we are|nous sommes)\s+(?:not|pas)\s+"
    r"(?:an?\s+|une?\s+)?(?:ai|ia|robot|bot|intelligence artificielle)\b",
    re.IGNORECASE,
)
_AI_DISCLOSURE = re.compile(r"\b(?:AI|IA|intelligence artificielle)\b", re.IGNORECASE)
_AI_IDENTITY_QUESTION = re.compile(
    r"\b(?:are you (?:a )?(?:human|ai|robot|bot)|human or an? ai|"
    r"pretend you are human|tell me you are not (?:ai|a robot|a bot)|"
    r"(?:êtes|etes)[- ]vous (?:humain|une? (?:ia|intelligence artificielle|robot))|"
    r"vous (?:êtes|etes) (?:humain|une? (?:ia|intelligence artificielle|robot))|"
    r"faites semblant d['\u2019]être humain|niez que vous (?:êtes|etes) une? ia)\b",
    re.IGNORECASE,
)

_DIRECT_PRODUCT_QUESTION = re.compile(
    r"\b(?:"
    r"what(?:'s| is) (?:in|included in|the price|the cost)|"
    r"what does .{0,30}(?:plan|package|subscription) include|"
    r"how much|price range|(?:basic|starter|essential|family|premium) "
    r"(?:plan|package)|month[- ]to[- ]month|no contract|contract terms|"
    r"trial|devices? (?:does|do|can)|what do you offer|send|whatsapp|register|interested|offers?"
    r")\b",
    re.IGNORECASE,
)
_LOW_VALUE_ACKNOWLEDGEMENT = re.compile(
    r"^(?:i (?:hear you|understand|see|appreciate that)|got it|that makes sense|"
    r"right|okay|i'm glad you're still with me)\b",
    re.IGNORECASE,
)
_VERIFIED_VALUE_CUE = re.compile(
    r"\b(?:essential|family|premium|trial|screens?|smart tv|firestick|apple tv|"
    r"android|two months free|ten euros|fifteen euros|twenty euros|activation|"
    r"i(?:'ll| will| can)|we (?:can|will)|let's|next step|call back|send|schedule|"
    r"confirm|recommend|option|means|so you|that gives|you can|works?)\b",
    re.IGNORECASE,
)


class AgentPolicyRuntime:
    """One call's persona, task, caller memory, evaluation, and persistence."""

    def __init__(
        self,
        *,
        caller_id: str,
        task_id: str,
        language: str,
        call_direction: str = "outbound",
        additional_instructions: str = "",
        memory_enabled: bool = True,
        available_tools: set[str] | None = None,
        event_sink: EventSink | None = None,
        memory_manager: LayeredMemoryManager | None = None,
    ) -> None:
        self.caller_id = LayeredMemoryManager.normalize_caller_id(caller_id)
        self.task_id = task_id
        self.language = language
        self.call_context = CallContextPolicy(call_direction)
        self.available_tools = available_tools or set()
        self.tool_capabilities: dict[str, frozenset[str]] = {}
        self.event_sink = event_sink
        self.memory_enabled = (memory_enabled and self.caller_id != "anonymous"
                               and not self.caller_id.startswith("unknown:"))
        self.persona_compiler = PersonaCompiler()
        identity_status = self.persona_compiler.identity_kernel.production_status()
        if not identity_status["ready"]:
            raise ValueError(
                "Active Identity Kernel profile is not production-ready: "
                f"{identity_status['evaluator_version']} score={identity_status['score']}"
            )
        self.task_engine = TaskEngine()
        self.task_contract = self.task_engine.require_contract(task_id)
        active_package = ControlPlaneStore().active()
        self.memory_scope = memory_namespace(self.persona_compiler.identity_kernel.active, self.task_contract,
                                             active_package.package.package_id if active_package else '')
        self.memory_manager = (memory_manager or LayeredMemoryManager(persistence_enabled=False)).scoped(
            self.memory_scope, persistence_enabled=self.memory_enabled
        )
        self.memory_writer = ValidatedMemoryWriter(self.memory_manager)
        # Slots, stage and outcome are tracked in code. The contract listed what
        # to discover and nothing checked it, so the agent re-asked answered
        # questions and never knew when the task was done.
        self.task = TaskRuntime(self.task_contract)
        if any(slot.id == "preferred_language" for slot in self.task.slots):
            preferred = "French" if language.lower().startswith("fr") else "English"
            self.task.record("preferred_language", preferred)
        self.caller_memory = (
            self.memory_manager.get_caller_memory(self.caller_id) if self.memory_enabled else None
        )
        self.system_prompt = self.persona_compiler.compile(
            caller_memory=self.caller_memory,
            task_contract=self.task_contract,
            language=language,
            call_direction=self.call_context.direction.value,
            additional_instructions=additional_instructions,
            available_tools=self.available_tools,
            caller_id=self.caller_id,
        )
        self._persona_compile_args = {
            "language": language,
            "call_direction": self.call_context.direction.value,
            "additional_instructions": additional_instructions,
        }
        self.judge = PersonalityFidelityJudge()
        # The persona owns every wording; this guard only decides *when* one is
        # needed, which is the part the model cannot judge from text alone.
        self.repair = RepairPolicy(
            language=language,
            overrides=self.persona_compiler.repair_phrases(language),
        )
        self.acknowledgements = VariedPhrasePicker(pool=acknowledgements_for(language))
        # These are observational. They inform the live prompt and evaluator;
        # they never substitute canned dialogue into the model's response.
        self._spoken_sentences: deque[str] = deque(maxlen=24)
        self._response_sentence_drafts: dict[str, list[str]] = {}
        self._response_turn_drafts: dict[str, str] = {}
        self._completed_ai_turns: deque[str] = deque(maxlen=12)
        self._question_open = False
        # Incremented on every caller turn. A reply generated for an older turn
        # is stale: the caller has already moved on, and speaking it produces
        # two answers to two versions of the same question.
        self._turn_epoch = 0
        self._verified_actions: set[str] = set()
        self._verified_actions_by_channel: dict[str, set[str]] = {}
        self._action_receipts: dict[str, ActionReceipt] = {}
        self._current_action_receipts: dict[str, ActionReceipt] = {}
        self._stream_policy_violations: list[str] = []
        self._caller_register = ""
        self._caller_language = "fr" if language.lower().startswith("fr") else "en"
        self.last_caller_text = ""
        self.last_caller_transcript_trusted = True
        self.last_caller_transcription_confidence: float | None = None
        self.recent_caller_turns: deque[tuple[str, bool]] = deque(maxlen=6)
        self._caller_transcript_number = 0
        self._pending_consent_proposal: ConsentProposal | None = None
        self._current_consent: frozenset[str] = frozenset()
        self._response_consent_epochs: dict[str, int] = {}
        self._latest_consent_response_sequence = 0
        self._turn_started_at = 0.0
        self._background_tasks: set[asyncio.Task[Any]] = set()
        self._pending_memory_turns: dict[str, tuple[str, str, float, float, list[str]]] = {}
        self._memory_playback_outcomes: dict[str, str] = {}
        self._memory_write_tail: asyncio.Task[None] | None = None
        self._response_sequence = 0
        self._pending_playback_ids: deque[str] = deque()
        self._active_playback_id: str | None = None
        self._playback_interrupted = False
        self._live_context: Any | None = None
        self._live_state_message: dict[str, str] | None = None
        self._opening_attempted = False
        self._permission_state = "unknown"
        self._conversation_stage = "OPEN"
        self._last_caller_intent = "unknown"
        self._latest_turn_quality = "unknown"
        self._latest_turn_guidance = "Listen to the caller's latest meaning and answer it directly."
        self._last_ai_response = ""
        self._last_ai_delivery = "none"
        self._last_guard_rejection = ""
        self._repeat_authorized_epoch = -1
        self._terminal_response_id: str | None = None
        self._terminal_completion_reason = ""
        self._terminal_completion_sink: CallCompletionSink | None = None
        self._terminal_completion_notified = False
        self._closed = False
        self._projecting_transcription = False

    def recompile_system_prompt(self) -> str:
        """Rebuild the persona now that the live tool set is known.

        The prompt is compiled in __init__, before any tool backend has been
        reached, so it would otherwise tell the model "Connected Tools: none"
        while the runtime was holding twenty of them — and a persona instructed
        it had no tools does not call any.
        """

        self.system_prompt = self.persona_compiler.compile(
            caller_memory=self.caller_memory,
            task_contract=self.task_contract,
            available_tools=self.available_tools,
            caller_id=self.caller_id,
            **self._persona_compile_args,
        )
        return self.system_prompt

    def attach_context(self, context: Any) -> None:
        """Attach one mutable, provider-independent live-state system message."""

        self._live_context = context
        self._live_state_message = {"role": "system", "content": ""}
        context.add_message(self._live_state_message)
        self._refresh_live_state()

    @staticmethod
    def _normalized_intent_text(text: str) -> str:
        normalized = re.sub(r"[^\wÀ-ÿ'\u2019]+", " ", text.casefold())
        return " ".join(normalized.replace("'", " ").replace("\u2019", " ").split())

    def _classify_permission(self, text: str) -> str:
        normalized = self._normalized_intent_text(text)
        if not normalized:
            return "unknown"
        if affirmative(text):
            return 'granted'
        # Permission is a high-impact state transition, so only accept a short
        # direct answer. Substring searches used to grant/refuse permission
        # from quoted examples such as "say: did I catch you at a good time?".
        if len(normalized.split()) > 18:
            return "unknown"
        positive = (
            r"(?:oui|yes|yeah|yep|okay|ok|d accord|bien sur|certainement)",
            r"(?:oui|yes|okay|ok)?\s*(?:vas y|allez y|go ahead|please continue|"
            r"continuez|je vous ecoute)",
            r"(?:yes|yeah|oui)\s+(?:i am|i m|je suis)\s+(?:available|free|disponible)",
            r"(?:yes|yeah|sure|okay|ok|oui)\s+(?:(?:that|it) s?\s+)?"
            r"(?:fine|okay|ok|good|bon|bien)",
            r"(?:yes|yeah|sure|oui)\s+(?:you can|i have (?:a )?(?:minute|moment)|"
            r"i can talk|vous pouvez)",
            r"(?:yes|oui)?\s*(?:this is|it is|c est)?\s*(?:a )?(?:good time|bon moment)",
            r"(?:tell me more|sounds interesting|i am interested|je suis interesse|"
            r"je suis intéressé|dites m en plus|ca m interesse|ça m intéresse)",
        )
        negative = (
            r"(?:no|non|no thank you|non merci|stop|not interested|pas interesse|pas intéressé)",
            r"(?:no thanks|non merci)?\s*(?:i am|i m|je suis)?\s*"
            r"(?:not interested|pas interesse|pas intéressé)",
            r"(?:no|non)\s+(?:not interested|pas interesse|pas intéressé)",
            r"(?:please )?(?:don t call|do not call|stop calling|ne m appelez plus)"
            r"(?: me)?(?: again)?",
            r"(?:sorry )?(?:this is|it is|c est)?\s*(?:a )?(?:bad time|mauvais moment)",
            r"(?:no |sorry )?(?:i can t|i cannot)\s+talk(?: right now| now)?",
            r"(?:no |sorry )?(?:now|this)\s+is\s+not\s+(?:a )?good time",
        )
        if any(re.fullmatch(pattern, normalized) for pattern in negative):
            return "refused"
        if any(re.fullmatch(pattern, normalized) for pattern in positive):
            return "granted"
        return "unknown"

    def _is_goodbye(self, text: str) -> bool:
        normalized = self._normalized_intent_text(text)
        return bool(
            re.search(
                r"^(?:bye|goodbye|see you|talk later|au revoir|salut|a bientot|à bientôt|"
                r"bonne journee|bonne journée|je dois y aller|i have to go|"
                r"thanks for (?:this|the information|your time)|"
                r"thank you that(?:'s| is) all|merci pour (?:tout|ces informations))"
                r"[.! ]*$",
                normalized,
            )
        )

    def _is_attention_check(self, text: str) -> bool:
        normalized = self._normalized_intent_text(text)
        return any(
            re.search(pattern, normalized)
            for pattern in (
                r"^(?:hello|hello there|hi|hey|allo|all[oô])"
                r"(?: (?:are you there|can you hear me|vous m entendez|tu m entends))?"
                r"(?: (?:okay|ok|clearly|bien|maintenant|toujours|please|s il vous plait))?$",
                r"^(?:are you (?:still )?there|you (?:still )?there|"
                r"can you hear me(?:(?: okay| ok| clearly| now)){0,2}|"
                r"do you hear me(?:(?: okay| ok| clearly| now)){0,2}|"
                r"vous m entendez(?:(?: bien| clairement| maintenant| toujours)){0,2}|"
                r"est ce que vous m entendez(?:(?: bien| clairement| maintenant)){0,2}|"
                r"tu m entends(?:(?: bien| clairement| maintenant| toujours)){0,2})"
                r"(?: please| s il vous plait)?$",
            )
        )

    @property
    def reply_language(self) -> str:
        """Locale used for deterministic wording on the caller's current turn."""

        return "fr-FR" if self._caller_language == "fr" else "en-US"

    async def _adopt_caller_language(self, language_code: str | None) -> None:
        code = (language_code or "").strip().lower().replace("_", "-").split("-", 1)[0]
        if code not in {"en", "fr"} or code == self._caller_language:
            return
        self._caller_language = code
        self.repair.language = self.reply_language
        self.repair.overrides = self.persona_compiler.repair_phrases(self.reply_language)
        self.acknowledgements = VariedPhrasePicker(pool=acknowledgements_for(self.reply_language))
        if any(slot.id == "preferred_language" for slot in self.task.slots):
            changed = self.task.record(
                "preferred_language", "French" if code == "fr" else "English"
            )
            if changed.changed:
                await self._emit({"type": "task_state", **self.task.summary()})
        if not self._projecting_transcription:
            logger.info("Caller language switched to %s", code)

    def live_state_instructions(self) -> str:
        """Return advisory state for the model without scripting its next reply."""

        last_response = " ".join(self._last_ai_response.split())[:300] or "none"
        known = "; ".join(f"{key}={value}" for key, value in self.task.state.items()) or "none"
        missing = self.task.missing_slots()
        missing_names = ", ".join(slot.id for slot in missing) or "none"
        next_question = "none"
        if missing:
            nxt = missing[0]
            next_question = nxt.question or f"what their {nxt.id.replace('_', ' ')} is"
        strategy_hint, _permitted_question = self.call_context.steering(next_question)
        memory_guidance = (
            "Use only supplied caller history; remembered preferences are not evidence of a CRM action."
            if self.memory_enabled else
            "Automatic caller history is disabled. Use this call's context normally, but do not promise to save details for future calls."
        )
        if self._last_caller_intent == "direct_product_question":
            required_move = (
                "ANSWER NOW from PRODUCT GROUND TRUTH in the stable system context. "
                "Give the requested essentials first; "
                "do not ask discovery, ask what they mean, or announce a future lookup."
            )
        elif self._last_caller_intent == "attention_check":
            required_move = (
                "Confirm you are present warmly ('Yes, I am here!'), and IMMEDIATELY answer or fulfill the caller's last request (such as sending info on WhatsApp, discussing plans, or confirming registration). Never stay silent and never just say 'I am here' without moving forward."
            )
        else:
            required_move = (
                "Add one useful thing: a direct answer, a verified value connection, or one "
                "necessary unanswered question. An acknowledgement or paraphrase alone is invalid."
            )
        return (
            "# INTERNAL LIVE CALL CONTEXT — never read or mention this block aloud\n"
            "This is advisory context, not a script. The caller's latest meaning always outranks "
            "task order, sales stages, missing fields, and sample phrases.\n"
            f"{self.call_context.state_block(next_question)}\n"
            f"opening_already_attempted: {'yes' if self._opening_attempted else 'no'}\n"
            f"permission_to_continue: {self._permission_state}\n"
            f"current_turn_consent_scopes: {', '.join(sorted(self._current_consent)) or 'none'}\n"
            f"caller_history_enabled: {'yes' if self.memory_enabled else 'no'}\n"
            f"caller_history_capability: {memory_guidance}\n"
            f"current_conversation_stage: {self._conversation_stage}\n"
            f"latest_caller_intent: {self._last_caller_intent}\n"
            f"latest_caller_turn_quality_hint: {self._latest_turn_quality}\n"
            f"latest_turn_guidance_hint: {self._latest_turn_guidance}\n"
            f"reply_language: {'French' if self._caller_language == 'fr' else 'English'}\n"
            f"last_ai_turn_delivery: {self._last_ai_delivery}\n"
            f"last_ai_turn_text: {last_response}\n"
            f"facts_already_collected: {known}\n"
            f"uncollected_context (discover only when natural): {missing_names}\n"
            f"optional_next_topic: {next_question}\n"
            f"strategy_hint (optional): {strategy_hint}\n"
            f"required_next_move: {required_move}\n"
            "State fields are fallible hints; the caller's latest words always win. Answer a "
            "direct question or correction before the objective. If words are unclear, ask one "
            "brief contextual clarification and do not advance task state. Do not repeat the "
            "last AI sentence, re-pitch a chosen plan, or use missing fields as a script. If "
            "latest_caller_intent is goodbye or permission_refused, close briefly with no sales "
            "question."
        )

    def _refresh_live_state(self) -> None:
        if self._live_state_message is None:
            return
        self._live_state_message["content"] = self.live_state_instructions()
        # Keep changing state immediately before the newest caller turn instead
        # of permanently at message index 1. Ollama can now reuse the stable
        # persona and dialogue prefix; only this small state block and the new
        # turn require prefill as a call grows.
        messages = getattr(self._live_context, "messages", None)
        if isinstance(messages, list):
            with contextlib.suppress(ValueError):
                messages.remove(self._live_state_message)
            # CRITICAL CONVERSATION CONTINUITY:
            # The system live state message must be placed BEFORE the user's latest turn, NOT AFTER.
            # If placed after the user turn, Ollama sees the prompt ending in a system message rather than a user prompt,
            # disrupting the conversational turn-taking dialogue structure.
            if messages and messages[-1].get('role') == 'user':
                messages.insert(len(messages) - 1, self._live_state_message)
            else:
                messages.append(self._live_state_message)

    def classify_turn(self, text: str) -> TurnQuality:
        """Decide whether this turn carries something to answer at all."""

        if looks_semantically_incomplete(text):
            return TurnQuality.FRAGMENT
        # Terminal intent and an answer to the still-pending opening permission
        # question are meaningful even when they are only one word. Do not apply
        # this to ordinary "yes"/"okay" backchannels later in the conversation.
        permission = self._classify_permission(text)
        awaiting_opening_answer = (
            self._opening_attempted
            and self._permission_state == "unknown"
            and permission != "unknown"
        )
        if self._is_goodbye(text) or awaiting_opening_answer:
            return TurnQuality.ACTIONABLE
        return classify_caller_turn(
            text,
            question_is_open=self._question_open,
            language=self.reply_language,
        )

    def matches_expected_answer(self, text: str) -> bool:
        """Whether a short utterance fills a still-open task slot."""

        return any(slot.match(text) for slot in self.task.missing_slots())

    def is_explicit_conversation_control(self, text: str) -> bool:
        """Whether a short turn intentionally controls or redirects the call."""

        return (
            self._is_goodbye(text)
            or self._is_attention_check(text)
            or self._classify_permission(text) != "unknown"
        )

    def terminal_control_kind(self, text: str) -> str | None:
        """Return the explicit call-ending intent that must outrank task progress."""

        if self._is_goodbye(text):
            return "goodbye"
        if self._opening_attempted and self._classify_permission(text) == "refused":
            return "refusal"
        return None

    def note_opening_attempted(self) -> None:
        """Record dispatch of the opening before asynchronous audio events arrive.

        Realtime input transcription can complete before the interrupted greeting's
        ``response.done`` event. Recording this at dispatch time makes an immediate
        caller refusal authoritative even in that event ordering.
        """

        self._opening_attempted = True
        if self.call_context.direction.value == "inbound":
            self._permission_state = "granted"
            self._conversation_stage = "INTENT_DISCOVERY"
        else:
            self._conversation_stage = "AWAIT_PERMISSION"
        self._question_open = True
        self._refresh_live_state()

    def terminal_response_instruction(self, kind: str) -> str:
        """Create a short response-level instruction for an immediate clean close."""

        french = self.reply_language.lower().startswith("fr")
        if kind == "goodbye":
            exact = (
                "Merci pour votre temps. Au revoir." if french else "Thanks for your time. Goodbye."
            )
        else:
            exact = (
                "Aucun problème. Je ne vais pas vous retenir. Bonne journée."
                if french
                else "No problem. I won't keep you. Have a good day."
            )
        language = "French" if french else "English"
        return (
            f"The caller clearly chose to end the call ({kind}). Say exactly this once in "
            f"{language}, naturally and completely: {exact} Do not introduce yourself, mention "
            "the company or offer, ask a question, continue selling, or add any other words."
        )

    def last_spoken_turn(self) -> str:
        """The last thing the caller actually heard, for a repeat request."""

        return self._last_ai_response.strip()

    def note_repair_delivered(self) -> None:
        self._last_ai_delivery = "repair"
        self._refresh_live_state()

    def note_turn_understood(self) -> None:
        self.repair.record_success()

    def note_turn_quality(self, quality: TurnQuality) -> None:
        """Give the model acoustic/semantic context without choosing its words."""

        guidance = {
            TurnQuality.ACTIONABLE: (
                "Respond to the caller's actual meaning directly, then continue naturally."
            ),
            TurnQuality.BACKCHANNEL: (
                "Treat this brief response in the context of your last turn; do not launch a "
                "new script or assume details they did not say."
            ),
            TurnQuality.REPEAT_REQUEST: (
                "The caller is asking you to repeat or explain your last point. Rephrase it "
                "more simply and answer any clarification; do not move to a new question."
            ),
            TurnQuality.NOT_NOW: (
                "The caller may be directly saying this is a bad time. Verify that meaning from "
                "their actual sentence; quoted advice or a hypothetical callback is not a request."
            ),
            TurnQuality.IDENTITY_CHALLENGE: (
                "The caller wants to know who you are or why you called. Answer plainly and "
                "briefly before doing anything else."
            ),
            TurnQuality.FRAGMENT: (
                "The recognizer may have captured only part of the caller's thought. Use the "
                "actual transcript and context; if it truly remains incomplete, clarify briefly."
            ),
            TurnQuality.UNINTELLIGIBLE: (
                "The caller's audio was not intelligible. Do not infer meaning; ask them "
                "briefly and naturally to repeat it."
            ),
        }[quality]
        self._latest_turn_quality = quality.value
        self._latest_turn_guidance = guidance
        self._refresh_live_state()

    def _strip_self_name_vocative(self, sentence: str) -> str:
        """Stop the agent addressing the caller by its own name.

        The prompt names the configured agent and never names the caller, so the model
        can fill the vocative slot with the only name it has. Unless the caller gave a
        name, no vocative is safer than the wrong one.
        """

        identity = self.persona_compiler.effective_identity
        own = str(identity.get("name", "")).strip()
        if not own:
            return sentence
        known = str((self.caller_memory or {}).get("name") or "").strip()
        if known and known.casefold() == own.casefold():
            return sentence
        first = re.escape(own.split()[0])
        cleaned = re.sub(rf",\s*{first}\b(?=[\s.,!?]|$)", "", sentence, flags=re.IGNORECASE)
        cleaned = re.sub(rf"^{first}\s*,\s*", "", cleaned, flags=re.IGNORECASE)
        if cleaned != sentence:
            logger.warning("Removed self-name vocative addressed to the caller")
        return cleaned.strip()

    def _is_repeat(self, sentence: str, response_id: str | None = None) -> bool:
        """Check confirmed speech plus duplicates inside this particular draft."""

        if self._repeat_authorized_epoch == self._turn_epoch:
            return False

        normalized = " ".join(
            re.sub(r"[^\wÀ-ÿ\s]", " ", sentence.casefold(), flags=re.UNICODE).split()
        )
        if len(normalized) < 12:
            return False
        previous_sentences = [*self._spoken_sentences]
        if response_id is not None:
            previous_sentences.extend(self._response_sentence_drafts.get(response_id, []))
        # Similarity, substring containment and word-set overlap are not
        # equivalence: prices, names, negation and clause order can change the
        # meaning while retaining almost every character. Only a normalized
        # exact duplicate is strong enough evidence to withhold spoken output.
        return normalized in previous_sentences

    def _is_low_value_acknowledgement(self, sentence: str) -> bool:
        """Reject a short acknowledgement that contributes no new information.

        The model often generated ``acknowledge + stale question``. Once the
        duplicate question was correctly removed, only a robotic mirror reached
        the caller. This narrow check drops that empty first sentence while
        allowing any following fact, answer, or genuinely new question through.
        """

        rendered = " ".join(sentence.strip().split())
        if not rendered or "?" in rendered or len(rendered.split()) > 24:
            return False
        if not _LOW_VALUE_ACKNOWLEDGEMENT.search(rendered):
            return False
        if _VERIFIED_VALUE_CUE.search(rendered) or re.search(r"\d", rendered):
            return False
        return True

    @staticmethod
    def _is_direct_product_question(text: str) -> bool:
        rendered = " ".join(str(text or "").split())
        return bool(_DIRECT_PRODUCT_QUESTION.search(rendered))

    def _remember_spoken(self, sentence: str, response_id: str | None = None) -> None:
        # Non-streamed responses can contain several sentences. Retain their
        # exact units as well as the complete text; otherwise streaming the
        # same response later would compare each sentence to a whole paragraph.
        parts = [sentence, *re.split(r"(?<=[.!?])\s+", sentence.strip())]
        normalized_units = list(dict.fromkeys(
            " ".join(re.sub(r"[^\wÀ-ÿ\s]", " ", part.casefold(), flags=re.UNICODE).split())
            for part in parts if part.strip()
        ))
        normalized_units = [unit for unit in normalized_units if unit]
        if not normalized_units:
            return
        if response_id is None:
            self._spoken_sentences.extend(normalized_units)
            return
        outcome = self._memory_playback_outcomes.get(response_id)
        if outcome == "completed":
            self._spoken_sentences.extend(normalized_units)
        elif outcome is None:
            self._response_sentence_drafts.setdefault(response_id, []).extend(normalized_units)
            self._bound_speech_drafts()

    def _record_turn_draft(self, response_id: str, text: str) -> None:
        if not text:
            return
        outcome = self._memory_playback_outcomes.get(response_id)
        if outcome == "completed":
            self._completed_ai_turns.append(text)
            self._remember_consent_proposal(response_id, text)
        elif outcome is None:
            self._response_turn_drafts[response_id] = text
            self._bound_speech_drafts()

    def _bound_speech_drafts(self) -> None:
        for drafts in (self._response_sentence_drafts, self._response_turn_drafts):
            while len(drafts) > 256:
                drafts.pop(next(iter(drafts)))

    def _settle_speech_draft(self, response_id: str, status: str) -> None:
        sentences = self._response_sentence_drafts.pop(response_id, [])
        text = self._response_turn_drafts.pop(response_id, "")
        if status == "completed":
            self._spoken_sentences.extend(sentences)
            if text:
                self._completed_ai_turns.append(text)
                self._remember_consent_proposal(response_id, text)

    def _remember_consent_proposal(self, response_id: str, text: str) -> None:
        if self._response_consent_epochs.get(response_id) != self._caller_transcript_number:
            return
        sequence = int(response_id.rsplit('-', 1)[-1])
        if sequence < self._latest_consent_response_sequence:
            return
        self._latest_consent_response_sequence = sequence
        self._pending_consent_proposal = proposal_from_speech(text, response_id)

    @property
    def messaging_channels(self) -> frozenset[str]:
        capabilities = {capability for name in self.available_tools
                        for capability in self.tool_capabilities.get(name, frozenset()) | builtin_capabilities(name)}
        return frozenset(capability.split('.')[0] for capability in capabilities if capability.endswith('.send'))

    def has_current_consent(self, scope: str) -> bool:
        return self.last_caller_transcript_trusted and scope in self._current_consent

    @property
    def turn_epoch(self) -> int:
        return self._turn_epoch

    def is_stale(self, epoch: int) -> bool:
        """True when the caller has spoken again since this reply began."""

        return epoch != self._turn_epoch

    def observe_speech_started(self) -> None:
        """Invalidate pending replies before waiting for recognized words.

        A provider can take hundreds of milliseconds to produce a transcript.
        That interval must not let a response to the previous clause escape.
        Final transcription still advances the epoch for providers that do not
        publish their own speech-onset frames.
        """

        self._turn_epoch += 1

        self._current_consent = frozenset()

    async def project_caller_context(self, context: LLMContext, text: str) -> LLMContext:
        """Predict a committed prompt without recording an uncommitted caller turn.

        Use the same transition as commitment on isolated conversational state.
        No event sink, memory writer, playback or tool action runs here. Full
        prompt equality at consumption remains mandatory: a changed transcript,
        confidence, tool catalog or playback state invalidates this prediction.
        """
        projected = copy.copy(self)
        # These are the mutable objects touched by observe_transcription and
        # its language/quality helpers. External runtime services are read-only
        # during that transition and must never be deep-copied or invoked.
        for name in ("task", "call_context", "repair", "_verified_actions", "_verified_actions_by_channel", "_current_action_receipts", "recent_caller_turns"):
            setattr(projected, name, copy.deepcopy(getattr(self, name)))
        projected.event_sink = None
        projected._projecting_transcription = True
        source = context.get_messages()
        messages = copy.deepcopy(source)
        result = LLMContext(messages=messages)
        projected._live_context = result
        projected._live_state_message = next(
            (messages[index] for index, message in enumerate(source)
             if message is self._live_state_message), None,
        )
        await projected.observe_transcription(text)
        result.add_message({"role": "user", "content": text})
        return result

    async def observe_transcription(
        self,
        text: str,
        *,
        language_code: str | None = None,
        trusted_for_task: bool = True,
        transcription_confidence: float | None = None,
    ) -> None:
        self._turn_epoch += 1
        self._caller_transcript_number += 1
        proposal = self._pending_consent_proposal
        self._pending_consent_proposal = None
        self._current_consent = (
            resolve_consent(text, proposal, self.messaging_channels)
            if trusted_for_task and not looks_semantically_incomplete(text) else frozenset()
        )
        self._verified_actions.clear()
        self._verified_actions_by_channel.clear()
        self._current_action_receipts.clear()
        self.last_caller_text = text.strip()
        self._repeat_authorized_epoch = (
            self._turn_epoch
            if trusted_for_task and caller_authorizes_repetition(self.last_caller_text)
            else -1
        )
        self.last_caller_transcript_trusted = trusted_for_task
        self.last_caller_transcription_confidence = transcription_confidence
        self.recent_caller_turns.append((self.last_caller_text, trusted_for_task))
        detected = language_code or detect_language(self.last_caller_text)
        await self._adopt_caller_language(detected)
        register = detect_register(self.last_caller_text)
        if register:
            self._caller_register = register
        self._turn_started_at = time.monotonic()
        turn_quality = self.classify_turn(self.last_caller_text)
        if not trusted_for_task:
            turn_quality = TurnQuality.UNINTELLIGIBLE
        self.note_turn_quality(turn_quality)
        if self._current_consent:
            await self._emit({
                'type': 'consent_evidence', 'turn_epoch': self._turn_epoch,
                'scopes': sorted(self._current_consent),
                'proposal_response_id': proposal.response_id if proposal else None,
                'source': 'completed_proposal_reply' if affirmative(text) else 'direct_request',
            })
        is_goodbye = self._is_goodbye(self.last_caller_text)
        is_attention_check = self._is_attention_check(self.last_caller_text)
        if (
            trusted_for_task
            and turn_quality is TurnQuality.ACTIONABLE
            and not (is_goodbye or is_attention_check)
        ):
            # Permission is interpreted by the conservative direct-intent
            # classifier below. A loose task-slot regex must never grant it
            # merely because a long sentence contains "okay" or "good time".
            actions = self.task.observe_caller_turn(
                self.last_caller_text,
                excluded_slots={"permission_to_continue"},
                consent_scopes=self._current_consent,
            )
            if actions.changed:
                if not self._projecting_transcription:
                    logger.info(
                        "task state task_id=%s filled=%s stage=%s->%s",
                        self.task_id,
                        sorted(actions.state_delta),
                        actions.stage_from or self.task.stage,
                        actions.stage_to or self.task.stage,
                    )
                await self._emit({"type": "task_state", **self.task.summary()})
        if is_goodbye:
            self.task.set_outcome(CallOutcome.REFUSED)
            self._permission_state = "refused"
            self._last_caller_intent = "goodbye"
            self._conversation_stage = "CLOSE"
        elif is_attention_check:
            self._last_caller_intent = "attention_check"
        elif not trusted_for_task:
            self._last_caller_intent = "uncertain_audio"
        elif turn_quality is not TurnQuality.ACTIONABLE:
            self._last_caller_intent = turn_quality.value
        elif self._opening_attempted:
            permission = self._classify_permission(self.last_caller_text)
            if permission != "unknown" and (
                self._permission_state == 'unknown' or 'continue' in self._current_consent
            ):
                if any(slot.id == "permission_to_continue" for slot in self.task.slots):
                    recorded = self.task.record("permission_to_continue", permission)
                    if recorded.changed:
                        await self._emit({"type": "task_state", **self.task.summary()})
                self._permission_state = permission
                self._last_caller_intent = f"permission_{permission}"
                self._conversation_stage = "DISCOVER" if permission == "granted" else "CLOSE"
            else:
                self._last_caller_intent = (
                    "direct_product_question"
                    if self._is_direct_product_question(self.last_caller_text)
                    else "new_information_or_question"
                )
            if self._permission_state == "granted" and self.task.stage not in {"", "OPEN"}:
                self._conversation_stage = self.task.stage
        context_changed = False
        if trusted_for_task and turn_quality is TurnQuality.ACTIONABLE:
            context_changed = self.call_context.observe_caller_turn(
                self.last_caller_text,
                permission_state=self._permission_state,
            )
        if context_changed:
            await self._emit(
                {
                    "type": "call_context",
                    "direction": self.call_context.direction.value,
                    "mode": self.call_context.mode,
                    "phase": self.call_context.phase.value,
                    "interest": self.call_context.interest.value,
                    "product_qualification_unlocked": (
                        self.call_context.product_qualification_unlocked
                    ),
                }
            )
        self._refresh_live_state()
        event: dict[str, Any] = {
            "type": "transcript",
            "role": "user",
            "text": self.last_caller_text,
            "detected_language": self._caller_language,
        }
        if transcription_confidence is not None:
            event["transcription_confidence"] = round(transcription_confidence, 3)
        if not trusted_for_task:
            event["transcription_low_confidence"] = True
        await self._emit(event)

    async def finalize_response(
        self,
        raw_text: str,
        *,
        response_kind: str = "turn",
        enforce_spoken_policy: bool = True,
    ) -> tuple[str, TurnEvaluationResult]:
        spoken_text, evaluation, _response_id = await self.finalize_response_with_identity(
            raw_text,
            response_kind=response_kind,
            enforce_spoken_policy=enforce_spoken_policy,
        )
        return spoken_text, evaluation

    async def finalize_response_with_identity(
        self,
        raw_text: str,
        *,
        response_kind: str = "turn",
        enforce_spoken_policy: bool = True,
    ) -> tuple[str, TurnEvaluationResult, str]:
        """Finalize one response and return its delivery correlation identity."""

        material_violations = []
        if enforce_spoken_policy:
            unsupported = unsupported_material_categories(raw_text, self.task_contract)
            if unsupported:
                material_violations.append('Material product claim lacks current source review: ' + ', '.join(sorted(unsupported)))
                if response_kind == 'greeting':
                    raw_text = ' '.join(clause for clause in re.split(r'(?<=[.!?])\s+', raw_text)
                                        if not unsupported_material_categories(clause, self.task_contract))
                else:
                    raw_text = ("Je n'ai pas d'information vérifiée à ce sujet."
                                if self.reply_language.lower().startswith('fr')
                                else "I don't have verified information about that yet.")
        if response_kind == 'greeting' and enforce_spoken_policy:
            identity = self.persona_compiler.effective_identity
            profile = self.persona_compiler.identity_kernel.active
            language = 'fr' if self.reply_language.lower().startswith('fr') else 'en'
            raw_text = ensure_greeting_disclosure(
                raw_text, name=identity.get('name') or 'PhoneAgent',
                disclosure=profile.core.ai_disclosure.get(language, ''), language=language,
            )
        if enforce_spoken_policy:
            spoken_text, policy_violations = PermissionGate.enforce_spoken_response(
                raw_text,
                language=self.reply_language,
                verified_actions=self._verified_actions,
                messaging_available=self.messaging_available,
                available_messaging_channels=self.messaging_channels,
                verified_actions_by_channel=self._verified_actions_by_channel,
            )
            policy_violations.extend(material_violations)
        else:
            # Native speech-to-speech audio is already on the phone by the time
            # its transcript completes. Preserve the exact spoken text for UI,
            # evaluation, and memory instead of silently rewriting the record.
            spoken_text = raw_text.strip()
            policy_violations = []
        if response_kind == "greeting" and spoken_text:
            self.note_opening_attempted()
        if spoken_text:
            # The opening greeting normally ends in a question. Without this the
            # caller's "yes" was classified as an empty backchannel and dropped.
            self._question_open = spoken_text.rstrip().endswith("?")
        self._last_ai_response = spoken_text
        self._last_ai_delivery = "generated"
        self._refresh_live_state()
        latency_ms = (
            (time.monotonic() - self._turn_started_at) * 1000 if self._turn_started_at else 0.0
        )
        evaluation = self.judge.evaluate_turn(
            caller_input=self.last_caller_text,
            ai_response=spoken_text,
            persona_data=self.persona_compiler.evaluation_persona_data,
            task_contract=self.task_contract,
            policy_violations=policy_violations,
            recent_ai_responses=tuple(self._completed_ai_turns),
            call_direction=self.call_context.direction.value,
            verified_actions=set(self._verified_actions),
        )
        metrics = {
            "turn_latency_ms": round(latency_ms, 1),
            "text_checks_passed": evaluation.passed,
            "evaluation_scope": "heuristic_text_checks",
            "fidelity": evaluation.overall_score,
            "task_score": evaluation.task_performance_score * 4,
        }
        self._response_sequence += 1
        response_id = f"response-{self._response_sequence}"
        if spoken_text:
            self._response_consent_epochs[response_id] = self._caller_transcript_number
            self._pending_playback_ids.append(response_id)
            self._remember_spoken(spoken_text, response_id)
            self._record_turn_draft(response_id, spoken_text)
        await self._emit(
            {
                "type": "transcript",
                "role": "assistant",
                "text": spoken_text,
                "metrics": metrics,
                "response_id": response_id,
                "response_kind": response_kind,
                "delivery_status": "generated",
            }
        )
        await self._emit(
            {
                "type": "evaluation",
                "score": evaluation.overall_score,
                "task_score": evaluation.task_performance_score * 4,
                "passed": evaluation.passed,
                "feedback": evaluation.feedback,
                "evaluation_scope": "heuristic_text_checks",
                "verified_actions": sorted(self._verified_actions),
                "task_id": self.task_id,
            }
        )
        self._queue_memory_turn(response_id, spoken_text, latency_ms, evaluation, response_kind)
        return spoken_text, evaluation, response_id

    def arm_terminal_completion(
        self,
        response_id: str,
        reason: str,
        sink: CallCompletionSink | None,
    ) -> None:
        """Bind hangup to one exact response's verified playout result."""

        if not response_id or response_id not in self._pending_playback_ids:
            raise ValueError("terminal response must be pending phone playback")
        if self._terminal_response_id is not None or self._terminal_completion_notified:
            raise RuntimeError("terminal completion is already armed or completed")
        self._terminal_response_id = response_id
        self._terminal_completion_reason = " ".join(reason.split())[:500]
        self._terminal_completion_sink = sink

    async def _resolve_terminal_completion(self, response_id: str, status: str) -> None:
        if response_id != self._terminal_response_id:
            return
        reason = self._terminal_completion_reason or "AI ended call"
        sink = self._terminal_completion_sink
        self._terminal_response_id = None
        self._terminal_completion_reason = ""
        self._terminal_completion_sink = None
        if status != "completed":
            await self._emit(
                {
                    "type": "terminal_completion_aborted",
                    "response_id": response_id,
                    "playback_status": status,
                    "reason": "closing was not verified as rendered on the phone",
                }
            )
            return
        if self._terminal_completion_notified:
            return
        self._terminal_completion_notified = True
        await self._emit(
            {"type": "call_completion", "response_id": response_id, "reason": reason}
        )
        if sink is None:
            return
        result = sink(reason)
        if inspect.isawaitable(result):
            await result

    def begin_streamed_response(self) -> str:
        """Reserve this turn's playback identity before any audio is produced.

        Streaming releases the first sentence to TTS while the model is still
        writing, so bot-speaking frames reach the playback reporter before the
        turn is finalized. The identity therefore has to exist up front.
        """

        self._response_sequence += 1
        self._stream_policy_violations = []
        response_id = f"response-{self._response_sequence}"
        self._response_consent_epochs[response_id] = self._caller_transcript_number
        while len(self._response_consent_epochs) > 256:
            self._response_consent_epochs.pop(next(iter(self._response_consent_epochs)))
        self._pending_playback_ids.append(response_id)
        return response_id

    @property
    def messaging_available(self) -> bool:
        return bool(self.messaging_channels) or any(
            ("send" in name or "reply" in name)
            and any(kind in name for kind in ("whatsapp", "sms", "message", "checkout"))
            for name in self.available_tools
        )

    def observe_tool_result(self, name: str, output: str, epoch: int) -> None:
        """Authorize only claims supported by this turn's specific tool result."""
        if self.is_stale(epoch):
            return
        try:
            result = json.loads(output)
        except (TypeError, json.JSONDecodeError):
            return
        if not isinstance(result, dict) or result.get("error"):
            return
        # Journalled actions have already supplied a trusted typed receipt through
        # observe_action_receipt. Do not trust a nested receipt in backend text.
        if 'action_receipt' in result:
            return
        if name == 'whatsapp_last_delivery_status' and result.get('verified') is True:
            statuses = result.get('statuses')
            if isinstance(statuses, dict):
                for receipt in list(self._action_receipts.values()):
                    status = statuses.get(receipt.provider_id)
                    if receipt.channel != 'whatsapp' or status not in {'accepted', 'confirmed_in_chat', 'delivered', 'read', 'failed'}:
                        continue
                    from dataclasses import replace
                    self.observe_action_receipt(replace(receipt, state='accepted' if status == 'confirmed_in_chat' else status), epoch)
            return
        capabilities = self.tool_capabilities.get(name, frozenset()) | builtin_capabilities(name)
        channels = {cap.split('.')[0] for cap in capabilities}
        if len(channels) == 1 or name in BUSINESS_RECEIPTS:
            channel = next(iter(channels)) if channels else 'crm'
            action = 'sent' if channels else BUSINESS_RECEIPTS[name][0]
            template = ActionReceipt(f'legacy_{name}_{epoch}', name, channel, action, turn_epoch=epoch, submitted=True)
            self.observe_action_receipt(receipt_from_result(template, result), epoch)

    def observe_action_receipt(self, receipt: ActionReceipt, epoch: int) -> None:
        previous = self._action_receipts.get(receipt.operation_id)
        progress = {'accepted': 1, 'delivered': 2, 'read': 3}
        if previous and previous.provider_id == receipt.provider_id and (
            progress.get(previous.state, 0) > progress.get(receipt.state, 0) > 0
        ):
            receipt = previous
        self._action_receipts[receipt.operation_id] = receipt
        while len(self._action_receipts) > 128:
            self._action_receipts.pop(next(iter(self._action_receipts)))
        if self.is_stale(epoch):
            return
        self._current_action_receipts[receipt.operation_id] = receipt
        by_channel: dict[str, list[set[str]]] = {}
        for current in self._current_action_receipts.values():
            by_channel.setdefault(current.channel, []).append(set(current.verified_claims))
        self._verified_actions_by_channel = {
            channel: (set.intersection(*claims) if channel in {'whatsapp', 'email', 'sms'} else set.union(*claims))
            for channel, claims in by_channel.items()
        }
        self._verified_actions = set().union(*self._verified_actions_by_channel.values())

    def guard_sentence(
        self,
        sentence: str,
        *,
        is_first: bool,
        response_kind: str = "turn",
        response_id: str | None = None,
    ) -> tuple[str, bool]:
        """Clear one sentence for speech before any of it can be heard.

        Returns the text to speak and whether the rest of the turn must be
        abandoned. Once a sentence has been spoken it cannot be recalled, so a
        guard that substitutes safe wording also ends the turn rather than
        letting the model continue from text the caller never heard.
        """

        self._last_guard_rejection = ""
        ai_identity_question = bool(_AI_IDENTITY_QUESTION.search(self.last_caller_text))
        deceptive_identity = bool(_DECEPTIVE_SPOKEN_IDENTITY.search(sentence))
        missing_disclosure = ai_identity_question and not _AI_DISCLOSURE.search(sentence)
        if response_kind == "turn" and (
            deceptive_identity or (is_first and missing_disclosure)
        ):
            self._last_guard_rejection = "identity_deception"
            disclosures = self.persona_compiler.repair_phrases(self.reply_language).get(
                "identity", []
            )
            identity = self.persona_compiler.effective_identity
            name = str(identity.get("name") or "PhoneAgent").strip()
            sentence = str(disclosures[0]).strip() if disclosures else (
                f"Je suis {name}, le représentant téléphonique IA."
                if self.reply_language.lower().startswith("fr")
                else f"I'm {name}, the AI phone representative."
            )
            self._remember_spoken(sentence, response_id)
            self._last_ai_response = sentence
            return sentence, True
        if response_kind == "turn" and is_first and ai_identity_question:
            sentence = normalize_for_speech(sentence, self.reply_language)
            self._remember_spoken(sentence, response_id)
            self._last_ai_response = sentence
            return sentence, True
        if response_kind == "turn" and is_first and not self.last_caller_transcript_trusted:
            self._last_guard_rejection = "uncertain_audio"
            sentence = "Pardon, pouvez-vous répéter ?" if self.reply_language.lower().startswith("fr") else "Sorry, could you repeat that?"
            self._remember_spoken(sentence, response_id)
            self._question_open = True
            self._last_ai_response = sentence
            return sentence, True
        if response_kind == "turn" and is_first and is_unfinished_evaluation(self.last_caller_text):
            self._last_guard_rejection = "unfinished_evaluation"
            sentence = (
                "Vous disiez ?" if self.reply_language.lower().startswith("fr")
                else "You were saying?"
            )
            self._remember_spoken(sentence, response_id)
            self._question_open = True
            self._last_ai_response = sentence
            return sentence, True
        if response_kind == "turn" and is_first and is_unfinished_preference(self.last_caller_text):
            # The object of this preference was never recognized. At the bounded
            # end of the listening window, repair the missing information rather
            # than letting a fluent model choose it for the caller.
            self._last_guard_rejection = "unfinished_preference"
            sentence = (
                "Qu'est-ce que vous préférez ?" if self.reply_language.lower().startswith("fr")
                else "What would you prefer?"
            )
            self._remember_spoken(sentence, response_id)
            self._question_open = True
            self._last_ai_response = sentence
            return sentence, True
        spoken, violations = PermissionGate.enforce_spoken_response(
            sentence,
            language=self.reply_language,
            verified_actions=self._verified_actions,
            messaging_available=self.messaging_available,
            available_messaging_channels=self.messaging_channels,
            verified_actions_by_channel=self._verified_actions_by_channel,
        )
        unsupported = unsupported_material_categories(spoken, self.task_contract)
        if unsupported:
            self._last_guard_rejection = 'unverified_material_fact'
            self._stream_policy_violations.append('Material product claim lacks current source review: ' + ', '.join(sorted(unsupported)))
            spoken = (
                "Je n'ai pas d'information vérifiée à ce sujet."
                if self.reply_language.lower().startswith('fr')
                else "I don't have verified information about that yet."
            )
            self._remember_spoken(spoken, response_id)
            return spoken, True
        self._stream_policy_violations.extend(violations)
        spoken = self._strip_self_name_vocative(spoken)
        # Never let a known duplicate reach TTS. The response processor asks
        # the model to regenerate from the latest caller meaning; it does not
        # substitute a canned sales-stage sentence here.
        if response_kind not in {"recovery_fallback", "provider_recovery", "repair"} and self._is_repeat(spoken, response_id):
            self._last_guard_rejection = "repeat"
            logger.warning("Blocked model text repeated previously: %r", spoken[:80])
            return "", True
        if (
            response_kind != "recovery_fallback"
            and self._is_low_value_acknowledgement(spoken)
        ):
            self._last_guard_rejection = "low_value_acknowledgement"
            logger.warning("Blocked mirror-only model sentence: %r", spoken[:80])
            return "", True
        if spoken:
            spoken = normalize_for_speech(spoken, self.reply_language)
            self._remember_spoken(spoken, response_id)
            self._question_open = spoken.rstrip().endswith("?")
            # Record it as it is released, not when the turn finalizes: the
            # caller can ask "hein ?" while the model is still writing, and the
            # repeat has to contain what they actually heard.
            self._last_ai_response = spoken
        return spoken, bool(violations)

    def consume_guard_rejection(self) -> str:
        """Return and clear the reason the latest sentence was rejected."""

        reason = self._last_guard_rejection
        self._last_guard_rejection = ""
        return reason

    async def finalize_streamed_response(
        self,
        response_id: str,
        spoken_text: str,
        *,
        response_kind: str = "turn",
    ) -> TurnEvaluationResult:
        """Record a turn whose sentences were already guarded and spoken."""

        if response_kind == "greeting" and spoken_text:
            self.note_opening_attempted()
        if spoken_text:
            self._question_open = spoken_text.rstrip().endswith("?")
        self._last_ai_response = spoken_text
        self._last_ai_delivery = "generated"
        self._refresh_live_state()
        latency_ms = (
            (time.monotonic() - self._turn_started_at) * 1000 if self._turn_started_at else 0.0
        )
        evaluation = self.judge.evaluate_turn(
            caller_input=self.last_caller_text,
            ai_response=spoken_text,
            persona_data=self.persona_compiler.evaluation_persona_data,
            task_contract=self.task_contract,
            policy_violations=list(self._stream_policy_violations),
            recent_ai_responses=tuple(self._completed_ai_turns),
            call_direction=self.call_context.direction.value,
            verified_actions=set(self._verified_actions),
        )
        self._record_turn_draft(response_id, spoken_text)
        await self._emit(
            {
                "type": "transcript",
                "role": "assistant",
                "text": spoken_text,
                "metrics": {
                    "turn_latency_ms": round(latency_ms, 1),
                    "text_checks_passed": evaluation.passed,
                    "evaluation_scope": "heuristic_text_checks",
                    "fidelity": evaluation.overall_score,
                    "task_score": evaluation.task_performance_score * 4,
                },
                "response_id": response_id,
                "response_kind": response_kind,
                "delivery_status": "generated",
            }
        )
        await self._emit(
            {
                "type": "evaluation",
                "score": evaluation.overall_score,
                "task_score": evaluation.task_performance_score * 4,
                "passed": evaluation.passed,
                "feedback": evaluation.feedback,
                "evaluation_scope": "heuristic_text_checks",
                "verified_actions": sorted(self._verified_actions),
                "task_id": self.task_id,
            }
        )
        self._queue_memory_turn(response_id, spoken_text, latency_ms, evaluation, response_kind)
        return evaluation

    def _queue_memory_turn(
        self, response_id: str, spoken_text: str, latency_ms: float,
        evaluation: TurnEvaluationResult, response_kind: str,
    ) -> None:
        if not (self.memory_enabled and self.last_caller_text and self.last_caller_transcript_trusted):
            return
        if response_kind == "progress":
            return
        self._pending_memory_turns[response_id] = (
            self.last_caller_text, spoken_text, latency_ms,
            evaluation.overall_score, list(evaluation.feedback),
        )
        outcome = self._memory_playback_outcomes.get(response_id)
        if outcome is not None or not spoken_text:
            self._write_resolved_memory(response_id, outcome or "not_delivered")
        while len(self._pending_memory_turns) > 256:
            self._write_resolved_memory(next(iter(self._pending_memory_turns)), "unverified")

    def _write_resolved_memory(self, response_id: str, status: str) -> None:
        self._memory_playback_outcomes[response_id] = status
        # Delivery callbacks can arrive out of order. Preserve conversational
        # order so an older caller preference cannot overwrite a correction.
        while self._pending_memory_turns:
            first_id = next(iter(self._pending_memory_turns))
            outcome = self._memory_playback_outcomes.get(first_id)
            if outcome is None:
                break
            pending = self._pending_memory_turns.pop(first_id)
            task = asyncio.create_task(self._persist_memory_after(
                self._memory_write_tail, pending, outcome,
            ))
            self._memory_write_tail = task
            self._background_tasks.add(task)
            task.add_done_callback(self._memory_task_done)

    async def _persist_memory_after(
        self, previous: asyncio.Task[None] | None,
        pending: tuple[str, str, float, float, list[str]], status: str,
    ) -> None:
        if previous is not None:
            await asyncio.gather(previous, return_exceptions=True)
        caller_text, generated_text, latency_ms, score, feedback = pending
        await self.memory_writer.process_turn_async(
            self.caller_id, caller_text, generated_text, latency_ms, score,
            self.task_id, feedback, delivery_status=status,
        )

    def _memory_task_done(self, task: asyncio.Task[None]) -> None:
        self._background_tasks.discard(task)
        if not task.cancelled() and task.exception() is not None:
            logger.error("Memory persistence failed: %s", type(task.exception()).__name__)

    def _remember_memory_playback(self, response_id: str | None, status: str) -> None:
        if response_id is None or status not in {"completed", "interrupted", "failed", "not_delivered", "unverified"}:
            return
        self._settle_speech_draft(response_id, status)
        self._memory_playback_outcomes[response_id] = status
        self._write_resolved_memory(response_id, status)
        while len(self._memory_playback_outcomes) > 256:
            self._memory_playback_outcomes.pop(next(iter(self._memory_playback_outcomes)))

    def discard_pending_playback(self, response_id: str) -> None:
        """Drop a reserved identity when the turn produced nothing to speak."""

        if response_id != self._active_playback_id:
            self._remember_memory_playback(response_id, "not_delivered")
        try:
            self._pending_playback_ids.remove(response_id)
        except ValueError:
            pass

    def preview_response(self, raw_text: str) -> str:
        """Apply hard safety and TTS normalization without scripting dialogue."""

        spoken_text, _violations = PermissionGate.enforce_spoken_response(
            raw_text,
            language=self.reply_language,
            verified_actions=set(),
        )
        return normalize_for_speech(spoken_text, self.reply_language)

    def has_pending_speech(self) -> bool:
        """Whether another non-interrupted response already has speech to deliver."""
        if self._active_playback_id is not None and not self._playback_interrupted:
            return True
        return any(
            self._response_sentence_drafts.get(response_id) or self._response_turn_drafts.get(response_id)
            for response_id in self._pending_playback_ids
        )

    async def playback_started(self, *, response_id: str | None = None) -> None:
        if response_id is not None:
            if response_id == self._active_playback_id:
                return
            if response_id not in self._pending_playback_ids:
                return
            self._pending_playback_ids.remove(response_id)
            self._active_playback_id = response_id
        if self._active_playback_id is None and self._pending_playback_ids:
            self._active_playback_id = self._pending_playback_ids.popleft()
        self._pending_consent_proposal = None
        self._playback_interrupted = False
        self._last_ai_delivery = "playing"
        self._refresh_live_state()
        if self._active_playback_id:
            await self._emit_playback_status("playing")

    async def mark_playback_interrupted(self) -> None:
        # A started response keeps its identity until the stop/ACK callback can
        # report rendered frames. Queued responses are cancelled now: leaving
        # them in the FIFO assigns the next reply's audio to an obsolete draft.
        cancelled = tuple(self._pending_playback_ids)
        self._pending_playback_ids.clear()
        if self._active_playback_id is not None:
            self._playback_interrupted = True
        self._last_ai_delivery = "interrupted"
        self._refresh_live_state()
        for response_id in cancelled:
            await self._emit_playback_status(
                "interrupted", response_id=response_id,
                message="Queued response cancelled on interruption",
            )
            await self._resolve_terminal_completion(response_id, "interrupted")

    async def playback_stopped(
        self,
        *,
        delivered_frames: int | None = None,
        dropped_frames: int = 0,
    ) -> None:
        if self._active_playback_id is None:
            return
        message = ""
        if self._playback_interrupted:
            # A caller may barge in before Android renders the first frame. That
            # is a successful interruption, not a broken phone audio route.
            status = "interrupted"
            if delivered_frames is not None:
                message = f"Caller heard approximately {delivered_frames * 20 / 1000:.2f}s"
        elif delivered_frames is not None and delivered_frames <= 0:
            # Pipecat reports bot-speaking purely from TTS audio arriving at the
            # output transport, before any write is attempted. Reporting that as
            # "played" hid a completely dead uplink behind a confident success
            # message, so delivery is now judged by frames the phone link
            # actually accepted.
            status = "not_delivered"
            message = f"No audio reached the phone; {dropped_frames} output frame(s) were dropped"
            logger.error(
                "Assistant turn produced no delivered phone audio dropped_frames=%d",
                dropped_frames,
            )
        else:
            status = "completed"
        response_id = self._active_playback_id
        self._last_ai_delivery = status
        self._refresh_live_state()
        await self._emit_playback_status(
            status, message=message,
            delivery_confirmed=delivered_frames is not None and delivered_frames > 0,
        )
        self._active_playback_id = None
        self._playback_interrupted = False
        if response_id is not None:
            await self._resolve_terminal_completion(response_id, status)

    async def playback_disconnected(self) -> None:
        """End playback accounting when the call/link ends, without inventing delivery."""
        response_id = self._active_playback_id
        self._last_ai_delivery = "interrupted"
        if response_id is not None:
            await self._emit_playback_status(
                "interrupted", message="Call disconnected before final playback confirmation",
            )
        self._active_playback_id = None
        for pending_id in self._pending_playback_ids:
            self._remember_memory_playback(pending_id, "interrupted")
        self._pending_playback_ids.clear()
        self._playback_interrupted = False
        self._refresh_live_state()
        if response_id is not None:
            await self._resolve_terminal_completion(response_id, "interrupted")

    async def playback_failed(self, message: str, *, response_id: str | None = None) -> None:
        if response_id is not None and response_id != self._active_playback_id:
            if response_id not in self._pending_playback_ids:
                return
            self._pending_playback_ids.remove(response_id)
            await self._emit_playback_status("failed", message=message, response_id=response_id)
            await self._resolve_terminal_completion(response_id, "failed")
            self._refresh_live_state()
            return
        if self._active_playback_id is None and self._pending_playback_ids:
            self._active_playback_id = self._pending_playback_ids.popleft()
        if self._active_playback_id is None:
            return
        response_id = self._active_playback_id
        await self._emit_playback_status("failed", message=message)
        self._active_playback_id = None
        self._playback_interrupted = False
        self._last_ai_delivery = "failed"
        self._refresh_live_state()
        if response_id is not None:
            await self._resolve_terminal_completion(response_id, "failed")

    async def _emit_playback_status(
        self, status: str, *, message: str = "", delivery_confirmed: bool = False,
        response_id: str | None = None,
    ) -> None:
        effective_id = response_id or self._active_playback_id
        self._remember_memory_playback(
            effective_id,
            "unverified" if status == "completed" and not delivery_confirmed else status,
        )
        await self._emit(
            {
                "type": "playback_status",
                "response_id": effective_id,
                "status": status,
                "message": message,
            }
        )

    async def close(self) -> None:
        if self._closed:
            return
        self._closed = True
        try:
            await self._finalize_closed_call()
        finally:
            await self.memory_writer.close()

    async def _finalize_closed_call(self) -> None:
        if self._active_playback_id is not None:
            await self._emit_playback_status("interrupted")
            self._active_playback_id = None
        while self._pending_playback_ids:
            self._active_playback_id = self._pending_playback_ids.popleft()
            await self._emit_playback_status("interrupted")
        self._active_playback_id = None
        for response_id in tuple(self._pending_memory_turns):
            self._write_resolved_memory(response_id, "unverified")
        if self._background_tasks:
            await asyncio.gather(*tuple(self._background_tasks), return_exceptions=True)
        if self.task.outcome is CallOutcome.IN_PROGRESS:
            # Nothing decided the ending, so classify it from what was actually
            # achieved rather than leaving the call unaccounted for.
            self.task.set_outcome(
                CallOutcome.REFUSED
                if self._permission_state == "refused"
                else CallOutcome.QUALIFIED
                if not self.task.missing_slots()
                else CallOutcome.ABANDONED
            )
        summary = self.task.summary()
        logger.info("call disposition %s", summary)
        await self._emit({"type": "call_outcome", **summary})
        if self.memory_enabled:
            await asyncio.to_thread(
                self.memory_manager.complete_call_session,
                self.caller_id,
                json.dumps(summary, ensure_ascii=False),
            )

    async def _emit(self, event: dict[str, Any]) -> None:
        if self.event_sink is None:
            return
        result = self.event_sink(event)
        if inspect.isawaitable(result):
            await result


def transcription_evidence(
    frame: TranscriptionFrame,
) -> tuple[bool, float | None, str | None]:
    """Read project-owned acoustic metadata without trusting provider payloads."""

    result = frame.result if isinstance(frame.result, dict) else {}
    metadata = result.get("phone_agent", {}) if isinstance(result, dict) else {}
    if not isinstance(metadata, dict):
        metadata = {}
    trusted = metadata.get("trusted_for_task", True) is not False
    raw_confidence = metadata.get("confidence")
    confidence = float(raw_confidence) if isinstance(raw_confidence, int | float) else None
    raw_language = metadata.get("language")
    language = str(raw_language).strip() if raw_language else None
    return trusted, confidence, language


class TranscriptionPolicyProcessor(FrameProcessor):
    """Observe final caller transcriptions without changing pipeline semantics."""

    def __init__(self, runtime: AgentPolicyRuntime) -> None:
        super().__init__()
        self.runtime = runtime

    async def process_frame(self, frame: Frame, direction: FrameDirection) -> None:
        await super().process_frame(frame, direction)
        if direction is FrameDirection.DOWNSTREAM and isinstance(frame, UserStartedSpeakingFrame):
            self.runtime.observe_speech_started()
        if direction is FrameDirection.DOWNSTREAM and isinstance(frame, TranscriptionFrame):
            trusted, confidence, language = transcription_evidence(frame)
            await self.runtime.observe_transcription(
                frame.text,
                language_code=language,
                trusted_for_task=trusted,
                transcription_confidence=confidence,
            )
        await self.push_frame(frame, direction)


# A sentence ends at terminal punctuation followed by whitespace or the end of
# what has arrived so far. Decimals and abbreviations are deliberately not
# special-cased: releasing one clause early is cheap, and the run-on guard below
# bounds the damage if a model never punctuates.
_SENTENCE_BOUNDARY = re.compile(r"[^.!?…]*[.!?…]+(?:\s+|$)", re.UNICODE)
_RUN_ON_CHARS = 160


class ResponsePolicyProcessor(FrameProcessor):
    """Stream model-owned dialogue through narrow safety checks to speech.

    Buffering the whole response before speaking made the caller wait for the
    model to finish *and then* for the full utterance to be synthesized, which
    serialized two multi-second stages that Pipecat is designed to overlap.
    Each completed sentence receives only hard action-claim safety checks and
    TTS normalization, then is pushed immediately so synthesis of sentence one
    overlaps generation of sentence two. No task stage or canned conversation
    logic may replace the model's wording. The turn is recorded once.
    """

    def __init__(self, runtime: AgentPolicyRuntime) -> None:
        super().__init__()
        self.runtime = runtime
        self._pending = ""
        self._spoken: list[str] = []
        self._collecting = False
        self._stopped = False
        self._response_id: str | None = None
        self._epoch = -1
        self._rejected_repeat = ""
        self._all_rejected_repeats: list[str] = []
        self._repeat_retry: Callable[[str, int], Awaitable[bool]] | None = None
        self._retry_resolved: Callable[[int], Awaitable[None]] | None = None
        self._speech_floor_markers = False
        self._discard_unframed = False
        self._generation_id: int | None = None
        self._generation_failed = False
        self._failed_generations: deque[int] = deque(maxlen=64)
        self._output_recovery: Callable[[ErrorFrame, int], Awaitable[None]] | None = None
        self._generation_recovery: Callable[[int], Awaitable[None]] | None = None

    def bind_output_recovery(self, handler: Callable[[ErrorFrame, int], Awaitable[None]]) -> None:
        self._output_recovery = handler

    def bind_generation_recovery(self, recovery: Callable[[int], Awaitable[None]]) -> None:
        self._generation_recovery = recovery

    def enable_speech_floor_guard(self) -> None:
        """Stamp response authorization for an output SpeechFloorGuard."""

        self._speech_floor_markers = True

    def bind_repetition_recovery(
        self,
        retry: Callable[[str, int], Awaitable[bool]],
        resolved: Callable[[int], Awaitable[None]],
    ) -> None:
        """Attach pipeline callbacks after its worker has been constructed."""

        self._repeat_retry = retry
        self._retry_resolved = resolved

    def _take_sentence(self) -> str | None:
        """Pop one complete sentence, or an early clause/bounded chunk of a reply."""

        match = _SENTENCE_BOUNDARY.match(self._pending)
        if match and match.group(0).strip():
            sentence = match.group(0)
            self._pending = self._pending[len(sentence) :]
            return sentence.strip()

        # If the sentence is not ready, allow an early clause for the first
        # chunk. Ignore a very short first identity clause and use the next
        # natural boundary instead; the old anchored regex then buffered the
        # entire introduction and serialized LLM generation with Kokoro.
        if not self._spoken:
            for boundary in re.finditer(r"[,;:]", self._pending):
                end = boundary.end()
                if end < 20:
                    continue
                if end > 72:
                    break
                if end == len(self._pending) or self._pending[end].isspace():
                    clause = self._pending[:end]
                    self._pending = self._pending[end:].lstrip()
                    return clause.strip()

        if len(self._pending) >= _RUN_ON_CHARS:
            cut = self._pending.rfind(" ", 0, _RUN_ON_CHARS)
            if cut <= 0:
                cut = _RUN_ON_CHARS
            sentence = self._pending[:cut]
            self._pending = self._pending[cut:].lstrip()
            if sentence.strip():
                return sentence.strip()
        return None

    async def _release(self, sentence: str, direction: FrameDirection) -> None:
        if self.runtime.is_stale(self._epoch):
            # The caller spoke again while this was being written. Answering the
            # older turn now would deliver two replies to one question.
            if not self._stopped:
                logger.warning("Dropped a reply superseded by a newer caller turn")
            self._stopped = True
            self._pending = ""
            return
        spoken, stop = self.runtime.guard_sentence(
            sentence, is_first=not self._spoken, response_id=self._response_id,
        )
        rejection = self.runtime.consume_guard_rejection()
        if spoken:
            self._spoken.append(spoken)
            # Each guarded unit was stripped for policy checks. Restore the
            # separator consumed by _take_sentence: otherwise TTS receives
            # "sentence.Next sentence" and buffers/reads them as one unit.
            await self.push_frame(LLMTextFrame(spoken + " "), direction)
        elif (
            rejection in {"repeat", "low_value_acknowledgement"}
            and not self._spoken
        ):
            # Remember the duplicate in case the *whole* draft contains
            # nothing new.  Do not abandon the stream yet: smaller local
            # models often prefix a useful answer with one stale sentence.
            # Dropping only that sentence preserves the useful continuation
            # and avoids paying for another generation.
            if not self._rejected_repeat:
                self._rejected_repeat = sentence
            self._all_rejected_repeats.append(sentence)
        if stop:
            # Permission failures must abandon the remaining draft because it
            # may depend on wording the caller never heard.  Repetition is
            # different: skip that sentence and inspect later sentences for
            # genuinely new content.  If none exists, end-of-response recovery
            # schedules one clean regeneration.
            if rejection in {"repeat", "low_value_acknowledgement"}:
                return
            self._stopped = True
            self._pending = ""

    def _reset(self) -> None:
        self._pending = ""
        self._spoken = []
        self._collecting = False
        self._stopped = False
        self._response_id = None
        self._epoch = -1
        self._rejected_repeat = ""
        self._all_rejected_repeats = []
        self._generation_id = None
        self._generation_failed = False

    async def _release_recovery_fallback(
        self,
        rejected: str,
        epoch: int,
        direction: FrameDirection,
        all_rejected: list[str] | None = None,
    ) -> bool:
        """Speak one observable repair after the model exhausts its retry budget.

        This is deliberately narrower than normal conversation generation: it
        runs only after both the original model draft and its quality retry were
        blocked before TTS. Deterministic code owns recovery from that failure,
        while every ordinary conversational response remains LLM-owned.
        """

        if self.runtime.is_stale(epoch):
            return False
        rejected_list = all_rejected if all_rejected is not None else self._all_rejected_repeats
        avoid_phrases = tuple(
            dict.fromkeys(
                filter(
                    None,
                    [
                        rejected,
                        *rejected_list,
                        " ".join(rejected_list),
                    ],
                )
            )
        )
        fallback = self.runtime.repair.next_repair(avoid=avoid_phrases)
        response_id = self.runtime.begin_streamed_response()
        spoken, _stop = self.runtime.guard_sentence(
            fallback,
            is_first=True,
            response_kind="recovery_fallback",
            response_id=response_id,
        )
        self.runtime.consume_guard_rejection()
        if not spoken:
            self.runtime.discard_pending_playback(response_id)
            return False
        start = LLMFullResponseStartFrame()
        start.metadata[SPEECH_TURN_EPOCH] = epoch
        start.metadata[SPEECH_RESPONSE_ID] = response_id
        await self.push_frame(start, direction)
        if self.runtime.is_stale(epoch):
            self.runtime.discard_pending_playback(response_id)
            return False
        await self.push_frame(LLMTextFrame(spoken + " "), direction)
        await self.runtime.finalize_streamed_response(
            response_id,
            spoken,
            response_kind="recovery_fallback",
        )
        await self.push_frame(LLMFullResponseEndFrame(), direction)
        logger.warning(
            "Used policy repair after model repetition recovery was exhausted epoch=%d",
            epoch,
        )
        return True

    async def process_frame(self, frame: Frame, direction: FrameDirection) -> None:
        await super().process_frame(frame, direction)
        if isinstance(frame, InterruptionFrame):
            await self.runtime.mark_playback_interrupted()
        if direction is FrameDirection.UPSTREAM and isinstance(frame, ErrorFrame):
            epoch = frame.metadata.get(SPEECH_TURN_EPOCH, self.runtime.turn_epoch)
            scoped = SYNTHESIS_CONTEXT_ID in frame.metadata
            if not scoped or (isinstance(epoch, int) and not self.runtime.is_stale(epoch)):
                response_id = frame.metadata.get(SPEECH_RESPONSE_ID)
                if (scoped and isinstance(response_id, str)
                        and response_id != self.runtime._active_playback_id
                        and response_id not in self.runtime._pending_playback_ids):
                    await self.push_frame(frame, direction)
                    return
                await self.runtime.playback_failed(
                    frame.error, response_id=response_id if isinstance(response_id, str) else None,
                )
                if self._output_recovery is not None:
                    await self._output_recovery(frame, epoch)
        if direction is not FrameDirection.DOWNSTREAM:
            await self.push_frame(frame, direction)
            return
        if isinstance(frame, InterruptionFrame | UserStartedSpeakingFrame):
            if self._response_id is not None and not self._spoken:
                self.runtime.discard_pending_playback(self._response_id)
            self._reset()
            self._discard_unframed = True
            await self.push_frame(frame, direction)
            return
        if isinstance(frame, TTSSpeakFrame) and self._speech_floor_markers:
            epoch = frame.metadata.get(SPEECH_TURN_EPOCH, self.runtime.turn_epoch)
            if self.runtime.is_stale(epoch):
                return
            await self.push_frame(SpeechFloorPermissionFrame(epoch), direction)
            await self.push_frame(frame, direction)
            return
        if isinstance(frame, TTSStartedFrame) and self._speech_floor_markers:
            frame.metadata[SPEECH_TURN_EPOCH] = self.runtime.turn_epoch
        if isinstance(frame, GenerationFailureFrame):
            if (self._collecting and frame.generation_id == self._generation_id
                    and not self.runtime.is_stale(frame.turn_epoch)):
                self._generation_failed = True
                self._failed_generations.append(frame.generation_id)
                self._pending = ""
                self._stopped = True
            return
        if isinstance(frame, LLMTextFrame | LLMFullResponseEndFrame) and GENERATION_ID in frame.metadata:
            if not self._collecting or frame.metadata[GENERATION_ID] != self._generation_id:
                return
        if isinstance(frame, LLMFullResponseStartFrame):
            generation_id = frame.metadata.get(GENERATION_ID)
            epoch = frame.metadata.get(SPEECH_TURN_EPOCH, self.runtime.turn_epoch)
            if self.runtime.is_stale(epoch) or generation_id in self._failed_generations:
                return
            self._reset()
            self._generation_id = generation_id
            self._discard_unframed = False
            self._collecting = True
            self._epoch = epoch
            frame.metadata[SPEECH_TURN_EPOCH] = self._epoch
            self._response_id = self.runtime.begin_streamed_response()
            frame.metadata[SPEECH_RESPONSE_ID] = self._response_id
            await self.push_frame(frame, direction)
            return
        if isinstance(frame, LLMTextFrame):
            if self._discard_unframed:
                return
            if not self._collecting:
                self._collecting = True
                if self._response_id is None:
                    self._response_id = self.runtime.begin_streamed_response()
            if self._stopped:
                return
            self._pending += frame.text
            while (sentence := self._take_sentence()) is not None:
                await self._release(sentence, direction)
                if self._stopped:
                    break
            return
        if isinstance(frame, LLMFullResponseEndFrame) and self._collecting:
            trailing = self._pending.strip()
            self._pending = ""
            if trailing and not self._stopped:
                await self._release(trailing, direction)
            response_id = self._response_id
            spoken_text = " ".join(self._spoken).strip()
            stale = self.runtime.is_stale(self._epoch)
            epoch = self._epoch
            rejected_repeat = self._rejected_repeat
            all_rejected_repeats = list(self._all_rejected_repeats)
            generation_failed = self._generation_failed
            self._reset()
            if stale and response_id is not None:
                self.runtime.discard_pending_playback(response_id)
                await self.push_frame(frame, direction)
                return
            if generation_failed:
                self._discard_unframed = True
                if response_id is not None:
                    if spoken_text:
                        await self.runtime.finalize_streamed_response(response_id, spoken_text, response_kind="partial_generation")
                    else:
                        self.runtime.discard_pending_playback(response_id)
                await self.push_frame(frame, direction)
                if self._generation_recovery is not None:
                    await self._generation_recovery(epoch)
                return
            if rejected_repeat and not spoken_text:
                if response_id is not None:
                    self.runtime.discard_pending_playback(response_id)
                # Close the rejected response before queuing the regenerated
                # one so frame lifecycles cannot overlap downstream.
                await self.push_frame(frame, direction)
                scheduled = False
                if self._repeat_retry is not None:
                    try:
                        scheduled = await self._repeat_retry(rejected_repeat, epoch)
                    except Exception:
                        logger.exception("Could not schedule repetition recovery")
                if not scheduled:
                    released = await self._release_recovery_fallback(
                        rejected_repeat,
                        epoch,
                        direction,
                        all_rejected=all_rejected_repeats,
                    )
                    if released and self._retry_resolved is not None:
                        await self._retry_resolved(epoch)
                    if not released and rejected_repeat and not self.runtime.is_stale(epoch):
                        logger.warning("Repetition guard bypassed gracefully; releasing original response to preserve live call continuity")
                        response_id = self.runtime.begin_streamed_response()
                        start = LLMFullResponseStartFrame()
                        start.metadata[SPEECH_TURN_EPOCH] = epoch
                        start.metadata[SPEECH_RESPONSE_ID] = response_id
                        await self.push_frame(start, direction)
                        if self.runtime.is_stale(epoch):
                            self.runtime.discard_pending_playback(response_id)
                            return
                        await self.push_frame(LLMTextFrame(rejected_repeat), direction)
                        await self.runtime.finalize_streamed_response(response_id, rejected_repeat, response_kind="turn")
                        await self.push_frame(LLMFullResponseEndFrame(), direction)
                        released = True
                return
            if response_id is not None:
                if spoken_text:
                    await self.runtime.finalize_streamed_response(response_id, spoken_text)
                else:
                    self.runtime.discard_pending_playback(response_id)
            if spoken_text and self._retry_resolved is not None:
                await self._retry_resolved(epoch)
            await self.push_frame(frame, direction)
            return
        await self.push_frame(frame, direction)


class PlaybackEventProcessor(FrameProcessor):
    """Report what the phone actually rendered, including interruptions.

    Bot-speaking frames say only that TTS produced audio; they are emitted
    before the transport attempts a write and are unaffected by its result.
    Delivery is therefore measured from the session's own transport counters
    across each speaking span.
    """

    def __init__(
        self,
        runtime: AgentPolicyRuntime,
        session: Any | None = None,
        output_transport: Any | None = None,
        *,
        playout_timeout_secs: float = 6.0,
    ) -> None:
        super().__init__()
        self.runtime = runtime
        self._session = session
        self._output_transport = output_transport
        self._playout_timeout_secs = playout_timeout_secs
        self._delivered_at_start = 0
        self._rendered_at_start = -1
        self._dropped_at_start = 0
        self._audio_end_epoch_at_start = 0

    def _counters(self) -> tuple[int, int]:
        if self._session is None:
            return 0, 0
        metrics = self._session.metrics
        return int(metrics.output_frames), int(metrics.dropped_output_frames)

    async def process_frame(self, frame: Frame, direction: FrameDirection) -> None:
        await super().process_frame(frame, direction)
        if isinstance(frame, InterruptionFrame):
            await self.runtime.mark_playback_interrupted()
        elif direction is FrameDirection.DOWNSTREAM:
            if isinstance(frame, BotStartedSpeakingFrame):
                response_id = frame.metadata.get(SPEECH_RESPONSE_ID)
                if (isinstance(response_id, str) and response_id != self.runtime._active_playback_id
                        and response_id not in self.runtime._pending_playback_ids):
                    await self.push_frame(frame, direction)
                    return
                self._delivered_at_start, self._dropped_at_start = self._counters()
                if self._session is not None:
                    self._rendered_at_start = int(
                        self._session.metrics.last_rendered_sequence
                    )
                if self._output_transport is not None:
                    self._audio_end_epoch_at_start = self._output_transport.audio_end_epoch
                response_id = frame.metadata.get(SPEECH_RESPONSE_ID)
                await self.runtime.playback_started(response_id=response_id if isinstance(response_id, str) else None)
            elif isinstance(frame, BotStoppedSpeakingFrame):
                response_id = frame.metadata.get(SPEECH_RESPONSE_ID)
                if isinstance(response_id, str) and response_id != self.runtime._active_playback_id:
                    await self.push_frame(frame, direction)
                    return
                delivered, dropped = self._counters()
                if self._session is None or self._output_transport is None:
                    await self.runtime.playback_stopped(
                        delivered_frames=(
                            None if self._session is None else delivered - self._delivered_at_start
                        ),
                        dropped_frames=dropped - self._dropped_at_start,
                    )
                else:
                    marker = await self._output_transport.wait_for_audio_end(
                        self._audio_end_epoch_at_start,
                        timeout_secs=min(1.0, self._playout_timeout_secs),
                    )
                    if marker is None:
                        if self.runtime._playback_interrupted:
                            await self.runtime.playback_stopped(
                                delivered_frames=delivered - self._delivered_at_start,
                                dropped_frames=dropped - self._dropped_at_start,
                            )
                        elif not getattr(self._session, "is_active", True):
                            await self.runtime.playback_disconnected()
                        else:
                            await self.runtime.playback_failed(
                                "Phone audio end marker was not queued for this response"
                            )
                    else:
                        generation_id, end_sequence = marker
                        result = await self._session.wait_until_rendered(
                            generation_id,
                            end_sequence,
                            timeout_secs=self._playout_timeout_secs,
                        )
                        rendered_audio_frames = max(
                            0,
                            int(self._session.metrics.last_rendered_sequence)
                            - self._rendered_at_start
                            - 1,
                        )
                        if result == "interrupted":
                            await self.runtime.mark_playback_interrupted()
                            await self.runtime.playback_stopped(
                                delivered_frames=rendered_audio_frames,
                                dropped_frames=dropped - self._dropped_at_start,
                            )
                        elif result == "completed":
                            await self.runtime.playback_stopped(
                                delivered_frames=max(1, rendered_audio_frames),
                                dropped_frames=dropped - self._dropped_at_start,
                            )
                        else:
                            await self.runtime.playback_failed(
                                "Android did not acknowledge rendering this Cascade response"
                            )
            elif isinstance(frame, ErrorFrame):
                await self.runtime.playback_failed(frame.error)
        await self.push_frame(frame, direction)
