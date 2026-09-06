"""Compare EOF handling against the preserved pre-change source, without network I/O."""
import asyncio
import importlib.util
import json
import time
from pathlib import Path
from unittest.mock import AsyncMock

from pipecat.frames.frames import TranscriptionFrame

from phone_agent_gateway.ai_bridge.antigravity_live_stt import AntigravityLiveSTTService


class Vad:
    def set_sample_rate(self, _):
        pass


class EOFStream:
    eof = True
    def read_envelope(self, _):
        return None, None
    def close(self):
        pass


async def probe(service_type):
    stt = service_type(smart_turn_enabled=False, vad_analyzer=Vad())
    frames = []
    stt.push_frame = AsyncMock(side_effect=lambda f, *_: frames.append(f))
    stt.broadcast_interruption = AsyncMock()
    stt._stream = EOFStream()
    stt._speech_epoch = 1
    stt._speaking = stt._floor_claimed = True
    stt._speech_candidate_at = stt._last_speech_at = time.monotonic() - 5
    stt._last_transcript_update_at = time.monotonic() - 4
    stt._last_transcript = 'Send the'
    await stt._stream_reader_loop()
    await stt._watchdog_tick()
    await stt._close_session()
    return {'committed_after_transport_loss': [f.text for f in frames if isinstance(f, TranscriptionFrame)]}


async def main():
    spec = importlib.util.spec_from_file_location(
        'phone_agent_gateway.ai_bridge.asr_before_recovery',
        '/private/tmp/phoneagent-asr-recovery-before/ai_bridge/antigravity_live_stt.py',
    )
    baseline = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(baseline)
    result = {'scope': 'Controlled EOF with a pending partial; no external effects',
              'before': await probe(baseline.AntigravityLiveSTTService),
              'after': await probe(AntigravityLiveSTTService)}
    Path(__file__).with_name('asr-failure-commit-probe.json').write_text(json.dumps(result, indent=2)+'\n')
    print(json.dumps(result, indent=2))


if __name__ == '__main__':
    asyncio.run(main())
