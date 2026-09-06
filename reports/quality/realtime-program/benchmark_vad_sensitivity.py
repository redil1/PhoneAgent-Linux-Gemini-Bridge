"""Offline Silero sensitivity screen on synthetic EN/FR and non-speech controls.

Audio timestamps, not processing wall time, measure onset. Each case starts with
fresh model state; this screen does not establish echo robustness or phone latency.
"""
from __future__ import annotations

import asyncio
import hashlib
import importlib.util
import json
import time
import wave
from pathlib import Path

import numpy as np
from pipecat.audio.vad.silero import SileroVADAnalyzer
from pipecat.audio.vad.vad_analyzer import VADParams, VADState

ROOT = Path(__file__).resolve().parent
spec = importlib.util.spec_from_file_location('vad_matrix', ROOT/'benchmark_bilingual_noise_sequences.py')
matrix = importlib.util.module_from_spec(spec)
spec.loader.exec_module(matrix)


def fixtures():
    cases = []
    for case in matrix.make_cases():
        for level in ('clean','20','10'):
            source = case['pcm']
            mixed, _ = matrix.with_noise(source, 2*16000, None if level=='clean' else float(level), case['noise_seed'])
            # Leading noise has the same stationary realization/level as the tail.
            lead = mixed[-32000:]
            cases.append({'case':case['case'],'level':level,'language':case['language'],
                          'speech':True,'speech_start_ms':1000,'pcm':lead+mixed,
                          'source_sha256':case['source_sha256']})
    correction = (ROOT/'fixtures/fr_refusal_clarification.pcm').read_bytes()
    for level, gain in [('clean',1.0),('minus10db',10**(-10/20))]:
        scaled = (np.frombuffer(correction,dtype=np.int16)*gain).astype(np.int16).tobytes()
        cases.append({'case':'fr_refusal_clarification','level':level,'language':'fr-FR',
                      'speech':True,'speech_start_ms':1000,'pcm':bytes(32000)+scaled+bytes(32000),
                      'source_sha256':hashlib.sha256(scaled).hexdigest()})
    corpus = ROOT.parents[2]/'qualification/corpus/v1'
    manifest = json.loads((corpus/'manifest.json').read_text())
    for name in ('silence','noise_only'):
        audio = next(s['audio'] for s in manifest['scenarios'] if s['id']==name)
        path = corpus/audio['path']
        assert hashlib.sha256(path.read_bytes()).hexdigest()==audio['sha256']
        with wave.open(str(path)) as source:
            pcm = source.readframes(source.getnframes())
        cases.append({'case':name,'level':'control','speech':False,'pcm':pcm})
    rng = np.random.default_rng(621)
    duration = 6*16000
    t = np.arange(duration)/16000
    for name, samples in [
        ('tone', 4000*np.sin(2*np.pi*440*t)),
        ('dual_tone', 2000*(np.sin(2*np.pi*350*t)+np.sin(2*np.pi*440*t))),
        ('modulated_noise', rng.normal(0,2500,duration)*(0.5+0.5*np.sin(2*np.pi*4*t))),
        ('clicks', np.where((np.arange(duration)%8000)<100,12000,0)),
    ]:
        cases.append({'case':name,'level':'control','speech':False,'pcm':samples.astype(np.int16).tobytes()})
    return cases


async def probe(case, confidence, start_secs):
    vad = SileroVADAnalyzer(sample_rate=16000, params=VADParams(confidence=confidence,start_secs=start_secs,stop_secs=0.20,min_volume=0.0))
    vad.set_sample_rate(16000)
    states, scores = [], []
    predict = vad.voice_confidence
    offset = 0
    def score(pcm):
        value = predict(pcm)
        scores.append({'at_ms':(offset+640)/32,'value':float(np.asarray(value).reshape(-1)[0])})
        return value
    vad.voice_confidence = score
    started = time.perf_counter()
    previous = VADState.QUIET
    try:
        for offset in range(0,len(case['pcm']),640):
            state = await vad.analyze_audio(case['pcm'][offset:offset+640])
            if state!=previous:
                states.append({'at_ms':(offset+640)/32,'state':state.name})
                previous = state
        onsets = [s['at_ms'] for s in states if s['state']=='SPEAKING']
        source_start = case.get('speech_start_ms',0)
        after = [v-source_start for v in onsets if v>=source_start]
        return {**{k:v for k,v in case.items() if k!='pcm'}, 'confidence':confidence,'start_secs':start_secs,
                'audio_ms':len(case['pcm'])/32, 'first_onset_ms':after[0] if after else None,
                'pre_speech_false_start':any(v<source_start for v in onsets),
                'non_speech_false_start':not case['speech'] and bool(onsets),
                'processing_ms':(time.perf_counter()-started)*1000,'states':states,'scores':scores}
    finally:
        await vad.cleanup()
        vad._executor.shutdown(wait=False)


async def main():
    cases = fixtures()
    result = {'scope':__doc__,'profiles':[]}
    for confidence, start in [(0.7,0.12),(0.6,0.12),(0.5,0.12),(0.7,0.08),(0.6,0.08),(0.5,0.08)]:
        rows = [await probe(case,confidence,start) for case in cases]
        speech = [r for r in rows if r['speech']]
        onsets = [r['first_onset_ms'] for r in speech if r['first_onset_ms'] is not None]
        summary = {'confidence':confidence,'start_secs':start,'speech_cases':len(speech),
                   'missed_speech':sum(r['first_onset_ms'] is None for r in speech),
                   'pre_speech_false_cases':sum(r['pre_speech_false_start'] for r in speech),
                   'non_speech_false_cases':sum(r['non_speech_false_start'] for r in rows),
                   'median_onset_ms':float(np.median(onsets)), 'p95_onset_ms':float(np.percentile(onsets,95)),
                   'fr_correction':[{k:r[k] for k in ('level','first_onset_ms')} for r in rows if r['case']=='fr_refusal_clarification']}
        result['profiles'].append({'summary':summary,'rows':rows})
        (ROOT/'vad-sensitivity.json').write_text(json.dumps(result,indent=2)+'\n')
        print(json.dumps(summary),flush=True)


if __name__=='__main__':
    asyncio.run(main())
