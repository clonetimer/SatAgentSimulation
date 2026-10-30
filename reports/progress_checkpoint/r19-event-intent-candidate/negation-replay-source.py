"""Read-only product sanitizer replay; archived answers are never executed."""
from pathlib import Path
import argparse
import copy
import hashlib
import json
import sys

ROOT = Path(__file__).resolve().parents[2]
parser = argparse.ArgumentParser()
parser.add_argument('--source', type=Path, required=True)
parser.add_argument('--output', type=Path, required=True)
args = parser.parse_args()
source = args.source.resolve()
output = args.output.resolve()
assert source.is_relative_to(ROOT) and output.is_relative_to(ROOT / 'reports/local_requirements_completion')
sys.path.insert(0, str(source / 'src'))
from sat_sim.agent_facade import _effect_intent_state, _sanitize_generated_draft
from sat_sim.task_models import canonicalize_task_spec

base = ROOT / 'reports/local_requirements_completion'
requests = base / 'r11-natural-loss-variations-v2/requests.frozen.json'
request = json.loads(requests.read_text(encoding='utf-8'))[0]['text']
call_path = base / 'r13-typed-natural-loss-variations/create17/model_call.json'
raw = json.loads(json.loads(call_path.read_text(encoding='utf-8'))['raw_text'])
cases = []
for name, mode in [('actual_archived_answer', None), ('protocol_fixture_same_events_fault_mode', 'fault')]:
    payload = copy.deepcopy(raw)
    if mode is not None:
        payload['target']['mode'] = mode
    before = copy.deepcopy(payload)
    sanitized, repairs = _sanitize_generated_draft(payload, request)
    cases.append({'case': name, 'input_mode': before['target']['mode'],
                  'before_fault_count': len(canonicalize_task_spec(before)['events']['faults']),
                  'after_fault_count': len(canonicalize_task_spec(sanitized)['events']['faults']),
                  'input_unmodified': before == payload, 'repairs': repairs})
result = {'scope': 'Sanitizer causality replay only; no generated script or model performance claim.',
          'source': str(source), 'source_facade_sha256': hashlib.sha256((source / 'src/sat_sim/agent_facade.py').read_bytes()).hexdigest(),
          'request_file_sha256': hashlib.sha256(requests.read_bytes()).hexdigest(),
          'raw_call_file_sha256': hashlib.sha256(call_path.read_bytes()).hexdigest(),
          'intent': _effect_intent_state(request), 'cases': cases}
output.write_text(json.dumps(result, indent=2), encoding='utf-8')
print(json.dumps({'intent': result['intent'], 'counts': [(c['case'], c['before_fault_count'], c['after_fault_count']) for c in cases]}))
