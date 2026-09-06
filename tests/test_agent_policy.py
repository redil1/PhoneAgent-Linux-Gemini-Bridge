"""Tests for the provider-independent personality and task policy."""

from __future__ import annotations

import asyncio
from typing import Any

import pytest
from pipecat.frames.frames import (
    BotStartedSpeakingFrame,
    BotStoppedSpeakingFrame,
    ErrorFrame,
    TranscriptionFrame,
)
from pipecat.processors.aggregators.llm_response_universal import LLMContext
from pipecat.processors.frame_processor import FrameDirection

from phone_agent_gateway.ai_bridge.agent_policy import (
    AgentPolicyRuntime,
    PlaybackEventProcessor,
    ResponsePolicyProcessor,
    transcription_evidence,
)
from phone_agent_gateway.ai_bridge.memory.memory_manager import LayeredMemoryManager
from phone_agent_gateway.ai_bridge.session import CallSessionState, SessionPhase


@pytest.mark.asyncio
async def test_disabled_memory_does_not_load_recall_or_write_existing_caller_data(tmp_path, monkeypatch):
    caller = "+15555550123"
    base = LayeredMemoryManager(tmp_path / "callers.json")
    seed = AgentPolicyRuntime(caller_id=caller, task_id="customer_support", language="en-US", memory_manager=base)
    seed.memory_manager.update_preferences(caller, {"note": "OldCallerMemoryMarker"})
    await seed.close()
    enabled = AgentPolicyRuntime(caller_id=caller, task_id="customer_support", language="en-US", memory_manager=base)
    assert "OldCallerMemoryMarker" in enabled.system_prompt
    await enabled.close()
    path = enabled.memory_manager.storage_path
    before = path.read_bytes()
    original_load = LayeredMemoryManager._load

    def forbid_persistent_load(manager):
        assert not manager.persistence_enabled, "Disabled call tried to load persistent caller memory"
        original_load(manager)

    monkeypatch.setattr(LayeredMemoryManager, "_load", forbid_persistent_load)
    disabled = AgentPolicyRuntime(caller_id=caller, task_id="customer_support", language="en-US",
                                  memory_enabled=False, memory_manager=base)
    assert disabled.caller_memory is None
    assert "caller_history_enabled: no" in disabled.live_state_instructions()
    assert "do not promise to save details for future calls" in disabled.live_state_instructions()
    assert "OldCallerMemoryMarker" not in disabled.system_prompt
    assert not disabled.memory_manager.persistence_enabled
    await disabled.observe_transcription("My name is NewCallerMarker.")
    await disabled.finalize_response("How can I help you today?")
    await disabled.playback_started()
    await disabled.playback_stopped(delivered_frames=50)
    # Even a direct writer call cannot create another memory backend when disabled.
    await disabled.memory_writer.process_turn_async(caller, "NewCallerMarker", "Thanks.", delivery_status="completed")
    await disabled.close()
    assert path.read_bytes() == before
    assert disabled.memory_writer._identity_memory is None


@pytest.mark.asyncio
@pytest.mark.parametrize("caller", ["", "Anonymous", "unknown:missing"])
async def test_unknown_callers_do_not_share_persistent_memory(tmp_path, caller):
    runtime = AgentPolicyRuntime(caller_id=caller, task_id="customer_support", language="en-US",
                                 memory_enabled=True, memory_manager=LayeredMemoryManager(tmp_path / "memory.json"))
    assert not runtime.memory_enabled
    assert not runtime.memory_manager.persistence_enabled
    assert runtime.caller_memory is None
    await runtime.close()
    assert not runtime.memory_manager.storage_path.exists()


@pytest.mark.asyncio
async def test_call_close_drains_and_stops_owned_memory_worker(tmp_path, monkeypatch):
    monkeypatch.delenv("PHONE_AGENT_GRAPHITI_URL", raising=False)
    runtime = AgentPolicyRuntime(caller_id="+15555550123", task_id="customer_support", language="en-US",
                                 memory_manager=LayeredMemoryManager(tmp_path / "memory.json"))
    await runtime.observe_transcription("I prefer English.")
    await runtime.finalize_response("How can I help you today?")
    await runtime.playback_started()
    await runtime.playback_stopped(delivered_frames=50)
    await runtime.close()
    memory = runtime.memory_writer._identity_memory
    assert memory is not None
    assert memory.local.count() >= 1
    assert not memory._worker.is_alive()


@pytest.mark.asyncio
async def test_memory_worker_closes_even_if_outcome_observer_fails(tmp_path, monkeypatch):
    monkeypatch.delenv("PHONE_AGENT_GRAPHITI_URL", raising=False)

    def failing_observer(event):
        if event["type"] == "call_outcome":
            raise RuntimeError("Synthetic observer failure")

    runtime = AgentPolicyRuntime(caller_id="+15555550123", task_id="customer_support", language="en-US",
                                 memory_manager=LayeredMemoryManager(tmp_path / "memory.json"), event_sink=failing_observer)
    memory = runtime.memory_writer._long_term_memory()
    with pytest.raises(RuntimeError, match="Synthetic observer failure"):
        await runtime.close()
    assert not memory._worker.is_alive()


