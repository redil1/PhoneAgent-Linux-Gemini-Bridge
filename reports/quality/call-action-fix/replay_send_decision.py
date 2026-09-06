"""Replay a synthetic consent exchange through the configured LLM, never send.

OpenWA is queried only for session readiness and tool definitions. Its tool
handlers are never invoked. All confirmation results supplied to the model
are explicitly fixture data in this script, not real delivery evidence.
"""

from __future__ import annotations

import asyncio
import json
import re
from pathlib import Path
from types import SimpleNamespace

from pipecat.frames.frames import StartFrame
from pipecat.processors.aggregators.llm_response_universal import LLMContext

from phone_agent_gateway.ai_bridge.agent_policy import AgentPolicyRuntime
from phone_agent_gateway.ai_bridge.antigravity_gemini_llm import (
    AntigravityGeminiLLMService,
    _format_context_prompt,
)
from phone_agent_gateway.ai_bridge.cascade_tools import (
    CascadeToolRuntime,
    emitted_tool_instructions,
    parse_emitted_tool_call,
)


async def main():
    catalog_policy = AgentPolicyRuntime(caller_id="unknown:offline-replay", task_id="iptv_shopping_prod_v3_3", language="en-US", memory_enabled=False)
    tools = CascadeToolRuntime(policy=catalog_policy, caller_id="unknown:offline-replay", call_id="offline-replay")
    llm = AntigravityGeminiLLMService(model="gemini-2.5-flash", turn_timeout_secs=15)
    result = {"scope": "LLM decision replay; no WhatsApp send handlers invoked", "cases": []}
    try:
        catalog = await tools.start()
        result["available_tools"] = sorted(catalog)
        assert "whatsapp_send_text_current_customer" in catalog
        protocol = emitted_tool_instructions(SimpleNamespace(catalog=catalog))
        await llm.start(StartFrame())
        for language in ("en-US", "fr-FR"):
            policy = AgentPolicyRuntime(caller_id="unknown:offline-replay", task_id="iptv_shopping_prod_v3_3", language=language, memory_enabled=False, available_tools=set(catalog))
            try:
                french = language == "fr-FR"
                history = [
                    {"role": "user", "content": "Envoyez-moi cette offre." if french else "Can you send me this offer?"},
                    {"role": "assistant", "content": "Le forfait Advanced coûte 59 dollars pour douze mois. Puis-je vous envoyer les détails sur WhatsApp ?" if french else "The Advanced plan is $59 for twelve months. May I send the details on WhatsApp?"},
                    {"role": "user", "content": "Oui, s'il vous plaît." if french else "Yes, please."},
                ]
                context = LLMContext(messages=[{"role": "system", "content": policy.system_prompt}, {"role": "system", "content": protocol}, *history])
                answer = await llm._generate_gemini(_format_context_prompt(context))
                match = re.search(r"<tool_call>\s*(.*?)\s*</tool_call>", answer, re.DOTALL)
                payload = json.loads(match[1]) if match else {}
                name, arguments = parse_emitted_tool_call(payload, catalog) if payload else ("", {})
                case = {"language": language, "model_response": answer, "tool_selected": name, "argument_fields": sorted(arguments), "passed": name == "whatsapp_send_text_current_customer" and set(arguments) == {"text"}}
                if case["passed"]:
                    content = arguments["text"]
                    case["verified_offer_included"] = "59" in content and "12" in content and policy.task_contract["knowledge"]["advanced_checkout"] in content
                    context.add_message({"role": "assistant", "content": answer})
                    context.add_message({"role": "system", "content": 'Result of whatsapp_send_text_current_customer: {"accepted":true,"message_id":"fixture-only","delivery_confirmed":false,"delivery_status":"accepted","guidance":"Sent to WhatsApp; delivery is not confirmed. Do not claim delivered or read."}'})
                    confirmation = await llm._generate_gemini(_format_context_prompt(context))
                    case["fixture_confirmation_response"] = confirmation
                result["cases"].append(case)
            finally:
                await policy.close()
    finally:
        await tools.close()
        await catalog_policy.close()
        await llm.cleanup()
    Path(__file__).with_name("send-decision-replay.json").write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n")
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    asyncio.run(main())
