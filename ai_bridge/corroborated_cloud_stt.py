"""Optional local corroboration of cloud STT behind one acoustic controller.

The local decoder cannot release a turn or replace the caller's words. Agreement
can support readiness; disagreement or unavailable evidence requires clarification.
"""
from __future__ import annotations

import asyncio
import math
import re
import time
from collections.abc import AsyncGenerator
from typing import Any

from pipecat.frames.frames import Frame

from .antigravity_live_stt import AntigravityLiveSTTService
from .parakeet_local_stt import STTHypothesis, load_model_async, transcribe_pcm_async

MODEL = 'mlx-community/whisper-large-v3-turbo-q4'

_SMALL_NUMBERS = {
    word: str(index)
    for words in (
        'zero one two three four five six seven eight nine ten eleven twelve thirteen fourteen fifteen sixteen seventeen eighteen nineteen',
        'zéro un deux trois quatre cinq six sept huit neuf dix onze douze treize quatorze quinze seize',
    )
    for index, word in enumerate(words.split())
}
_TENS = {'twenty':20, 'thirty':30, 'forty':40, 'fifty':50, 'sixty':60, 'seventy':70, 'eighty':80, 'ninety':90,
         'vingt':20, 'trente':30, 'quarante':40, 'cinquante':50, 'soixante':60}


def recognition_key(text: str) -> tuple[str, ...]:
    """Normalize explicit formatting/fillers, never fuzzy-match semantic words."""
    text = text.casefold().replace('\u2019', "'")
    text = re.sub(r"\be[ -]?mail\b", "email", text)
    text = re.sub(r"\bwhats[ -]+app\b", "whatsapp", text)
    for contraction, expanded in {"can't":"can not", "cannot":"can not", "won't":"will not", "don't":"do not", "i'd":"i would", "i'm":"i am", "it's":"it is"}.items():
        text = re.sub(r'\b'+re.escape(contraction)+r'\b', expanded, text)
    text = text.replace('$', ' usd ').replace('€', ' eur ')
    tokens = re.findall(r'\d+(?:[.,]\d+)?|[^\W\d_]+', text, flags=re.UNICODE)
    for filler in (('actually',), ('well',), ('en','fait')):
        if tuple(tokens[:len(filler)]) == filler:
            tokens = tokens[len(filler):]
    # Currency names and numeric spellings are formatting, not fuzzy paraphrase.
    output = []
    index = 0
    while index < len(tokens):
        token = tokens[index]
        if token in _TENS:
            number = _TENS[token]
            if index+1 < len(tokens) and _SMALL_NUMBERS.get(tokens[index+1]) in set(map(str, range(1,10))):
                number += int(_SMALL_NUMBERS[tokens[index+1]])
                index += 1
            output.append(str(number))
        else:
            output.append({'dollar':'usd','dollars':'usd','euro':'eur','euros':'eur'}.get(token, _SMALL_NUMBERS.get(token, token)))
        index += 1
    currency = []
    index = 0
    def numeric(token):
        return bool(re.fullmatch(r"\d+(?:[.,]\d+)?", token))
    while index < len(output):
        token = output[index]
        if index+1 < len(output) and token in {'usd','eur'} and numeric(output[index+1]):
            currency.append(token+':'+output[index+1])
            index += 2
        elif index+1 < len(output) and numeric(token) and output[index+1] in {'usd','eur'}:
            currency.append(output[index+1]+':'+token)
            index += 2
        else:
            currency.append(token)
            index += 1
    return tuple(currency)