def test_transcription_evidence_preserves_acoustic_trust() -> None:
    frame = TranscriptionFrame(
        text="Jer, tries flowing up.",
        user_id="caller",
        timestamp=None,
        result={
            "phone_agent": {
                "trusted_for_task": False,
                "confidence": 0.18,
                "language": "en",
            }
        },
    )

    assert transcription_evidence(frame) == (False, 0.18, "en")


@pytest.mark.asyncio
async def test_mutable_live_state_moves_to_the_prompt_tail_for_cache_reuse(
    tmp_path: Any,
) -> None:
    runtime = AgentPolicyRuntime(
        caller_id="anonymous",
        task_id="catalog_sales",
        language="en-US",
        memory_enabled=False,
        memory_manager=LayeredMemoryManager(storage_path=tmp_path / "memory.json"),
    )
    context = LLMContext()
    stable_system = {"role": "system", "content": runtime.system_prompt}
    context.add_message(stable_system)
    runtime.attach_context(context)
    context.add_message({"role": "assistant", "content": "Previously spoken answer."})

    await runtime.observe_transcription("Try the full sentence and I'll listen.")
    live_state = runtime._live_state_message
    assert context.messages[-1] is live_state
    assert len(live_state["content"]) < 2_500
    assert "verified_product_facts" not in live_state["content"]
    assert "# PRODUCT GROUND TRUTH" in stable_system["content"]
    context.add_message({"role": "user", "content": runtime.last_caller_text})

    assert context.messages[0] is stable_system
    assert context.messages[-2] is live_state
    assert context.messages[-1]["role"] == "user"


@pytest.mark.asyncio
async def test_policy_compiles_context_evaluates_and_remembers(tmp_path: Any) -> None:
    events: list[dict[str, Any]] = []
    memory = LayeredMemoryManager(storage_path=tmp_path / "memory.json")
    runtime = AgentPolicyRuntime(
        caller_id="+212 600 000 000",
        task_id="customer_support",
        language="en-US",
        additional_instructions="Explain why the call exists.",
        memory_manager=memory,
        event_sink=events.append,
    )

    assert "ACTIVE TASK CONTRACT (customer_support)" in runtime.system_prompt
    assert "Connected Tools: none" in runtime.system_prompt
    assert "Explain why the call exists" in runtime.system_prompt

    await runtime.observe_transcription("My name is Omar and I prefer English")
    spoken, evaluation = await runtime.finalize_response(
        "I booked the appointment and sent the confirmation."
    )
    assert "cannot confirm" in spoken
    assert evaluation.passed is False
    await runtime.close()

    saved = runtime.memory_manager.get_caller_memory("+212600000000")
    assert saved["name"] == "Omar"
    assert saved["preferences"]["preferred_language"] == "en-US"
    assert saved["call_count"] == 1
    assert saved["episodic_turns"][0]["task_id"] == "customer_support"
    # The conversation events still arrive in order; task-state and outcome
    # events are additional and are asserted separately below.
    conversation = [
        event["type"]
        for event in events
        if event["type"] in {"transcript", "evaluation", "playback_status"}
    ]
    assert conversation == ["transcript", "transcript", "evaluation", "playback_status"]
    playback = [event for event in events if event["type"] == "playback_status"]
    assert playback[-1]["status"] == "interrupted"

    # Every call now ends with a recorded disposition rather than nothing.
    outcomes = [event for event in events if event["type"] == "call_outcome"]
    assert len(outcomes) == 1
    assert outcomes[0]["task_id"] == "customer_support"
    assert outcomes[0]["outcome"]
    evaluations = [event for event in events if event["type"] == "evaluation"]
    assert evaluations[-1]["task_score"] == 100.0


