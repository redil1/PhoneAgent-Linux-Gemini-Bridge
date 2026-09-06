"""Offline queue/protocol probes; no providers, messages, or phone calls."""

from __future__ import annotations

import asyncio
import json
from pathlib import Path
from types import SimpleNamespace

from pipecat.frames.frames import (
    LLMFullResponseEndFrame, LLMFullResponseStartFrame, LLMTextFrame, TTSSpeakFrame,
)
from pipecat.pipeline.pipeline import Pipeline
from pipecat.pipeline.worker import PipelineWorker
from pipecat.processors.aggregators.llm_response_universal import LLMContext
from pipecat.processors.frame_processor import FrameProcessor
from pipecat.workers.runner import WorkerRunner

from phone_agent_gateway.ai_bridge.agent_policy import AgentPolicyRuntime
from phone_agent_gateway.ai_bridge.cascade_tools import ToolCallProcessor
from phone_agent_gateway.ai_bridge.speculative_turn import SpeculativeTurnCoordinator
from phone_agent_gateway.ai_bridge.tasks.tool_catalog import RealtimeTool


async def preamble_probe():
    started = asyncio.Event()
    entered = asyncio.Event()
    release = asyncio.Event()
    heard = asyncio.Event()
    order = []

    class Runtime:
        catalog = {"lookup": RealtimeTool(name="lookup", definition={"name": "lookup", "parameters": {"properties": {}}}, handler=lambda _: {})}

        async def execute(self, name, arguments):
            order.append("tool_started")
            entered.set()
            await release.wait()
            order.append("tool_finished")
            return '{}'

    class Forward(FrameProcessor):
        async def process_frame(self, frame, direction):
            await super().process_frame(frame, direction)
            await self.push_frame(frame, direction)

    class Sink(FrameProcessor):
        async def process_frame(self, frame, direction):
            await super().process_frame(frame, direction)
            if isinstance(frame, TTSSpeakFrame):
                order.append("preamble_reached_speech_output")
                heard.set()
            await self.push_frame(frame, direction)

    async def preamble(_):
        order.append("preamble_queued")
        await worker.queue_frame(TTSSpeakFrame("One moment, let me check that."))

    processor = ToolCallProcessor(Runtime(), context=LLMContext(), llm=object(), preamble=preamble)
    worker = PipelineWorker(Pipeline([Forward(), processor, Sink()]), enable_rtvi=False)

    @worker.event_handler("on_pipeline_started")
    async def on_started(*_):
        started.set()

    runner = WorkerRunner(handle_sigint=False, handle_sigterm=False)
    await runner.add_workers(worker)
    task = asyncio.create_task(runner.run())
    try:
        await asyncio.wait_for(started.wait(), 2)
        await worker.queue_frames([LLMFullResponseStartFrame(), LLMTextFrame('<tool_call>{"name":"lookup","arguments":{}}</tool_call>'), LLMFullResponseEndFrame()])
        await asyncio.wait_for(entered.wait(), 2)
        await asyncio.sleep(0.08)
        before = heard.is_set()
        release.set()
        await asyncio.wait_for(heard.wait(), 2)
        return {"preamble_reached_output_while_tool_waited": before, "event_order": order}
    finally:
        release.set()
        await runner.cancel("offline probe complete")
        await asyncio.wait_for(task, 3)


async def speculative_tool_speech_probe():
    requests = []
    raw = '<tool_call>{"name":"whatsapp_send_text_current_customer","arguments":{"text":"Example offer"}}</tool_call>'

    class LLM:
        def start_prefetch(self, _):
            async def result():
                return raw
            return asyncio.create_task(result())

        def cancel_prefetch(self, _):
            pass

    class TTS:
        async def prefetch_text(self, text):
            requests.append(text)

        def clear_prefetch(self):
            pass

    preview = lambda text: AgentPolicyRuntime.preview_response(SimpleNamespace(reply_language="en-US"), text)
    coordinator = SpeculativeTurnCoordinator(context=LLMContext(), llm=LLM(), tts=TTS(), policy=SimpleNamespace(preview_response=preview))
    await coordinator.consider("Yes, please.")
    await coordinator._task
    await coordinator.close()
    return {"tts_prefetch_requests": requests, "tool_only_response_synthesized": bool(requests)}


async def main():
    result = {"scope": "Offline control-flow evidence, not a latency benchmark", "preamble": await preamble_probe(), "speculation": await speculative_tool_speech_probe()}
    Path(__file__).with_name("offline-probe-results.json").write_text(json.dumps(result, indent=2) + '\n')
    print(json.dumps(result, indent=2))


if __name__ == '__main__':
    asyncio.run(main())
