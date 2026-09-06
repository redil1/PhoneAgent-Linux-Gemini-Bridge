"""Paced continuous EN/FR endpoint matrix, real cloud ASR; no call or tool effects.

Noise is deterministic stationary 300-3400 Hz noise, not recorded café/echo audio.
Source EOF is used for measurement only and never passed to the turn controller.
"""
from __future__ import annotations

import argparse
import asyncio
import hashlib
import importlib.util
import json
import re
import tempfile
import time
from pathlib import Path

import numpy as np
from pipecat.frames.frames import ErrorFrame, TranscriptionFrame, UserStartedSpeakingFrame

from phone_agent_gateway.ai_bridge.antigravity_live_stt import AntigravityLiveSTTService
from phone_agent_gateway.ai_bridge.production_pipeline import _default_stt_context

ROOT = Path(__file__).resolve().parent
spec = importlib.util.spec_from_file_location('matrix_fixtures', ROOT / 'benchmark_components.py')
helper = importlib.util.module_from_spec(spec)
spec.loader.exec_module(helper)


def canonical(text):
    text = re.sub(r'\b6\b', 'six', text.casefold()).replace('\u2019', "'")
    return re.findall(r"\w+", text, re.UNICODE)


def make_cases(pause_scale=1.0, include_channel_holdouts=False):
    cases = []
    with tempfile.TemporaryDirectory(prefix='phoneagent-continuous-matrix-') as temporary:
        directory = Path(temporary)
        examples = [
            ('en_preference', 'en-US', 'Samantha', 'I prefer', 900, 'movies and series.'),
            ('fr_preference', 'fr-FR', 'Thomas', 'Je préfère', 1200, 'les films et les séries.'),
            ('en_correction', 'en-US', 'Samantha', 'Actually,', 800, 'I want the six month plan.'),
            ('fr_correction', 'fr-FR', 'Thomas', 'En fait,', 900, 'je voudrais le forfait de six mois.'),
            ('en_request', 'en-US', 'Samantha', 'Could you', 650, 'send the details on WhatsApp?'),
            ('fr_request', 'fr-FR', 'Thomas', 'Est-ce que vous pouvez', 900, 'envoyer les détails sur WhatsApp ?'),
            ('en_negative', 'en-US', 'Samantha', 'No, not the yearly plan.', 0, ''),
            ('fr_negative', 'fr-FR', 'Thomas', "Non, pas l'abonnement annuel.", 0, ''),
            ('en_yes_1', 'en-US', 'Samantha', 'Yes, please.', 0, ''),
            ('en_yes_2', 'en-US', 'Samantha', 'Yes, please.', 0, ''),
            ('fr_yes_1', 'fr-FR', 'Thomas', "Oui, s'il vous plaît.", 0, ''),
            ('fr_yes_2', 'fr-FR', 'Thomas', "Oui, s'il vous plaît.", 0, ''),
        ]
        if include_channel_holdouts:
            examples = [
                ('en_web', 'en-US', 'Samantha', 'Send the details through the website.', 0, ''),
                ('fr_web', 'fr-FR', 'Thomas', 'Pouvez-vous envoyer les détails sur le site web ?', 0, ''),
                ('en_email', 'en-US', 'Samantha', 'Not WhatsApp, please send me an email.', 0, ''),
                ('fr_email', 'fr-FR', 'Thomas', 'Pas sur WhatsApp, envoyez-moi un e-mail.', 0, ''),
                ('en_whatsapp', 'en-US', 'Samantha', 'Please send the details on WhatsApp.', 0, ''),
                ('fr_whatsapp', 'fr-FR', 'Thomas', 'Envoyez les détails sur WhatsApp, merci.', 0, ''),
            ]
        for key, language, voice, first, pause, second in examples:
            def synth(text, voice=voice, language=language):
                ident = 'bilingual-matrix-' + hashlib.sha256((voice+text).encode()).hexdigest()[:16]
                return helper.synthesize((ident, language, voice, text), directory)
            pause = round(pause * pause_scale)
            prefix = synth(first)
            pcm = prefix + (bytes(pause*32) + synth(second) if second else b'')
            pcm += bytes((-len(pcm)) % 640)
            cases.append({'case': key, 'language': language, 'expected': first+' '+second if second else first,
                          'pcm': pcm, 'noise_seed': 101+len(cases), 'continuation_ms': len(prefix)/32+pause if second else None,
                          'pause_ms': pause, 'source_sha256': hashlib.sha256(pcm).hexdigest()})
    return cases


def with_noise(pcm, tail_samples, snr, seed):
    samples = np.frombuffer(pcm, dtype=np.int16).astype(np.float64)
    padded = np.pad(samples, (0, tail_samples))
    if snr is None:
        return padded.astype(np.int16).tobytes(), 0
    energy = np.sqrt(np.mean(samples.reshape(-1, 320)**2, axis=1))
    active = np.repeat(energy > max(30, energy.max()*0.10), 320)
    speech_rms = float(np.sqrt(np.mean(samples[active]**2)))
    rng = np.random.default_rng(seed)
    raw = rng.normal(size=len(padded))
    spectrum = np.fft.rfft(raw)
    frequencies = np.fft.rfftfreq(len(raw), 1/16000)
    spectrum[(frequencies<300) | (frequencies>3400)] = 0
    noise = np.fft.irfft(spectrum, n=len(raw))
    noise *= speech_rms / (10**(snr/20)) / np.sqrt(np.mean(noise**2))
    mixed = padded + noise
    clipped = int(np.sum(np.abs(mixed)>32767))
    return np.clip(mixed, -32768, 32767).astype(np.int16).tobytes(), clipped