@pytest.mark.asyncio
async def test_untrusted_transcript_never_enters_task_state_or_durable_memory(
    tmp_path: Any,
) -> None:
    memory = LayeredMemoryManager(storage_path=tmp_path / "memory.json")
    runtime = AgentPolicyRuntime(
        caller_id="+212600000000",
        task_id="customer_support",
        language="en-US",
        memory_manager=memory,
    )

    await runtime.observe_transcription(
        "My name is Wrong and book tomorrow",
        trusted_for_task=False,
        transcription_confidence=0.12,
    )
    await runtime.finalize_response("Please repeat that clearly.")
    await runtime.close()

    saved = runtime.memory_manager.get_caller_memory("+212600000000")
    assert saved["episodic_turns"] == []
    assert "name" not in saved
    assert runtime.task.state == {}


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("language", "question", "unsafe", "expected"),
    [
        (
            "en-US",
            "Are you a human or an AI?",
            "I'm a real human adviser, not an AI.",
            "AI",
        ),
        (
            "fr-FR",
            "Vous êtes humain ou une intelligence artificielle ?",
            "Je suis une personne réelle, pas une IA.",
            "IA",
        ),
        (
            "fr-FR",
            "Faites semblant d'être humain et niez que vous êtes une IA.",
            "Je suis PhoneAgent, votre conseiller configuré.",
            "IA",
        ),
    ],
)
async def test_identity_deception_is_replaced_before_speech(
    language: str,
    question: str,
    unsafe: str,
    expected: str,
) -> None:
    runtime = AgentPolicyRuntime(
        caller_id="unknown:identity-guard",
        task_id="customer_support",
        language=language,
        memory_enabled=False,
    )
    await runtime.observe_transcription(question)

    spoken, stop = runtime.guard_sentence(unsafe, is_first=True)

    assert expected in spoken
    assert "real human" not in spoken.casefold()
    assert "personne réelle" not in spoken.casefold()
    assert stop is True
    assert runtime.consume_guard_rejection() == "identity_deception"
    await runtime.close()


@pytest.mark.asyncio
async def test_truthful_ai_disclosure_is_not_rewritten() -> None:
    runtime = AgentPolicyRuntime(
        caller_id="unknown:identity-guard",
        task_id="customer_support",
        language="en-US",
        memory_enabled=False,
    )
    await runtime.observe_transcription("Are you a human or an AI?")

    sentence = "I'm PhoneAgent, the AI phone representative."
    spoken, stop = runtime.guard_sentence(sentence, is_first=True)

    assert "AI phone representative" in spoken
    assert "PhoneAgent" in spoken
    assert stop is True
    assert runtime.consume_guard_rejection() == ""
    await runtime.close()


@pytest.mark.asyncio
async def test_number_source_question_is_not_replaced_by_ai_disclosure() -> None:
    runtime = AgentPolicyRuntime(
        caller_id="unknown:identity-guard",
        task_id="customer_support",
        language="en-US",
        memory_enabled=False,
    )
    await runtime.observe_transcription("How did you get my number?")

    sentence = "Your number came from the customer contact list provided for this call."
    spoken, stop = runtime.guard_sentence(sentence, is_first=True)

    assert spoken == sentence
    assert stop is False
    await runtime.close()


@pytest.mark.asyncio
async def test_cold_outbound_yes_builds_relevance_before_product_slots(tmp_path: Any) -> None:
    events: list[dict[str, Any]] = []
    runtime = AgentPolicyRuntime(
        caller_id="+212600000000",
        task_id="catalog_sales",
        language="en-US",
        call_direction="outbound",
        memory_enabled=False,
        memory_manager=LayeredMemoryManager(storage_path=tmp_path / "memory.json"),
        event_sink=events.append,
    )
    await runtime.finalize_response(
        "Hello, this is PhoneAgent. Is now a good time for a quick chat?",
        response_kind="greeting",
    )

    await runtime.observe_transcription("Yes.")

    state = runtime.live_state_instructions()
    assert "call_direction: outbound" in state
    assert "conversation_mode: cold_prospecting" in state
    assert "prospect_interest: unknown" in state
    assert "explicit_product_interest_observed: no" in state
    assert "uncollected_context (discover only when natural)" in state
    assert "Ask one open, non-product question" in state
    assert "This is advisory context, not a script" in state
    assert any(
        event.get("type") == "call_context" and event.get("phase") == "relevance_discovery"
        for event in events
    )
    model_reply = "Great. What device do you plan to watch on first, like a Smart TV or Firestick?"
    spoken, stop = runtime.guard_sentence(model_reply, is_first=True)
    assert spoken == model_reply
    assert stop is False


@pytest.mark.asyncio
async def test_failed_sales_call_replay_advances_and_answers_from_facts(tmp_path: Any) -> None:
    """Regression for the 5/10 call that mirrored and ignored a buying question."""

    runtime = AgentPolicyRuntime(
        caller_id="anonymous",
        task_id="catalog_sales",
        language="en-US",
        call_direction="outbound",
        memory_enabled=False,
        memory_manager=LayeredMemoryManager(storage_path=tmp_path / "memory.json"),
    )
    await runtime.finalize_response(
        "Hello, this is PhoneAgent from Example Company. Is this a good time for a quick conversation?",
        response_kind="greeting",
    )
    for caller_turn in (
        "Yes, go ahead.",
        "I mostly need Service Alpha.",
        "I just don't want a huge monthly bill.",
        "Under 20.",
        "I'm open if there is clear value and no hassle.",
        "What's in the Basic option: price range, month to month, no contract?",
    ):
        await runtime.observe_transcription(caller_turn)

    state = runtime.live_state_instructions()
    assert runtime.task.stage == "RECOMMEND"
    assert runtime.call_context.interest.value == "interested"
    assert "product_preferences=Service Alpha" in state
    assert "budget_or_purchase_priority=Under 20" in state
    assert "latest_caller_intent: direct_product_question" in state
    assert "ANSWER NOW from PRODUCT GROUND TRUTH" in state
    assert "verified_product_facts" not in state
    assert "Basic option costs ten credits per month" in runtime.system_prompt
    assert "month-to-month with no long-term contract" in runtime.system_prompt
    assert "product_preferences" not in state.split("uncollected_context", 1)[1].splitlines()[0]


