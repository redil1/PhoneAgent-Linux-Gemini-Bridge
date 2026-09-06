"""Continuous VAD checks across PCM alignment and interleaved non-speech.

No cloud or phone I/O. Simulated audio time measures detection; all input is
synthetic. Keeps real Silero state across a repeated speech/control sequence.
"""
from __future__ import annotations

import asyncio
import importlib.util
import json
import time
from pathlib import Path

import numpy as np
from pipecat.audio.vad.vad_analyzer import VADParams, VADState

from phone_agent_gateway.ai_bridge.streaming_silero_vad import StreamingSileroVADAnalyzer

ROOT = Path(__file__).resolve().parent
spec = importlib.util.spec_from_file_location('sensitivity', ROOT/'benchmark_vad_sensitivity.py')
fixtures = importlib.util.module_from_spec(spec)
spec.loader.exec_module(fixtures)


async def alignment(confidence, pcm, gain, start_secs):
    rows = []
    for phase in range(0,160,20):
        vad = StreamingSileroVADAnalyzer(sample_rate=16000, params=VADParams(confidence=confidence,start_secs=start_secs,stop_secs=0.2,min_volume=0))
        vad.set_sample_rate(16000)
        source = (np.frombuffer(pcm,dtype=np.int16)*gain).astype(np.int16).tobytes()
        prefix_ms = 1000+phase
        signal = bytes(prefix_ms*32)+source+bytes(32000)
        first = None
        try:
            for offset in range(0,len(signal),640):
                state = await vad.analyze_audio(signal[offset:offset+640])
                if first is None and offset>=prefix_ms*32 and state is VADState.SPEAKING:
                    first = (offset+640)/32-prefix_ms
            rows.append({'phase_ms':phase,'onset_ms':first})
        finally:
            await vad.cleanup()
            vad._executor.shutdown(wait=False)
    return rows


async def continuous(confidence, start_secs):
    cases = fixtures.fixtures()
    # Each speech utterance is followed by every non-speech control. Repeated
    # rounds test recurrent state rather than resetting before each sample.
    speech = [c for c in cases if c['speech'] and c.get('level')=='clean']
    controls = [c for c in cases if not c['speech']]
    vad = StreamingSileroVADAnalyzer(sample_rate=16000, params=VADParams(confidence=confidence,start_secs=start_secs,stop_secs=0.2,min_volume=0))
    vad.set_sample_rate(16000)
    false_cases = []
    input_bytes = 0
    shapes = set()
    started = time.perf_counter()
    try:
        for round_index in range(3):
            for phrase in speech:
                for case in [phrase,*controls]:
                    detected = False
                    for offset in range(0,len(case['pcm']),640):
                        chunk = case['pcm'][offset:offset+640]
                        state = await vad.analyze_audio(chunk)
                        input_bytes += len(chunk)
                        if not case['speech'] and state is VADState.SPEAKING:
                            detected = True
                    if detected:
                        false_cases.append({'round':round_index,'after':phrase['case'],'control':case['case']})
                    shapes.add((tuple(vad._model._state.shape), tuple(vad._model._context.shape)))
        return {'audio_seconds':input_bytes/32000,'processing_seconds':time.perf_counter()-started,
                'non_speech_cases':len(controls)*len(speech)*3,'control_ignore_prefix_ms':0,'false_cases':false_cases,
                'state_shapes':list(shapes)}
    finally:
        await vad.cleanup()
        vad._executor.shutdown(wait=False)


async def main():
    pcm = (ROOT/'fixtures/fr_refusal_clarification.pcm').read_bytes()
    result = {'scope':__doc__,'profiles':[]}
    for confidence, start_secs in ((0.7,0.12),(0.6,0.12),(0.6,0.096)):
        item = {'confidence':confidence,'start_secs':start_secs,
                'alignment_clean':await alignment(confidence,pcm,1.0,start_secs),
                'alignment_quiet':await alignment(confidence,pcm,10**(-10/20),start_secs),
                'continuous':await continuous(confidence,start_secs)}
        result['profiles'].append(item)
        (ROOT/'vad-continuous.json').write_text(json.dumps(result,indent=2)+'\n')
        print(json.dumps(item),flush=True)


if __name__=='__main__':
    asyncio.run(main())
