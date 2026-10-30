"""Read-only request-form coverage, not an accuracy or causal attribution score."""
from pathlib import Path
import hashlib
import json
import re
import sys
import psutil
import yaml

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / 'src'))
from sat_sim.task_models import canonicalize_task_spec

p = psutil.Process()
p.nice(psutil.BELOW_NORMAL_PRIORITY_CLASS)
p.cpu_affinity([x for x in p.cpu_affinity() if x >= 12])
DATA = ROOT / 'data/training/taskspec_lora_v5_current_prompt_event_aligned'
report = {'scope':'Request-form coverage of the actual v5 train/validation labels; no model inference, '
                   'dataset edits, accuracy relabeling or proof that any one coverage gap caused a failure.',
          'splits': {}}
for split in ['train', 'validation']:
    path = DATA / (split + '.jsonl')
    raw = path.read_bytes()
    counts = {'records':0, 'eventful_labels':0, 'eventful_with_explicit_json_marker':0,
              'eventful_without_explicit_json_marker':0, 'requests_with_percent_literals':0}
    uncovered = []
    for line in raw.decode('utf-8').splitlines():
        row = json.loads(line)
        request = row['request']
        answer = next(m['content'] for m in reversed(row['messages']) if m['role'] == 'assistant')
        spec = canonicalize_task_spec(yaml.safe_load(answer))
        eventful = any(spec['events'].values())
        declared = bool(re.search(r'\bEvents\s*[:：]|事件配置\s*[:：]|事件为', request, re.I))
        counts['records'] += 1
        counts['requests_with_percent_literals'] += bool(re.search(r'\d+(?:\.\d+)?\s*[%％]', request))
        if eventful:
            counts['eventful_labels'] += 1
            counts['eventful_with_explicit_json_marker'] += declared
            counts['eventful_without_explicit_json_marker'] += not declared
            if not declared:
                uncovered.append({'id':row['id'], 'family':row['family']})
    assert hashlib.sha256(path.read_bytes()).hexdigest() == hashlib.sha256(raw).hexdigest()
    report['splits'][split] = {'sha256':hashlib.sha256(raw).hexdigest(), 'counts':counts,
                               'eventful_without_marker_records':uncovered}
out = ROOT / 'reports/local_requirements_completion/r16-training-request-coverage.json'
out.write_text(json.dumps(report,indent=2),encoding='utf-8')
print(json.dumps({k:v['counts'] for k,v in report['splits'].items()}))