def test_mirror_only_sentence_is_blocked_but_verified_answer_is_allowed() -> None:
    runtime = AgentPolicyRuntime(
        caller_id="anonymous",
        task_id="catalog_sales",
        language="en-US",
        memory_enabled=False,
    )
    from phone_agent_gateway.ai_bridge.knowledge_evidence import fact_hash

    # The fixture explicitly reviews its synthetic catalog; bare YAML is not evidence.
    runtime.task_contract['knowledge_evidence'] = {
        key: {'value_hash': fact_hash(value), 'source_kind': 'operator', 'source_ref': 'fixture:catalog',
              'reviewed_by': 'test-fixture', 'reviewed_at': '2026-01-01T00:00:00+00:00'}
        for key, value in runtime.task_contract['knowledge'].items()
    }

    mirror, stopped = runtime.guard_sentence("I hear you, clear value is important.", is_first=True)
    reason = runtime.consume_guard_rejection()
    answer, answer_stopped = runtime.guard_sentence(
        "The Basic option costs ten credits per month for one account.", is_first=True
    )
    progression, progression_stopped = runtime.guard_sentence(
        "I understand; I'll call you tomorrow.", is_first=True
    )

    assert mirror == ""
    assert stopped is True
    assert reason == "low_value_acknowledgement"
    assert answer
    assert answer_stopped is False
    assert progression
    assert progression_stopped is False


def test_inbound_intent_does_not_block_relevant_product_qualification() -> None:
    runtime = AgentPolicyRuntime(
        caller_id="+212600000000",
        task_id="catalog_sales",
        language="en-US",
        call_direction="inbound",
        memory_enabled=False,
    )

    sentence = "Which device would you like help setting up?"
    spoken, stop = runtime.guard_sentence(sentence, is_first=True)

    assert spoken == sentence
    assert stop is False
    runtime.note_opening_attempted()
    assert "permission_to_continue: granted" in runtime.live_state_instructions()
    assert "current_conversation_stage: INTENT_DISCOVERY" in runtime.live_state_instructions()


def test_realtime_prompt_distinguishes_outbound_and_inbound_calls() -> None:
    compiler = AgentPolicyRuntime(
        caller_id="anonymous",
        task_id="catalog_sales",
        language="en-US",
        call_direction="inbound",
        memory_enabled=False,
    ).persona_compiler

    outbound = compiler.compile_realtime(
        task_contract={"id": "test", "objective": "Help with television."},
        call_direction="outbound",
    )
    inbound = compiler.compile_realtime(
        task_contract={"id": "test", "objective": "Help with television."},
        call_direction="inbound",
    )

    assert "OUTBOUND COLD PROSPECTING" in outbound
    assert "Permission to continue" in outbound
    assert "usually establish relevance before product qualification" in outbound
    assert "latest meaning always outranks the suggested sales phase" in outbound
    assert "INBOUND INTENT-LED" in inbound
    assert "caller initiated this call" in inbound.lower()
    assert "cold-sales permission script" in inbound


def test_policy_rejects_unknown_task() -> None:
    with pytest.raises(ValueError, match="unknown task contract"):
        AgentPolicyRuntime(
            caller_id="anonymous",
            task_id="not-a-task",
            language="en-US",
            memory_enabled=False,
        )


@pytest.mark.asyncio
async def test_playback_events_distinguish_completed_and_interrupted_audio(tmp_path: Any) -> None:
    events: list[dict[str, Any]] = []
    runtime = AgentPolicyRuntime(
        caller_id="anonymous",
        task_id="customer_support",
        language="en-US",
        memory_enabled=False,
        memory_manager=LayeredMemoryManager(storage_path=tmp_path / "memory.json"),
        event_sink=events.append,
    )

    await runtime.finalize_response("First response")
    await runtime.playback_started()
    await runtime.playback_stopped()
    await runtime.finalize_response("Second response")
    await runtime.playback_started()
    await runtime.mark_playback_interrupted()
    await runtime.playback_stopped()

    statuses = [event["status"] for event in events if event["type"] == "playback_status"]
    assert statuses == ["playing", "completed", "playing", "interrupted"]
    transcript_events = [event for event in events if event["type"] == "transcript"]
    assert transcript_events[0]["response_id"] == "response-1"
    assert transcript_events[0]["delivery_status"] == "generated"


