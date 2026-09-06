"""Matched local confidence-gated decode of noisy matrix clips, no model downloads.

Decoder timing excludes endpoint detection. This is not an installed hybrid path.
"""
from __future__ import annotations

import asyncio
import importlib.util
import json
import os
import time
from dataclasses import asdict
from pathlib import Path

from phone_agent_gateway.ai_bridge.parakeet_local_stt import load_model_async, transcribe_pcm_async

ROOT = Path(__file__).resolve().parent
spec = importlib.util.spec_from_file_location('bilingual_matrix', ROOT/'benchmark_bilingual_noise_sequences.py')
matrix = importlib.util.module_from_spec(spec)
spec.loader.exec_module(matrix)


async def main():
    os.environ['HF_HUB_OFFLINE'] = '1'
    model = 'mlx-community/whisper-large-v3-turbo-q4'
    start = time.perf_counter()
    await load_model_async(model)
    result = {'scope': __doc__, 'model': model, 'load_and_prewarm_ms': (time.perf_counter()-start)*1000, 'rows': []}
    cases = matrix.make_cases()
    for index, case in enumerate(cases):
        if case['case'] not in {'fr_negative', 'en_negative'}:
            continue
        for level in ('clean', '20', '10'):
            source = case['pcm']
            full, _ = matrix.with_noise(source, 8*16000, None if level=='clean' else float(level), 101+index)
            pcm = full[:len(source)+3840]
            for language in ('auto', case['language']):
                start = time.perf_counter()
                hypothesis = await transcribe_pcm_async(pcm, model, language)
                row = {'case': case['case'], 'level': level, 'language_mode': language,
                       'expected': case['expected'], 'source_sha256': case['source_sha256'],
                       'decode_ms': (time.perf_counter()-start)*1000, **asdict(hypothesis)}
                row['words_match'] = matrix.canonical(row['text'])==matrix.canonical(case['expected'])
                result['rows'].append(row)
                (ROOT/'noisy-local-crosscheck.json').write_text(json.dumps(result, ensure_ascii=False, indent=2)+'\n')
                print(json.dumps({k:v for k,v in row.items() if k!='diagnostics'},ensure_ascii=False), flush=True)


if __name__ == '__main__':
    asyncio.run(main())
