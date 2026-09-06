"""Paired current versus preserved pre-compaction live-state LLM latency.

Both variants use the current sanitized stable prompt and identical tool
protocol. Only the mutable live-state text changes. Real selected LLM, no STT,
TTS, phone, tool execution, memory write, or customer data.
"""
from __future__ import annotations

import asyncio
import importlib.util
import json
import statistics
import time
from pathlib import Path
from types import SimpleNamespace

from pipecat.frames.frames import StartFrame
from pipecat.processors.aggregators.llm_context import LLMContext

from phone_agent_gateway.ai_bridge.antigravity_gemini_llm import _format_context_prompt
from phone_agent_gateway.ai_bridge.pipecat_transport import (
    PhoneAgentTransport,
    PhoneAgentTransportParams,
)
from phone_agent_gateway.ai_bridge.production_pipeline import ProductionCallPipeline
from phone_agent_gateway.ai_bridge.runtime_config import ProviderConfig

ROOT = Path(__file__).resolve().parent
OLD_POLICY = Path("/private/tmp/phoneagent-corroboration-before/ai_bridge/agent_policy.py")


def preserved_policy_class():
    if not OLD_POLICY.is_file():
        raise RuntimeError("Preserved pre-compaction policy source is unavailable")
    name = "phone_agent_gateway.ai_bridge.preserved_agent_policy"
    spec = importlib.util.spec_from_file_location(name, OLD_POLICY)
    if spec is None or spec.loader is None:
        raise RuntimeError("Could not load preserved policy source")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module.AgentPolicyRuntime


def with_live_state(messages: list[dict], live_state: dict, content: str) -> list[dict]:
    return [
        {**message, "content": content if message is live_state else message.get("content", "")}
        for message in messages
    ]


def correct_price(text: str) -> bool:
    normalized = " ".join(text.casefold().replace("-", " ").split())
    return "39" in normalized or "thirty nine" in normalized


async def main() -> None:
    pipeline = ProductionCallPipeline(
        PhoneAgentTransport(PhoneAgentTransportParams()),
        SimpleNamespace(
            providers=ProviderConfig(
                stt_provider="antigravity_live",
                llm_provider="antigravity_gemini",
                tts_provider="edge_tts",
            ),
            task_id="iptv_shopping_prod_v3_3",
            sample_rate=16_000,
            memory_enabled=False,
            system_prompt="",
        ),
        caller_id="unknown:live-state-latency",
    )
    await pipeline._attach_tools()
    current = pipeline.policy
    old_type = preserved_policy_class()
    preserved = old_type(
        caller_id="unknown:live-state-latency",
        task_id="iptv_shopping_prod_v3_3",
        language="en-US",
        memory_enabled=False,
    )
    current_state = current.live_state_instructions()
    preserved_state = preserved.live_state_instructions()
    messages = list(pipeline.context.messages)
    live_state = current._live_state_message
    if live_state is None:
        raise RuntimeError("Pipeline did not attach mutable live state")
    variants = {
        "preserved": with_live_state(messages, live_state, preserved_state),
        "current": with_live_state(messages, live_state, current_state),
    }
    question = "What does the Professional plan cost?"
    service = pipeline.services.llm
    await service.start(StartFrame())
    result = {
        "scope": __doc__,
        "question": question,
        "live_state_chars": {
            "preserved": len(preserved_state),
            "current": len(current_state),
        },
        "prompt_chars": {},
        "rows": [],
    }
    try:
        for trial in range(6):
            order = ("preserved", "current") if trial % 2 == 0 else ("current", "preserved")
            for variant in order:
                context = LLMContext(
                    [*variants[variant], {"role": "user", "content": question}]
                )
                prompt = _format_context_prompt(context)
                result["prompt_chars"][variant] = len(prompt)
                started = time.perf_counter()
                response = await service._generate_gemini(prompt)
                row = {
                    "trial": trial,
                    "order": list(order),
                    "variant": variant,
                    "elapsed_ms": (time.perf_counter() - started) * 1000,
                    "response": response,
                    "correct_price": correct_price(response),
                }
                result["rows"].append(row)
                (ROOT / "live-state-prompt-latency.json").write_text(
                    json.dumps(result, ensure_ascii=False, indent=2) + "\n"
                )
                print(json.dumps(row, ensure_ascii=False), flush=True)
        result["summary"] = {
            variant: {
                "median_ms": statistics.median(
                    row["elapsed_ms"]
                    for row in result["rows"]
                    if row["variant"] == variant
                ),
                "correct": sum(
                    row["correct_price"]
                    for row in result["rows"]
                    if row["variant"] == variant
                ),
            }
            for variant in variants
        }
        (ROOT / "live-state-prompt-latency.json").write_text(
            json.dumps(result, ensure_ascii=False, indent=2) + "\n"
        )
    finally:
        await service.cleanup()
        await pipeline.services.stt.cleanup()
        await pipeline.services.tts.cleanup()
        await pipeline.cancel("live-state latency evaluation completed")
        await preserved.close()


if __name__ == "__main__":
    asyncio.run(main())
