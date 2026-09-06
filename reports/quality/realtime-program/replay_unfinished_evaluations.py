"""Paced synthetic EN/FR evaluation continuations through the selected STT bridge.

No calls, speakers, microphone or action tools. Print evidence to stdout.
"""

from __future__ import annotations

import asyncio
import importlib.util
import json
import tempfile
import time
from pathlib import Path

from pipecat.frames.frames import TranscriptionFrame

from phone_agent_gateway.ai_bridge.antigravity_live_stt import AntigravityLiveSTTService

ROOT = Path(__file__).resolve().parent


async def main():
    spec = importlib.util.spec_from_file_location('fixture_helper', ROOT / 'benchmark_components.py')
    helper = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(helper)
    cases = [
        ('en_negative', 'en-US', 'Samantha', 'Yes, yes, it seems very', 'expensive for me.', 900),
        ('fr_negative', 'fr-FR', 'Thomas', 'Oui, ça semble très', 'cher pour moi.', 1200),
        ('en_positive', 'en-US', 'Samantha', 'That sounds extremely', 'useful for our team.', 1500),
        ('fr_positive', 'fr-FR', 'Thomas', 'Cela paraît particulièrement', 'utile pour notre équipe.', 1800),
    ]
    with tempfile.TemporaryDirectory(prefix='phoneagent-evaluation-audio-') as directory:
        for name, lang, voice, first, second, pause in cases:
            first_pcm = helper.synthesize((name + '_first', lang, voice, first), Path(directory))
            last_pcm = helper.synthesize((name + '_last', lang, voice, second), Path(directory))
            pcm = first_pcm + bytes(pause * 32) + last_pcm
            service = AntigravityLiveSTTService(language=lang, smart_turn_enabled=True,
                                               speculative_pipeline_enabled=True)
            commits = []
            hypotheses = []
            started = time.monotonic()
            clock = {'started': started}
            stage = service._stage_transcription

            def capture_hypothesis(text, *, is_final, _hypotheses=hypotheses, _clock=clock, _stage=stage):
                _hypotheses.append({'at_ms': (time.monotonic() - _clock['started']) * 1000,
                                   'text': text, 'final': is_final})
                return _stage(text, is_final=is_final)

            async def capture(frame, *_, _commits=commits, _clock=clock):
                if isinstance(frame, TranscriptionFrame):
                    _commits.append({'at_ms': (time.monotonic() - _clock['started']) * 1000,
                                    'text': frame.text, 'result': frame.result})

            async def interrupt():
                pass

            service._stage_transcription = capture_hypothesis
            service.push_frame = capture
            service.broadcast_interruption = interrupt
            try:
                await asyncio.to_thread(service._discover_bridge)
                await service._start_session()
                started = time.monotonic()
                clock['started'] = started
                for offset in range(0, len(pcm) + 8 * 32000, 640):
                    chunk = pcm[offset:offset + 640].ljust(640, b'\0') if offset < len(pcm) else bytes(640)
                    async for _ in service.run_stt(chunk):
                        pass
                    await asyncio.sleep(max(0, started + (offset + 640) / 32000 - time.monotonic()))
                    if offset >= len(pcm) and commits and (time.monotonic() - started) * 1000 > commits[-1]['at_ms'] + 650:
                        break
                continuation_ms = len(first_pcm) / 32 + pause
                print(json.dumps({
                    'case': name, 'expected': first + ' ' + second, 'pause_ms': pause,
                    'continuation_start_ms': continuation_ms, 'eof_ms': len(pcm) / 32,
                    'premature_commit': any(c['at_ms'] < continuation_ms for c in commits),
                    'commits': commits, 'hypotheses': hypotheses,
                }, ensure_ascii=False), flush=True)
            finally:
                await service._close_session()
                service._vad._executor.shutdown(wait=False)
                if service._smart_turn is not None:
                    service._smart_turn._executor.shutdown(wait=False)


if __name__ == '__main__':
    asyncio.run(main())
