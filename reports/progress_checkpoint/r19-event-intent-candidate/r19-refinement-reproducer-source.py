"""Replay the real R19 merger inputs and record unmodified time decisions."""
from pathlib import Path
import json
import re
import sys

ROOT = Path(__file__).resolve().parents[2]
BASE = ROOT/'reports/local_requirements_completion'
SOURCE = BASE/'event-intent-r19-candidate'
sys.path.insert(0,str(SOURCE/'src'))
import sat_sim.unified_agent as module

base=json.loads((BASE/'r19-real-end-to-end-variations/create17/task_spec.json').read_text(encoding='utf-8'))
candidate=json.loads((BASE/'r19-real-end-to-end-variations/refine42/task_spec.json').read_text(encoding='utf-8'))
request=json.loads((BASE/'r11-natural-loss-variations-v2/requests.frozen.json').read_text(encoding='utf-8'))[1]['text']
original=module._time_value_seconds
decisions=[]
def observed(text,patterns):
    value=original(text,patterns)
    matches=[]
    for pattern in patterns:
        match=re.search(pattern,text,re.I)
        if match:
            matches.append({'matched':match.group(0),'groups':match.groups(),
                            'following':text[match.end():match.end()+24]})
    decisions.append({'value':value,'matches':matches})
    return value
module._time_value_seconds=observed
result={'scope':'Replays actual archived base/candidate and user instruction in a diagnostic process. '
                'Time-helper return values are recorded without changing them. No inference or execution.',
        'base_simulation':base['simulation'],'candidate_simulation':candidate['simulation'],
        'time_decisions':decisions}
try:
    merged,changed=module._merge_natural_language_refinement(base,candidate,request)
    result.update(status='UNEXPECTED_SUCCESS',merged_simulation=merged['simulation'],changed=changed)
except ValueError as exc:
    result.update(status='REPRODUCED_REFINEMENT_UNIT_FAILURE',error={'type':type(exc).__name__,'message':str(exc)})
(BASE/'r19-refinement-unit-failure.json').write_text(json.dumps(result,ensure_ascii=False,indent=2),encoding='utf-8')
print(json.dumps({'status':result['status'],'time_values':[d['value'] for d in decisions]},ensure_ascii=True))
