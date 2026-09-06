"""Replay the real French correction with controlled recurrent-state reset timing.

Uses real Silero inference and simulated PCM time, no cloud or phone. Only the
periodic reset trigger changes; VAD confidence and duration remain0.7/120ms.
"""
from __future__ import annotations

import asyncio
import json
import time
from pathlib import Path

from pipecat.audio.vad.silero import SileroVADAnalyzer
from pipecat.audio.vad.vad_analyzer import VADParams, VADState

ROOT = Path(__file__).resolve().parent


async def probe(service_type, reset_after_onset_ms):
    source = (ROOT/'fixtures/fr_refusal_clarification.pcm').read_bytes()
    pcm = bytes(32000)+source+bytes(32000)
    vad = service_type(sample_rate=16000, params=VADParams(confidence=0.7,start_secs=0.12,stop_secs=0.2,min_volume=0))
    vad.set_sample_rate(16000)
    original = vad.voice_confidence
    offset = 0
    injected = False
    trace = []
    def predict(audio):
        nonlocal injected
        audio_ms = (offset+640)/32
        if reset_after_onset_ms is not None and not injected and audio_ms >= 1000+reset_after_onset_ms:
            vad._last_reset_time = 0
            injected = True
        elif not injected:
            vad._last_reset_time = time.time()
        previous = vad._last_reset_time
        value = original(audio)
        trace.append({'at_ms':audio_ms-1000,'reset':vad._last_reset_time!=previous,'confidence':float(value.item()) if hasattr(value,'item') else float(value),'state_before':vad._vad_state.name})
        return value
    vad.voice_confidence = predict
    first = None
    try:
        for offset in range(0,len(pcm),640):
            state = await vad.analyze_audio(pcm[offset:offset+640])
            if first is None and offset>=32000 and state is VADState.SPEAKING:
                first = (offset+640)/32-1000
        return {'reset_after_onset_ms':reset_after_onset_ms,'first_speech_ms':first,'trace':trace}
    finally:
        await vad.cleanup()
        vad._executor.shutdown(wait=False)


async def main():
    result = {'scope':__doc__,'baseline':[]}
    for phase in [None,0,40,80,120,160,200,240,280]:
        row = await probe(SileroVADAnalyzer,phase)
        result['baseline'].append(row)
        print(json.dumps({k:v for k,v in row.items() if k!='trace'}),flush=True)
    from phone_agent_gateway.ai_bridge.streaming_silero_vad import StreamingSileroVADAnalyzer
    result['streaming'] = [await probe(StreamingSileroVADAnalyzer, phase) for phase in [None,0,40,80,120,160,200,240,280]]
    print(json.dumps({'streaming':[{k:v for k,v in r.items() if k!='trace'} for r in result['streaming']]}),flush=True)
    (ROOT/'vad-reset-phase.json').write_text(json.dumps(result,indent=2)+'\n')


if __name__=='__main__':
    asyncio.run(main())
