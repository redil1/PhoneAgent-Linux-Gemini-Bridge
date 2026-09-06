"""Continuous EN/FR synthetic ASR/endpoint replay; no phone, LLM or external action.

Real cloud ASR, Silero and Smart Turn process paced telephone-bandlimited audio.
Known source EOF is used only for measurement, never passed to the endpoint.
"""
from __future__ import annotations

import asyncio
import argparse
import importlib.util
import json
import tempfile
import time
from pathlib import Path

from pipecat.frames.frames import TranscriptionFrame, StartFrame
from phone_agent_gateway.ai_bridge.antigravity_live_stt import AntigravityLiveSTTService

ROOT = Path(__file__).resolve().parent
spec = importlib.util.spec_from_file_location('fixture_helper', ROOT/'benchmark_components.py')
helper = importlib.util.module_from_spec(spec)
spec.loader.exec_module(helper)


async def main():
    parser=argparse.ArgumentParser()
    parser.add_argument('--provider',choices=['antigravity_live','whisper_mlx'],default='antigravity_live')
    args=parser.parse_args()
    output=ROOT/('turn-sequences-mlx.json' if args.provider=='whisper_mlx' else 'turn-sequences.json')
    cases = []
    with tempfile.TemporaryDirectory(prefix='phoneagent-turn-audio-') as tmp:
        def phrase(key, language, voice, text):
            return helper.synthesize((key, language, voice, text), Path(tmp))
        for key, lang, voice, text in [
            ('en_short','en-US','Samantha','Yes, please.'),
            ('en_auxiliary','en-US','Samantha','Yes, I can.'),
            ('fr_negative','fr-FR','Thomas','Je ne sais pas.'),
            ('en_stranded_question','en-US','Samantha','Where are you calling from?'),
        ]:
            cases.append({'case':key,'expected':text,'pcm':phrase(key,lang,voice,text)})
        for key, lang, voice, first, second, pause in [
            ('en_preference_pause','en-US','Samantha','I prefer','movies and series.',900),
            ('fr_preference_pause','fr-FR','Thomas','Je préfère','les films et les séries.',1200),
        ]:
            a=phrase(key+'_first',lang,voice,first)
            b=phrase(key+'_second',lang,voice,second)
            cases.append({'case':key,'expected':first+' '+second,'pcm':a+bytes(pause*32)+b,'pause_ms':pause,'continuation_at_ms':len(a)/32+pause})
        for n in range(2):
            cases.append({'case':f'fr_repeated_yes_{n}','expected':"Oui, s'il vous plaît.",'pcm':phrase('fr_short','fr-FR','Thomas',"Oui, s'il vous plaît.")})
    if args.provider=='whisper_mlx':
        from phone_agent_gateway.ai_bridge.local_neural_stt import LocalNeuralSTTService
        service=LocalNeuralSTTService(model='mlx-community/whisper-large-v3-turbo-q4',decode_language='auto',language='en-US',smart_turn_enabled=True,speculative_pipeline_enabled=True)
    else:
        service=AntigravityLiveSTTService(language='en-US',smart_turn_enabled=True,speculative_pipeline_enabled=True)
    origin=time.perf_counter()
    records=[]
    commits=[]
    predictions=[]
    hypotheses=[]
    decoder_events=[]
    if hasattr(service, "set_latency_sink"):
        service.set_latency_sink(lambda event: decoder_events.append({"at_ms": (time.perf_counter()-origin)*1000, **event}))
    def at():return (time.perf_counter()-origin)*1000
    original_stage=service._stage_transcription
    def stage(text, *, is_final):
        hypotheses.append({'at_ms':at(),'text':text,'final':is_final})
        return original_stage(text,is_final=is_final)
    original_predict=service._run_smart_turn_inference
    def predict(pcm):
        start=at();result=original_predict(pcm)
        predictions.append({'at_ms':at(),'elapsed_ms':at()-start,**result})
        return result
    async def capture(frame,*_):
        if isinstance(frame,TranscriptionFrame):
            commits.append({'at_ms':at(),'text':frame.text,'metadata':frame.result})
    async def interrupt():pass
    service._stage_transcription=stage
    service._run_smart_turn_inference=predict
    service.push_frame=capture
    service.broadcast_interruption=interrupt
    result={'scope':'Synthetic continuous ASR/endpointing only; real selected ASR/Silero/Smart Turn; no calls/messages/microphone/speaker','provider':args.provider,'rows':records}
    try:
        if args.provider=='whisper_mlx':
            await service.start(StartFrame())
        else:
            await asyncio.to_thread(service._discover_bridge)
            await service._start_session()
        for case in cases:
            pcm=case['pcm'];start=at();eof=start+len(pcm)/32;prior=len(commits);hi=len(hypotheses);pi=len(predictions);di=len(decoder_events)
            row={k:v for k,v in case.items() if k!='pcm'}
            row.update({'audio_ms':len(pcm)/32,'start_ms':start,'eof_ms':eof})
            for offset in range(0,len(pcm)+32000*8,640):
                chunk=pcm[offset:offset+640] if offset<len(pcm) else bytes(640)
                async for _ in service.run_stt(chunk):pass
                await asyncio.sleep(max(0,(start+(offset+640)/32-at())/1000))
                if offset>=len(pcm) and len(commits)>prior and at()-commits[-1]['at_ms']>=600:
                    break
            observed=commits[prior:]
            row['commits']=[{**c,'after_eof_ms':c['at_ms']-eof} for c in observed]
            row['hypotheses']=hypotheses[hi:]
            row['predictions']=predictions[pi:]
            row['decoder_events']=decoder_events[di:]
            row['premature_commit']=any(c['at_ms']<eof-100 for c in observed)
            row['final_commit_after_eof_ms']=observed[-1]['at_ms']-eof if observed else None
            records.append(row)
            output.write_text(json.dumps(result,ensure_ascii=False,indent=2)+'\n')
            print(json.dumps({k:v for k,v in row.items() if k not in ('hypotheses','predictions','commits','decoder_events')},ensure_ascii=False),flush=True)
    except Exception as exc:
        result['error']=type(exc).__name__
        raise
    finally:
        await service._close_session()
        service._vad._executor.shutdown(wait=False)
        if service._smart_turn is not None:service._smart_turn._executor.shutdown(wait=False)
        output.write_text(json.dumps(result,ensure_ascii=False,indent=2)+'\n')

if __name__=='__main__':asyncio.run(main())