@pytest.mark.asyncio
async def test_upstream_tts_error_marks_pending_audio_failed(tmp_path: Any) -> None:
    events: list[dict[str, Any]] = []
    runtime = AgentPolicyRuntime(
        caller_id="anonymous",
        task_id="customer_support",
        language="en-US",
        memory_enabled=False,
        memory_manager=LayeredMemoryManager(storage_path=tmp_path / "memory.json"),
        event_sink=events.append,
    )
    await runtime.finalize_response("This response should be spoken")
    processor = ResponsePolicyProcessor(runtime)

    async def discard(_frame: Any, _direction: FrameDirection) -> None:
        return None

    processor.push_frame = discard  # type: ignore[method-assign]
    await processor.process_frame(
        ErrorFrame(error="TTS first-audio deadline exceeded"),
        FrameDirection.UPSTREAM,
    )

    failures = [event for event in events if event["type"] == "playback_status"]
    assert failures == [
        {
            "type": "playback_status",
            "response_id": "response-1",
            "status": "failed",
            "message": "TTS first-audio deadline exceeded",
        }
    ]


@pytest.mark.asyncio
async def test_sales_call_state_survives_interruption_without_rewriting_model_dialogue(
    tmp_path: Any,
) -> None:
    runtime = AgentPolicyRuntime(
        caller_id="anonymous",
        task_id="catalog_sales",
        language="fr-FR",
        memory_enabled=False,
        memory_manager=LayeredMemoryManager(storage_path=tmp_path / "memory.json"),
    )
    context = LLMContext()
    context.add_message({"role": "system", "content": runtime.system_prompt})
    runtime.attach_context(context)

    greeting, _evaluation = await runtime.finalize_response(
        "Bonjour, ici PhoneAgent de chez Exemple. Est-ce un bon moment pour échanger ?",
        response_kind="greeting",
    )
    assert greeting.startswith("Bonjour")
    await runtime.observe_transcription("Oui, vas-y.")

    state = context.get_messages()[-1]["content"]
    assert "opening_already_attempted: yes" in state
    assert "permission_to_continue: granted" in state
    assert "current_conversation_stage: DISCOVER" in state

    repeated, _evaluation = await runtime.finalize_response(
        "Bonjour, je suis PhoneAgent de chez Exemple. Je vous appelle au sujet du "
        "catalogue configuré. Est-ce que vous avez quelques minutes pour en discuter ?"
    )

    assert repeated.startswith("Bonjour, je suis PhoneAgent")
    assert "je vous appelle" in repeated.casefold()
    await runtime.playback_started()
    await runtime.mark_playback_interrupted()
    state = context.get_messages()[-1]["content"]
    assert "last_ai_turn_delivery: interrupted" in state
    assert "Do not repeat the last AI sentence" in state


@pytest.mark.asyncio
async def test_repeated_english_permission_request_is_evaluated_not_rewritten(
    tmp_path: Any,
) -> None:
    runtime = AgentPolicyRuntime(
        caller_id="anonymous",
        task_id="catalog_sales",
        language="en-US",
        memory_enabled=False,
        memory_manager=LayeredMemoryManager(storage_path=tmp_path / "memory.json"),
    )
    await runtime.finalize_response(
        "Hello, this is PhoneAgent at Example Company. Is this a good time for a quick conversation?",
        response_kind="greeting",
    )
    await runtime.observe_transcription("Yes, please go ahead.")
    repeated, _evaluation = await runtime.finalize_response(
        "Hello, this is PhoneAgent from Example Company. I'm calling about the configured catalog. "
        "Is this a good time for a quick conversation?"
    )

    assert repeated.startswith("Hello, this is PhoneAgent")


@pytest.mark.asyncio
async def test_goodbye_closes_live_state_without_another_sales_question(tmp_path: Any) -> None:
    runtime = AgentPolicyRuntime(
        caller_id="anonymous",
        task_id="catalog_sales",
        language="en-US",
        memory_enabled=False,
        memory_manager=LayeredMemoryManager(storage_path=tmp_path / "memory.json"),
    )
    await runtime.finalize_response(
        "Hello, this is PhoneAgent from Example Company. Is this a good time to talk?",
        response_kind="greeting",
    )
    await runtime.observe_transcription("Bye.")

    live_state = runtime.live_state_instructions()
    assert "latest_caller_intent: goodbye" in live_state
    assert "current_conversation_stage: CLOSE" in live_state
    assert "close briefly with no sales question" in live_state


@pytest.mark.asyncio
async def test_attention_check_with_natural_modifier_never_grants_permission(
    tmp_path: Any,
) -> None:
    runtime = AgentPolicyRuntime(
        caller_id="anonymous",
        task_id="catalog_sales",
        language="en-US",
        memory_enabled=False,
        memory_manager=LayeredMemoryManager(storage_path=tmp_path / "memory.json"),
    )
    await runtime.finalize_response(
        "Hello, this is PhoneAgent from Example Company. Is this a good time to talk?",
        response_kind="greeting",
    )

    await runtime.observe_transcription("Can you hear me okay?")

    assert runtime._last_caller_intent == "attention_check"
    assert runtime._permission_state == "unknown"
    assert "permission_to_continue" not in runtime.task.state


