"""Read-only replay of raw-response and scoped regression evidence."""
from pathlib import Path
import datetime
import hashlib
import json
import runpy
import sys
import xml.etree.ElementTree as ET

ROOT = Path(__file__).resolve().parents[2]
LOCAL = ROOT / 'reports/local_requirements_completion'
CANDIDATE = LOCAL / 'event-structured-r13-candidate'
sys.path.insert(0, str(CANDIDATE / 'src'))
from sat_sim.task_models import canonicalize_task_spec
from sat_sim.capability_agent import _natural_battery_loss_requirement_issues


def read(path):
    return json.loads(path.read_text(encoding='utf-8'))


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


checks = {}
frozen = LOCAL / 'r11-natural-loss-variations-v2/requests.frozen.json'
checks['frozen_request_bytes'] = sha(frozen) == 'dfe9281ddd8d36819946f811ff1f0c18517d10e67e4d2fec877d4600d37851fd'
state = read(LOCAL / 'r13-typed-natural-loss-variations/state.json')
folder = LOCAL / 'r13-typed-natural-loss-variations/create17'
call = read(folder / 'model_call.json')
raw = json.loads(call['raw_text'])
events = canonicalize_task_spec(raw)['events']
checks['r13_archived_failed'] = state['status'] == 'FAILED_NO_SUBSTITUTION'
checks['r13_actual_model_verified'] = state['cases'][0]['checks']['actual_model_verified'] is True
checks['r13_raw_bytes_bound'] = hashlib.sha256(call['raw_text'].encode()).hexdigest() == state['cases'][0]['backend_identity']['invocation_evidence']['response_sha256']
checks['r13_two_raw_faults'] = len(events['faults']) == 2
battery = [e for e in events['faults'] if e['effect'] == 'eps_battery_capacity_loss']
checks['r13_correct_battery_literal'] = len(battery) == 1 and battery[0]['magnitude'] == .17 and battery[0]['start_s'] == 4.
checks['r13_extra_payload_fault'] = any(e['effect'] == 'payload_instrument_off' for e in events['faults'])
checks['r13_raw_nominal_mode'] = raw['target']['mode'] == 'nominal'
normalized = read(folder / 'task_spec.json')
checks['r13_normalized_events_empty'] = not any(canonicalize_task_spec(normalized)['events'].values())
checks['r13_product_validation_passed'] = read(folder / 'validation.json')['ok'] is True
checks['r13_independent_driver_rejected'] = state['cases'][0]['checks']['raw_loss_fraction'] is False
checks['r13_refinement_not_run'] = len(state['cases']) == 1
request = read(frozen)[0]['text']
original_raw = json.dumps(raw, sort_keys=True)
checks['new_guard_rejects_extra_raw_fault'] = bool(_natural_battery_loss_requirement_issues(raw, request))
checks['new_guard_rejects_cleared_normalized_faults'] = bool(_natural_battery_loss_requirement_issues(normalized, request))
checks['guard_did_not_mutate_raw'] = json.dumps(raw, sort_keys=True) == original_raw
for name, expected in [('r13-pytest-finish-diagnostic', 31), ('r14-natural-guard-unit', 16), ('r14-natural-guard-regression-v2', 93)]:
    suite = ET.parse(LOCAL / name / 'junit.xml').getroot().find('testsuite')
    checks[name + '_counts'] = (int(suite.attrib['tests']) == expected and int(suite.attrib['errors']) == 0 and int(suite.attrib['failures']) == 0 and int(suite.attrib['skipped']) == 0)
    checks[name + '_terminal_exit_zero'] = read(LOCAL / name / 'pytest-returned.json')['exitstatus'] == 0
module = runpy.run_path(str(ROOT / 'scripts/run_native_endurance.py'))
fp = module['runtime_source_fingerprint']
checks['production_application_unchanged'] = fp(module['application_source_manifest']()) == '4cce6d46062a27bc310cc168c1d9587a458c56b4692bd60cf2c60548c2698f82'
checks['candidate_application_bound'] = fp(module['application_source_manifest'](CANDIDATE)) == 'c7937da1d0afc69a1208794816175c51a4084e65acbe28c65232ab57944a8fba'
result = {'status': 'SCOPED_EVIDENCE_PASS_NOT_PRODUCT_ACCEPTANCE' if all(checks.values()) else 'FAIL',
          'observed_at_utc': datetime.datetime.now(datetime.timezone.utc).isoformat(),
          'scope': 'Actual r13 failure and protocol/guard regression evidence only. R14 real generation and execution remain independently adjudicated; this does not promote the candidate.',
          'checks': checks}
out = LOCAL / 'r14-event-candidate-independent-review.json'
out.write_text(json.dumps(result, indent=2), encoding='utf-8')
print(json.dumps({'status': result['status'], 'checks': len(checks), 'failed': [k for k,v in checks.items() if not v]}))
raise SystemExit(0 if all(checks.values()) else 1)