async def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', default='bilingual-noise-sequences.json')
    parser.add_argument('--levels', nargs='+', default=['clean', '20', '10'])
    parser.add_argument('--pause-scale', type=float, default=1.0)
    parser.add_argument('--cases', nargs='*')
    parser.add_argument('--corroborate', action='store_true')
    parser.add_argument('--channel-holdouts', action='store_true')
    args = parser.parse_args()
    output = ROOT / args.output
    cases = make_cases(args.pause_scale, args.channel_holdouts)
    if args.cases:
        cases = [case for case in cases if case['case'] in args.cases]
    if not cases:
        parser.error('No matching cases selected')
    service_class = AntigravityLiveSTTService
    if args.corroborate:
        from phone_agent_gateway.ai_bridge.corroborated_cloud_stt import CorroboratedCloudSTTService
        service_class = CorroboratedCloudSTTService
    service = service_class(language='en-US', context_bias=_default_stt_context('en-US'),
                                       smart_turn_enabled=True, speculative_pipeline_enabled=True)
    origin = time.perf_counter()
    def now():
        return (time.perf_counter()-origin)*1000
    commits, hypotheses, acoustic, predictions, errors, verifications = [], [], [], [], [], []
    if args.corroborate:
        decode = service._decode_for_verification
        async def observe_decode(pcm, model, language):
            result = await decode(pcm, model, language)
            verifications.append({'at_ms': now(), 'text': result.text, 'trusted': result.trusted_for_task,
                                  'language_mode': language, 'audio_ms': len(pcm)/32,
                                  'audio_sha256': hashlib.sha256(pcm).hexdigest()})
            return result
        service._decode_for_verification = observe_decode
    stage = service._handle_provider_transcription
    async def observe(text, *, is_final):
        accepted = await stage(text, is_final=is_final)
        hypotheses.append({'at_ms': now(), 'text': text, 'final': is_final, 'accepted': accepted,
                           'speech_epoch': service._speech_epoch})
        return accepted
    predict = service._run_smart_turn_inference
    def prediction(pcm):
        started = now()
        result = predict(pcm)
        predictions.append({'at_ms': now(), 'elapsed_ms': now()-started, **result})
        return result
    async def capture(frame, *_):
        if isinstance(frame, TranscriptionFrame):
            commits.append({'at_ms': now(), 'text': frame.text, 'metadata': frame.result})
        elif isinstance(frame, UserStartedSpeakingFrame):
            acoustic.append({'at_ms': now(), 'speech_epoch': service._speech_epoch})
        elif isinstance(frame, ErrorFrame):
            errors.append({'at_ms': now(), 'error': frame.error, 'fatal': frame.fatal})
    async def interrupt():
        pass
    service._handle_provider_transcription = observe
    service._run_smart_turn_inference = prediction
    service.push_frame = capture
    service.broadcast_interruption = interrupt
    result = {'scope': __doc__, 'initial_language': 'en-US', 'local_corroboration': args.corroborate, 'rows': [], 'errors': errors}
    try:
        await asyncio.to_thread(service._discover_bridge)
        await service._start_session()
        for level in args.levels:
            for case in cases:
                pcm = case['pcm']
                mixed, clipped = with_noise(pcm, 8*16000, None if level=='clean' else float(level), case['noise_seed'])
                row = {k:v for k,v in case.items() if k!='pcm'}
                start = now()
                row.update(level=level, clipped_samples=clipped, source_duration_ms=len(pcm)/32, start_ms=start)
                ci, hi, ai, pi, vi = len(commits), len(hypotheses), len(acoustic), len(predictions), len(verifications)
                queue_peak = 0
                for offset in range(0, len(mixed), 640):
                    async for _ in service.run_stt(mixed[offset:offset+640]):
                        pass
                    queue_peak = max(queue_peak, service._send_queue.qsize())
                    await asyncio.sleep(max(0, (start+(offset+640)/32-now())/1000))
                    if (offset >= len(pcm) and len(commits)>ci and not service.caller_owns_floor()
                            and now()-commits[-1]['at_ms'] >= 750):
                        break
                actual = ' '.join(c['text'] for c in commits[ci:])
                row.update(verifications=verifications[vi:], commits=commits[ci:], hypotheses=hypotheses[hi:], acoustic=acoustic[ai:], predictions=predictions[pi:],
                           actual=actual, words_match=canonical(actual)==canonical(case['expected']), commit_count=len(commits)-ci,
                           eof_to_commit_ms=[c['at_ms']-start-len(pcm)/32 for c in commits[ci:]],
                           before_continuation=bool(case['continuation_ms'] and any(c['at_ms']-start<case['continuation_ms'] for c in commits[ci:])),
                           queue_peak=queue_peak, unresolved_floor=service.caller_owns_floor())
                result['rows'].append(row)
                output.write_text(json.dumps(result, ensure_ascii=False, indent=2)+'\n')
                print(json.dumps({k:v for k,v in row.items() if k not in ('hypotheses','predictions','commits','acoustic','verifications')},ensure_ascii=False), flush=True)
    finally:
        await service._close_session()
        service._vad._executor.shutdown(wait=False)
        if service._smart_turn is not None:
            service._smart_turn._executor.shutdown(wait=False)
        result['elapsed_seconds'] = now()/1000
        output.write_text(json.dumps(result,ensure_ascii=False,indent=2)+'\n')


if __name__ == '__main__':
    asyncio.run(main())