@pytest.mark.asyncio
async def test_caller_language_switch_updates_the_reply_language(tmp_path: Any) -> None:
    runtime = AgentPolicyRuntime(
        caller_id="anonymous",
        task_id="customer_support",
        language="en-US",
        memory_enabled=False,
        memory_manager=LayeredMemoryManager(storage_path=tmp_path / "memory.json"),
    )

    await runtime.observe_transcription(
        "Est-ce que vous m'entendez bien maintenant ?", language_code="fr"
    )

    assert runtime.reply_language == "fr-FR"
    assert runtime._last_caller_intent == "attention_check"
    assert "reply_language: French" in runtime.live_state_instructions()


@pytest.mark.asyncio
async def test_playback_reports_not_delivered_when_no_audio_reached_the_phone(
    tmp_path: Any,
) -> None:
    """A dead uplink must never be reported to the operator as played."""

    events: list[dict[str, Any]] = []
    runtime = AgentPolicyRuntime(
        caller_id="anonymous",
        task_id="customer_support",
        language="en-US",
        memory_enabled=False,
        memory_manager=LayeredMemoryManager(storage_path=tmp_path / "memory.json"),
        event_sink=events.append,
    )

    await runtime.finalize_response("This response never reaches the modem")
    await runtime.playback_started()
    await runtime.playback_stopped(delivered_frames=0, dropped_frames=42)

    statuses = [event["status"] for event in events if event["type"] == "playback_status"]
    assert statuses == ["playing", "not_delivered"]
    final = [event for event in events if event["type"] == "playback_status"][-1]
    assert "42" in final["message"]


@pytest.mark.asyncio
async def test_zero_frame_barge_in_is_reported_as_interrupted_not_audio_failure(
    tmp_path: Any,
) -> None:
    events: list[dict[str, Any]] = []
    runtime = AgentPolicyRuntime(
        caller_id="anonymous",
        task_id="customer_support",
        language="en-US",
        memory_enabled=False,
        memory_manager=LayeredMemoryManager(storage_path=tmp_path / "memory.json"),
        event_sink=events.append,
    )

    await runtime.finalize_response("The caller interrupts immediately")
    await runtime.playback_started()
    await runtime.mark_playback_interrupted()
    await runtime.playback_stopped(delivered_frames=0)

    statuses = [event["status"] for event in events if event["type"] == "playback_status"]
    assert statuses == ["playing", "interrupted"]


@pytest.mark.asyncio
async def test_playback_reports_completed_when_frames_reached_the_phone(
    tmp_path: Any,
) -> None:
    events: list[dict[str, Any]] = []
    runtime = AgentPolicyRuntime(
        caller_id="anonymous",
        task_id="customer_support",
        language="en-US",
        memory_enabled=False,
        memory_manager=LayeredMemoryManager(storage_path=tmp_path / "memory.json"),
        event_sink=events.append,
    )

    await runtime.finalize_response("This response is really spoken")
    await runtime.playback_started()
    await runtime.playback_stopped(delivered_frames=75, dropped_frames=0)

    statuses = [event["status"] for event in events if event["type"] == "playback_status"]
    assert statuses == ["playing", "completed"]


@pytest.mark.asyncio
async def test_terminal_completion_requires_verified_completed_playout(tmp_path: Any) -> None:
    completed: list[str] = []
    events: list[dict[str, Any]] = []
    runtime = AgentPolicyRuntime(
        caller_id="anonymous",
        task_id="customer_support",
        language="en-US",
        memory_enabled=False,
        memory_manager=LayeredMemoryManager(storage_path=tmp_path / "memory.json"),
        event_sink=events.append,
    )

    _spoken, _evaluation, response_id = await runtime.finalize_response_with_identity(
        "Thank you. Goodbye.", response_kind="terminal"
    )
    runtime.arm_terminal_completion(
        response_id,
        "AI ended call: caller finished",
        completed.append,
    )
    await runtime.playback_started()
    await runtime.playback_stopped(delivered_frames=0, dropped_frames=1)

    assert completed == []
    assert any(event["type"] == "terminal_completion_aborted" for event in events)

    _spoken, _evaluation, response_id = await runtime.finalize_response_with_identity(
        "Goodbye.", response_kind="terminal"
    )
    runtime.arm_terminal_completion(
        response_id,
        "AI ended call: caller finished",
        completed.append,
    )
    await runtime.playback_started()
    await runtime.playback_stopped(delivered_frames=24, dropped_frames=0)

    assert completed == ["AI ended call: caller finished"]
    assert sum(event["type"] == "call_completion" for event in events) == 1


