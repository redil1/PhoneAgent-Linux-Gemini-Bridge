"""Compare partial-prefetch failure semantics against the preserved baseline."""
import asyncio
import importlib.util
import json
import sys
from pathlib import Path

from pipecat.frames.frames import ErrorFrame, TTSAudioRawFrame

from phone_agent_gateway.ai_bridge.edge_tts_service import EdgeTTSService


async def probe(service_type):
    service = service_type()
    ready, release = asyncio.Event(), asyncio.Event()
    async def decoder(_text, stream):
        stream.append(bytes(640))
        ready.set()
        await release.wait()
        return b""  # Existing decoder failure contract, after PCM prefix was emitted.
    service._decode_edge_pcm = decoder
    preparing = asyncio.create_task(service.prefetch_text("Hello."))
    await ready.wait()
    speaking = service.run_tts("Hello.", "probe")
    first = await anext(speaking)
    release.set()
    frames = [first, *[frame async for frame in speaking]]
    await preparing
    return {'audio_chunks': sum(isinstance(f, TTSAudioRawFrame) for f in frames),
            'reported_errors': sum(isinstance(f, ErrorFrame) for f in frames)}


async def main():
    spec = importlib.util.spec_from_file_location(
        'phone_agent_gateway.ai_bridge.tts_before_failure_recovery',
        '/private/tmp/phoneagent-tts-recovery-before/ai_bridge/edge_tts_service.py',
    )
    baseline = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = baseline
    spec.loader.exec_module(baseline)
    result = {'scope': 'Controlled decoder failure after streamed PCM prefix; no network or phone output',
              'before': await probe(baseline.EdgeTTSService), 'after': await probe(EdgeTTSService)}
    Path(__file__).with_name('tts-partial-prefetch-probe.json').write_text(json.dumps(result, indent=2)+'\n')
    print(json.dumps(result, indent=2))


if __name__ == '__main__':
    asyncio.run(main())
