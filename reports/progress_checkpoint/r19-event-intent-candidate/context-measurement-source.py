"""Tokenizer-only context comparison on one archived canonical task."""
from pathlib import Path
import copy
import hashlib
import json
import psutil
import sys

ROOT = Path(__file__).resolve().parents[2]
SOURCE = ROOT / 'reports/local_requirements_completion/event-intent-r16-candidate'
sys.path.insert(0, str(SOURCE / 'src'))
from sat_sim.unified_agent import _refinement_prompt_projection
from sat_sim.capability_agent import build_capability_json_prompt
from sat_sim.llm_backends import OPENAI_TASKSPEC_SYSTEM_PROMPT
from transformers import AutoTokenizer

p = psutil.Process()
p.nice(psutil.BELOW_NORMAL_PRIORITY_CLASS)
p.cpu_affinity([x for x in p.cpu_affinity() if x >= 12])
manifest = json.loads((ROOT / '.models/Qwen--Qwen2.5-Coder-1.5B-Instruct-provenance.json').read_text(encoding='utf-8'))
tokenizer = AutoTokenizer.from_pretrained(manifest['snapshot'], local_files_only=True, trust_remote_code=False)
base_path = ROOT / 'reports/local_requirements_completion/r13-typed-natural-loss-variations/create17/task_spec.json'
base = json.loads(base_path.read_text(encoding='utf-8'))
latest = json.loads((ROOT / 'reports/local_requirements_completion/r11-natural-loss-variations-v2/requests.frozen.json').read_text(encoding='utf-8'))[1]['text']
old = copy.deepcopy(base)
old.pop('metadata', None)
new = _refinement_prompt_projection(base)
assert {k:v for k,v in old.items() if k != 'provenance'} == new
counts = {}
for name, projected in [('before', old), ('after', new)]:
    text = ('这是一次基于现有 TaskSpec 的多轮补充。请输出完整、更新后的 TaskSpec；'
            '保留用户未要求修改的能力、参数、事件和输出，不要凭空增加故障或退化。\n'
            f'现有 TaskSpec：\n{json.dumps(projected, ensure_ascii=False)}\n'
            f'本轮补充指令：\n{latest}')
    prompt = build_capability_json_prompt(text)
    messages = [{'role':'system','content':OPENAI_TASKSPEC_SYSTEM_PROMPT}, {'role':'user','content':prompt}]
    tokens = tokenizer.apply_chat_template(messages, tokenize=True, add_generation_prompt=True, enable_thinking=False)
    counts[name] = {'base_chars':len(json.dumps(projected,ensure_ascii=False)), 'prompt_tokens':len(tokens),
                    'requested_output_tokens':4096, 'context_limit':16384,
                    'fits_full_requested_budget':len(tokens)+4096<=16384,
                    'prompt_sha256':hashlib.sha256(prompt.encode()).hexdigest()}
result = {'scope':'Tokenizer-only comparison on the same archived r13 canonical task. Not a model generation, current r16 execution or accuracy result.',
          'base_sha256':hashlib.sha256(base_path.read_bytes()).hexdigest(), 'physics_fields_identical':True, 'counts':counts}
(ROOT / 'reports/local_requirements_completion/r16-refinement-context-measurement.json').write_text(json.dumps(result,indent=2),encoding='utf-8')
print(json.dumps(result))