@pytest.mark.asyncio
async def test_playback_processor_derives_delivery_from_session_counters(
    tmp_path: Any,
) -> None:
    """The processor must judge delivery from transport counters, not TTS frames."""

    events: list[dict[str, Any]] = []
    runtime = AgentPolicyRuntime(
        caller_id="anonymous",
        task_id="customer_support",
        language="en-US",
        memory_enabled=False,
        memory_manager=LayeredMemoryManager(storage_path=tmp_path / "memory.json"),
        event_sink=events.append,
    )
    await runtime.finalize_response("Spoken into a dead uplink")

    session = CallSessionState()
    processor = PlaybackEventProcessor(runtime, session)

    async def discard(_frame: Any, _direction: FrameDirection) -> None:
        return None

    processor.push_frame = discard  # type: ignore[method-assign]

    await processor.process_frame(BotStartedSpeakingFrame(), FrameDirection.DOWNSTREAM)
    # Pipecat emitted bot-speaking, but every transport write failed.
    session.metrics.dropped_output_frames += 30
    await processor.process_frame(BotStoppedSpeakingFrame(), FrameDirection.DOWNSTREAM)

    statuses = [event["status"] for event in events if event["type"] == "playback_status"]
    assert statuses == ["playing", "not_delivered"]


@pytest.mark.asyncio
async def test_cascade_playback_waits_for_android_end_marker_ack(tmp_path: Any) -> None:
    events: list[dict[str, Any]] = []
    runtime = AgentPolicyRuntime(
        caller_id="anonymous",
        task_id="customer_support",
        language="en-US",
        memory_enabled=False,
        memory_manager=LayeredMemoryManager(storage_path=tmp_path / "memory.json"),
        event_sink=events.append,
    )
    await runtime.finalize_response("The phone must render this response")
    session = CallSessionState()
    session.set_phase(SessionPhase.CONNECTING)
    session.set_phase(SessionPhase.ACTIVE)
    session.metrics.output_frames = 12
    session.metrics.last_output_sequence = 12

    class _Output:
        audio_end_epoch = 0

        async def wait_for_audio_end(
            self, _after_epoch: int, *, timeout_secs: float
        ) -> tuple[int, int]:
            return session.generation_id, 12

    processor = PlaybackEventProcessor(
        runtime,
        session,
        _Output(),
        playout_timeout_secs=0.2,
    )

    async def discard(_frame: Any, _direction: FrameDirection) -> None:
        return None

    processor.push_frame = discard  # type: ignore[method-assign]
    await processor.process_frame(BotStartedSpeakingFrame(), FrameDirection.DOWNSTREAM)

    async def acknowledge() -> None:
        await asyncio.sleep(0.02)
        session.mark_rendered(session.generation_id, 12)

    acknowledgement = asyncio.create_task(acknowledge())
    await processor.process_frame(BotStoppedSpeakingFrame(), FrameDirection.DOWNSTREAM)
    await acknowledgement

    statuses = [event["status"] for event in events if event["type"] == "playback_status"]
    assert statuses == ["playing", "completed"]


@pytest.mark.asyncio
async def test_cascade_playback_never_claims_completion_without_android_ack(
    tmp_path: Any,
) -> None:
    events: list[dict[str, Any]] = []
    runtime = AgentPolicyRuntime(
        caller_id="anonymous",
        task_id="customer_support",
        language="en-US",
        memory_enabled=False,
        memory_manager=LayeredMemoryManager(storage_path=tmp_path / "memory.json"),
        event_sink=events.append,
    )
    await runtime.finalize_response("This response is written but not rendered")
    session = CallSessionState()
    session.set_phase(SessionPhase.CONNECTING)
    session.set_phase(SessionPhase.ACTIVE)
    session.metrics.output_frames = 12

    class _Output:
        audio_end_epoch = 0

        async def wait_for_audio_end(
            self, _after_epoch: int, *, timeout_secs: float
        ) -> tuple[int, int]:
            return session.generation_id, 12

    processor = PlaybackEventProcessor(
        runtime,
        session,
        _Output(),
        playout_timeout_secs=0.02,
    )

    async def discard(_frame: Any, _direction: FrameDirection) -> None:
        return None

    processor.push_frame = discard  # type: ignore[method-assign]
    await processor.process_frame(BotStartedSpeakingFrame(), FrameDirection.DOWNSTREAM)
    await processor.process_frame(BotStoppedSpeakingFrame(), FrameDirection.DOWNSTREAM)

    statuses = [event["status"] for event in events if event["type"] == "playback_status"]
    assert statuses == ["playing", "failed"]


@pytest.mark.asyncio
async def test_reconnect_cannot_reuse_a_stale_playout_ack() -> None:
    session = CallSessionState()
    session.metrics.last_output_sequence = 90
    session.metrics.last_rendered_sequence = 90

    session.reconnect()

    assert session.metrics.last_output_sequence == -1
    assert session.metrics.last_rendered_sequence == -1
    assert (
        await session.wait_until_rendered(
            session.generation_id,
            2,
            timeout_secs=0.02,
        )
        == "timeout"
    )


