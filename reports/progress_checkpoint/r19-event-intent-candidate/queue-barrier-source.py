"""One-token serialization barrier, not a semantic/model-quality evaluation."""
from pathlib import Path
import datetime
import json
import time
import urllib.request

OUT = Path(__file__).resolve().parent / 'r16-model-queue-barrier.json'
request = {'model': 'Qwen2.5-Coder-1.5B-SatAgent-v5-guided-graph-v8',
           'messages': [{'role': 'user', 'content': 'Reply with one token.'}],
           'max_tokens': 1, 'temperature': 0, 'stream': False}
record = {'scope': 'One-token inference barrier: LocalModelEngine serializes generate with its lock. '
                   'Health alone does not prove an empty generation queue. This probe is not accuracy or timing qualification.',
          'status': 'RUNNING', 'started_at_utc': datetime.datetime.now(datetime.timezone.utc).isoformat(),
          'request': request}
OUT.write_text(json.dumps(record, indent=2), encoding='utf-8')
start = time.perf_counter()
try:
    req = urllib.request.Request('http://127.0.0.1:8031/v1/chat/completions',
        data=json.dumps(request).encode(), headers={'Content-Type': 'application/json'})
    with urllib.request.urlopen(req, timeout=600) as response:
        result = json.load(response)
    assert result['usage']['completion_tokens'] == 1
    record.update(status='BARRIER_RETURNED', response=result)
except Exception as exc:
    record.update(status='FAILED', error={'type': type(exc).__name__, 'message': str(exc)})
finally:
    record['elapsed_s'] = time.perf_counter() - start
    record['finished_at_utc'] = datetime.datetime.now(datetime.timezone.utc).isoformat()
    OUT.write_text(json.dumps(record, indent=2), encoding='utf-8')
print(json.dumps({'status': record['status'], 'elapsed_s': record['elapsed_s']}))
raise SystemExit(0 if record['status'] == 'BARRIER_RETURNED' else 1)
