"""用途：只读核对注册表生成对话数据集的已知提示格式与显式默认绘图约定。
参数：--dataset-dir 指定数据集目录；--output 指定尚不存在的 JSON 报告路径。
输出：带输入哈希、分割与逐记录检查结果的报告，以及标准输出摘要；不改写数据集，
不评价任意对话意图或模型准确率。

Audit default-plot contracts in registry-synthesized chat datasets.

This checks the known prompt format and explicit default-plot instruction, not
arbitrary conversational intent or model accuracy. It never rewrites a dataset.
"""
from __future__ import annotations
import argparse
import hashlib
import json
from pathlib import Path
import sys

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'src'))
from sat_sim.task_models import canonicalize_task_spec


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def audit(directory):
    results=[]
    sources={}
    identifiers={}
    for split in ['train','validation']:
        path=directory/f'{split}.jsonl'
        sources[split]={'path':str(path.resolve()),'sha256':sha(path)}
        seen=set()
        for number,line in enumerate(path.read_text(encoding='utf-8').splitlines(),start=1):
            if not line.strip():
                continue
            item={'split':split,'line':number,'checks':{}}
            try:
                row=json.loads(line)
                item['id']=row['id']
                item['checks']['unique_id_in_split']=row['id'] not in seen
                seen.add(row['id'])
                prompt=row['messages'][1]['content']
                context=json.loads(prompt.split('## Capability context\n```json\n',1)[1].split('\n```',1)[0])
                instruction='use default_trace_fields for plots' in prompt
                item['checks']['known_default_plot_instruction']=instruction
                label=canonicalize_task_spec(json.loads(row['messages'][-1]['content']))
                default=context['selected_capability']['default_trace_fields']
                item['checks']['label_matches_instructed_default_plots']=label['outputs']['plots']==default
                if not item['checks']['label_matches_instructed_default_plots']:
                    item.update(instructed_default_plots=default,label_plots=label['outputs']['plots'])
            except (KeyError,IndexError,TypeError,ValueError) as exc:
                item['checks']['supported_record_parses']=False
                item['error']={'type':type(exc).__name__,'message':str(exc)}
            item['passed']=bool(item['checks']) and all(item['checks'].values())
            results.append(item)
        identifiers[split]=seen
    disjoint=not bool(identifiers['train'] & identifiers['validation'])
    nonempty=all(identifiers[split] for split in identifiers)
    unchanged=all(sha(directory/f'{split}.jsonl')==sources[split]['sha256'] for split in sources)
    passed=all(row['passed'] for row in results) and disjoint and bool(nonempty) and unchanged
    return {'status':'PASS_SCOPED_CONTRACT' if passed else 'FAIL_SCOPED_CONTRACT',
            'scope':'Known registry-synthesized default-telemetry prompts only. Checks the explicit plot instruction against normalized labels and ID split separation; not complete dataset quality, arbitrary-intent validity, or model accuracy. Does not alter historical scores, labels or model outputs.',
            'source_sha256':sources,'canonicalizer_sha256':sha(ROOT/'src/sat_sim/task_models.py'),
            'inputs_unchanged':unchanged,'split_ids_disjoint':disjoint,'splits_nonempty':bool(nonempty),
            'records':len(results),'passing_records':sum(row['passed'] for row in results),
            'failing_records':sum(not row['passed'] for row in results),
            'split_counts':{split:sum(row['split']==split for row in results) for split in identifiers},
            'failures':[row for row in results if not row['passed']]}


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--dataset-dir',type=Path,required=True)
    parser.add_argument('--output',type=Path,required=True)
    args=parser.parse_args()
    if args.output.exists():
        parser.error('fresh report required; historical evidence is never overwritten')
    report=audit(args.dataset_dir.resolve())
    report['auditor_sha256']=sha(Path(__file__))
    args.output.write_text(json.dumps(report,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
    print(json.dumps({key:report[key] for key in ['status','records','passing_records','failing_records','split_counts','split_ids_disjoint','inputs_unchanged']}))
    return 0 if report['status']=='PASS_SCOPED_CONTRACT' else 1


if __name__=='__main__':
    raise SystemExit(main())