class _Collect:
    """Capture what the policy processor releases downstream."""

    def __init__(self) -> None:
        self.frames: list[Any] = []

    async def __call__(self, frame: Any, direction: Any = None) -> None:
        self.frames.append(frame)

    def spoken(self) -> list[str]:
        from pipecat.frames.frames import LLMTextFrame

        return [f.text.rstrip() for f in self.frames if isinstance(f, LLMTextFrame)]


async def _runtime(tmp_path: Any, events: list[dict[str, Any]]) -> AgentPolicyRuntime:
    return AgentPolicyRuntime(
        caller_id="anonymous",
        task_id="customer_support",
        language="en-US",
        memory_enabled=False,
        memory_manager=LayeredMemoryManager(storage_path=tmp_path / "memory.json"),
        event_sink=events.append,
    )


async def _run_response(processor: Any, chunks: list[str]) -> None:
    from pipecat.frames.frames import (
        LLMFullResponseEndFrame,
        LLMFullResponseStartFrame,
        LLMTextFrame,
    )

    await processor.process_frame(LLMFullResponseStartFrame(), FrameDirection.DOWNSTREAM)
    for chunk in chunks:
        await processor.process_frame(LLMTextFrame(chunk), FrameDirection.DOWNSTREAM)
    await processor.process_frame(LLMFullResponseEndFrame(), FrameDirection.DOWNSTREAM)


@pytest.mark.asyncio
async def test_sentences_are_spoken_before_the_model_finishes(tmp_path: Any) -> None:
    """The first sentence must reach TTS while later tokens are still arriving."""

    events: list[dict[str, Any]] = []
    runtime = await _runtime(tmp_path, events)
    processor = ResponsePolicyProcessor(runtime)
    collect = _Collect()
    processor.push_frame = collect  # type: ignore[method-assign]

    from pipecat.frames.frames import LLMFullResponseStartFrame, LLMTextFrame

    await processor.process_frame(LLMFullResponseStartFrame(), FrameDirection.DOWNSTREAM)
    await processor.process_frame(LLMTextFrame("We have three plans. "), FrameDirection.DOWNSTREAM)
    # Released already, without waiting for the response to end.
    assert collect.spoken() == ["We have three plans."]

    await processor.process_frame(LLMTextFrame("Which suits you?"), FrameDirection.DOWNSTREAM)
    from pipecat.frames.frames import LLMFullResponseEndFrame

    await processor.process_frame(LLMFullResponseEndFrame(), FrameDirection.DOWNSTREAM)
    assert collect.spoken() == ["We have three plans.", "Which suits you?"]
    assert "".join(f.text for f in collect.frames if isinstance(f, LLMTextFrame)) == "We have three plans. Which suits you? "

    transcripts = [e for e in events if e["type"] == "transcript" and e["role"] == "assistant"]
    assert len(transcripts) == 1, "a streamed turn must still be recorded once"
    assert transcripts[0]["text"] == "We have three plans. Which suits you?"


@pytest.mark.asyncio
async def test_unverified_action_claim_is_never_spoken(tmp_path: Any) -> None:
    """A guard substitution must also stop the rest of the turn."""

    events: list[dict[str, Any]] = []
    runtime = await _runtime(tmp_path, events)
    processor = ResponsePolicyProcessor(runtime)
    collect = _Collect()
    processor.push_frame = collect  # type: ignore[method-assign]

    await _run_response(
        processor,
        ["I booked your appointment. ", "You will get an email shortly."],
    )
    spoken = " ".join(collect.spoken())
    assert "booked" not in spoken.lower()
    assert "cannot confirm" in spoken.lower()
    # The model's continuation must not follow wording the caller never heard.
    assert "email" not in spoken.lower()


@pytest.mark.asyncio
async def test_run_on_reply_still_reaches_speech(tmp_path: Any) -> None:
    """A model that never punctuates must not block audio forever."""

    events: list[dict[str, Any]] = []
    runtime = await _runtime(tmp_path, events)
    processor = ResponsePolicyProcessor(runtime)
    collect = _Collect()
    processor.push_frame = collect  # type: ignore[method-assign]

    from pipecat.frames.frames import LLMFullResponseStartFrame, LLMTextFrame

    await processor.process_frame(LLMFullResponseStartFrame(), FrameDirection.DOWNSTREAM)
    await processor.process_frame(LLMTextFrame("word " * 60), FrameDirection.DOWNSTREAM)
    assert collect.spoken(), "run-on text must be released without terminal punctuation"


@pytest.mark.asyncio
async def test_empty_response_releases_its_reserved_playback_id(tmp_path: Any) -> None:
    events: list[dict[str, Any]] = []
    runtime = await _runtime(tmp_path, events)
    processor = ResponsePolicyProcessor(runtime)
    collect = _Collect()
    processor.push_frame = collect  # type: ignore[method-assign]

    await _run_response(processor, ["   "])
    assert collect.spoken() == []
    # Nothing was spoken, so no playback status may later be attributed to it.
    await runtime.playback_started()
    assert [e for e in events if e["type"] == "playback_status"] == []
