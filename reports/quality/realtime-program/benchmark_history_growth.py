"""Synthetic history-size/recall probe; real LLM, no calls or action execution.

This is an assembled-history experiment, not a sixty-turn live conversation.
The current task/tool context is read; all replies are collected as text only.
"""
from __future__ import annotations
import asyncio
import json
import time
from pathlib import Path

from pipecat.frames.frames import StartFrame, CancelFrame
from pipecat.processors.aggregators.llm_context import LLMContext
from phone_agent_gateway.ai_bridge.agent_policy import AgentPolicyRuntime
from phone_agent_gateway.ai_bridge.antigravity_gemini_llm import AntigravityGeminiLLMService, _format_context_prompt
from phone_agent_gateway.ai_bridge.cascade_tools import CascadeToolRuntime, emitted_tool_instructions

ROOT=Path(__file__).resolve().parent


async def main():
    policy=AgentPolicyRuntime(caller_id='unknown:history-probe',task_id='iptv_shopping_prod_v3_3',language='en-US',memory_enabled=False)
    runtime=CascadeToolRuntime(policy=policy,caller_id='unknown:history-probe',call_id='history-probe')
    llm=AntigravityGeminiLLMService(model='gemini-2.5-flash')
    rows=[]
    output={'scope':__doc__,'rows':rows}
    exchanges=[
        ('I mainly watch movies at home in the evening.','We can focus on whether the available movie selection suits you.'),
        ('I am comparing options and have not decided to purchase.','Of course. You can compare the information before deciding.'),
        ('I value a clear explanation of what is included.','The important details are the content, term, total price and supported devices.'),
        ('I do not want an automatic purchase or message.','Understood. I will keep this conversation informational.'),
        ('I sometimes watch live sports on the weekend.','Then both the movie selection and the relevant live sports matter to your comparison.'),
        ('I am still considering the options you described.','Take your time. We can clarify anything that affects your decision.'),
    ]
    try:
        await runtime.start()
        system=policy.recompile_system_prompt()
        protocol=emitted_tool_instructions(runtime)
        output['base_sections']={'system_chars':len(system),'live_state_chars':len(policy.live_state_instructions()),'tool_protocol_chars':len(protocol)}
        await llm.start(StartFrame())
        for trial in range(3):
            for count in ([0,20,60] if trial%2==0 else [60,20,0]):
                context=LLMContext(messages=[{'role':'system','content':system},{'role':'system','content':protocol}])
                context.add_message({'role':'user','content':'Please remember: I want French subtitles and I have exactly one television.'})
                context.add_message({'role':'assistant','content':'You want French subtitles and have one television.'})
                for i in range(count):
                    user,assistant=exchanges[i%len(exchanges)]
                    context.add_message({'role':'user','content':user})
                    context.add_message({'role':'assistant','content':assistant})
                context.add_message({'role':'user','content':'In one short sentence, what subtitle language and how many televisions did I say I have? Do not perform any action.'})
                prompt=_format_context_prompt(context)
                start=time.perf_counter()
                row={'trial':trial,'prior_exchanges':count,'prompt_chars':len(prompt)}
                try:
                    text=await llm._generate_gemini(prompt)
                    row.update(response=text,elapsed_ms=(time.perf_counter()-start)*1000)
                except Exception as exc:
                    row.update(error=type(exc).__name__,elapsed_ms=(time.perf_counter()-start)*1000)
                rows.append(row)
                (ROOT/'history-growth-benchmark.json').write_text(json.dumps(output,indent=2)+'\n')
                print(json.dumps(row),flush=True)
    finally:
        await llm.cancel(CancelFrame())
        await runtime.close()
        await policy.close()

if __name__=='__main__':asyncio.run(main())
