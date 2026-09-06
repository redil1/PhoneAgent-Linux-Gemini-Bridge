"""Restore Studio after the verified old process exited beyond refresh grace.

Uses existing saved settings and the current desktop shell environment; no secrets
are printed or written. Refuses to start if the old process or port owner remains.
"""
from __future__ import annotations

import importlib.util
import json
import os
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parent
spec = importlib.util.spec_from_file_location('refresh_helper', ROOT/'refresh_partial_confirmation_runtime.py')
helper = importlib.util.module_from_spec(spec)
spec.loader.exec_module(helper)
if helper.alive(968):
    raise RuntimeError('Old Studio process still exists; restoration deferred')
owner = subprocess.run(['lsof','-nP','-iTCP:8090','-sTCP:LISTEN','-t'], capture_output=True, text=True)
if owner.stdout.strip():
    raise RuntimeError('Port 8090 already has an owner; restoration refused')
previous = json.loads((ROOT/'runtime-tts-recovery-refresh-result.json').read_text())
process = helper.launch(dict(os.environ))
result = {'previous_pid':968, 'activated':False,
          'refresh_issue':'Old Studio exceeded45s grace, then exited; first helper did not launch replacement',
          'environment_source':'Current desktop shell plus existing saved Studio settings; prior process environment unavailable'}
result['runtime'] = helper.health(process, previous['runtime']['configuration'], updated=True)
result['activated'] = True
(ROOT/'runtime-partial-confirmation-refresh-result.json').write_text(json.dumps(result,indent=2)+'\n')
print(json.dumps(result,indent=2))