class CorroboratedCloudSTTService(AntigravityLiveSTTService):
    """Stream cloud recognition while a bounded local check shares its speech epoch."""

    def __init__(self, *, verifier_model: str = MODEL, verification_timeout_secs: float = 1.8, **kwargs: Any):
        super().__init__(**kwargs)
        if not math.isfinite(verification_timeout_secs) or verification_timeout_secs <= 0:
            raise ValueError('Verification timeout must be positive')
        self._verifier_model = verifier_model
        self._decoder_model = verifier_model
        self._verification_timeout = verification_timeout_secs
        self._verification_pause = 0.30
        self._verification_pcm = bytearray()
        self._verification_speech_end = 0
        self._verification_max_bytes = 120*16000*2
        self._verification_overflow = False
        self._verification_task: asyncio.Task[None] | None = None
        self._verification_attempt: tuple[int, float, int] | None = None
        self._verification_result_key: tuple[int, float, int] | None = None
        self._verification_result: STTHypothesis | None = None
        self._verification_ms: float | None = None
        self._decode_for_verification = self._decode_reference

    async def _decode_reference(self, pcm: bytes, model: str, language: str) -> STTHypothesis:
        # A small channel vocabulary, with competing terms, improves proper-noun
        # recognition without supplying any expected consent, price or answer.
        return await transcribe_pcm_async(
            pcm, model, language, initial_prompt="WhatsApp, web, site web, SMS, e-mail.",
        )

    def _verification_key(self) -> tuple[int, float, int]:
        return self._speech_epoch, self._last_speech_at, self._transport_revision

    def _verification_ready(self) -> bool:
        return self._verification_result_key == self._verification_key()

    def _verification_agrees(self) -> bool:
        hypothesis = self._verification_result
        return bool(self._verification_ready() and hypothesis is not None and hypothesis.trusted_for_task
                    and hypothesis.text and recognition_key(hypothesis.text) == recognition_key(self._last_transcript))

    def _partial_is_confirmed(self) -> bool:
        return super()._partial_is_confirmed() or self._verification_agrees()

    async def _start_session(self) -> None:
        try:
            from huggingface_hub import snapshot_download
            async with asyncio.timeout(10.0):
                self._decoder_model = await asyncio.to_thread(snapshot_download, self._verifier_model, local_files_only=True)
                await load_model_async(self._decoder_model)
        except Exception:
            await self.push_error(error_msg="Local speech corroboration requires an installed Apple GPU decoder and cached model", fatal=True)
            raise
        self._verification_pcm.clear()
        self._verification_speech_end = 0
        self._verification_overflow = False
        self._verification_attempt = self._verification_result_key = None
        self._verification_result = None
        await super()._start_session()

    async def run_stt(self, audio: bytes) -> AsyncGenerator[Frame | None, None]:
        if not audio or self._is_closing:
            return
        self._verification_pcm.extend(audio)
        async for frame in super().run_stt(audio):
            yield frame
        if self._recognition_gap or self._recognition_unavailable:
            self._verification_pcm.clear()
            self._verification_speech_end = 0
            return
        if self._acoustic_active:
            self._verification_speech_end = len(self._verification_pcm)
        if not self.caller_owns_floor() and not self._acoustic_active:
            keep = int(self._target_sample_rate * 2 * self.recognition_preroll_secs)
            if len(self._verification_pcm) > keep:
                del self._verification_pcm[:-keep]
            self._verification_speech_end = 0
            self._verification_overflow = False
        if len(self._verification_pcm) > self._verification_max_bytes:
            self._verification_pcm.clear()
            self._verification_speech_end = 0
            self._verification_overflow = True

    def _can_speculate(self) -> bool:
        if not super()._can_speculate() or self._verification_overflow:
            return False
        if self._verification_ready():
            return self._verification_agrees()
        return time.monotonic() < self._last_speech_at + self._verification_pause + self._verification_timeout

    async def _watchdog_tick(self) -> None:
        key = self._verification_key()
        deadline = self._last_speech_at + self._verification_pause + self._verification_timeout
        if (not self._is_closing and not self._recognition_unavailable and not self._recognition_gap
                and self.caller_owns_floor() and self._verification_speech_end and not self._verification_overflow
                and self._silence_elapsed() >= self._verification_pause and time.monotonic() < deadline
                and self._verification_attempt != key
                and (self._verification_task is None or self._verification_task.done())):
            end = min(len(self._verification_pcm), self._verification_speech_end+3840)
            snapshot = bytes(self._verification_pcm[:end])
            language = self._language_signal(self._last_transcript)
            language = language if language in {'en','fr'} else 'auto'
            self._verification_attempt = key
            self._verification_task = asyncio.create_task(self._verify(snapshot, key, language), name='cloud-stt-corroboration')
        await super()._watchdog_tick()

    async def _verify(self, snapshot: bytes, key: tuple[int, float, int], language: str) -> None:
        started = time.perf_counter()
        try:
            # Retain one owned task until native inference actually returns.
            # The turn deadline does not queue replacement work behind a timed-out decode.
            result = await self._decode_for_verification(snapshot, self._decoder_model, language)
        except asyncio.CancelledError:
            raise
        except Exception:
            result = STTHypothesis('', trusted_for_task=False)
        if (self._is_closing or key != self._verification_key() or self._acoustic_active
                or self._last_committed_speech_epoch >= key[0]):
            return
        self._verification_result_key = key
        self._verification_result = result
        self._verification_ms = (time.perf_counter()-started)*1000

    def _recognition_metadata(self) -> dict[str, Any]:
        base = super()._recognition_metadata()
        hypothesis = self._verification_result if self._verification_ready() else None
        reason = (
            'buffer_overflow' if self._verification_overflow
            else 'unavailable' if hypothesis is None
            else 'low_local_confidence' if not hypothesis.trusted_for_task or not hypothesis.text
            else 'agreement' if self._verification_agrees()
            else 'transcript_disagreement'
        )
        return {**base, 'trusted_for_task': base.get('trusted_for_task', True) is not False and reason == 'agreement',
                'corroboration': {'reason':reason, 'model':self._verifier_model,
                                 'local_confidence': hypothesis.confidence if hypothesis else None,
                                 'language': hypothesis.language if hypothesis else None,
                                 'decode_ms': self._verification_ms if hypothesis else None}}

    async def _commit_pending_transcript(self, **kwargs: Any) -> None:
        waiting_for_evidence = not self._verification_ready() or (
            not self._provider_final_seen and not self._verification_agrees()
        )
        if (waiting_for_evidence and not self._verification_overflow
                and time.monotonic() < self._last_speech_at + self._verification_pause + self._verification_timeout):
            return
        committed_before = self._last_committed_speech_epoch
        key = self._verification_key()
        consumed = len(self._verification_pcm)
        await super()._commit_pending_transcript(**kwargs)
        if self._last_committed_speech_epoch != committed_before:
            del self._verification_pcm[:consumed]
            self._verification_speech_end = max(0, self._verification_speech_end-consumed)
            self._verification_overflow = False
            if self._verification_result_key == key:
                self._verification_result_key = None
                self._verification_result = None

    async def _close_session(self) -> None:
        self._is_closing = True
        task, self._verification_task = self._verification_task, None
        if task is not None:
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)
        self._verification_pcm.clear()
        await super()._close_session()
