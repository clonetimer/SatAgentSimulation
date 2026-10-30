#!/usr/bin/env python3
"""用途：用实际基座分词器核验微调样本长度和 completion-only 标签。
参数：--dataset-dir、--base-manifest、--max-length、--output。
输出：完整数据覆盖及 token 长度报告；任一样本超限时非零退出，不截断答案。
"""
import argparse
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'src'))
from sat_sim_ml.data import encode_record, load_training_data  # noqa: E402


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--dataset-dir', type=Path, default=ROOT/'data/training/taskspec_lora')
    parser.add_argument('--base-manifest', type=Path, default=ROOT/'.models/Qwen--Qwen3-0.6B-provenance.json')
    parser.add_argument('--max-length', type=int, default=12288)
    parser.add_argument('--output', type=Path, default=ROOT/'reports/local_requirements_completion/sft-token-lengths.json')
    args = parser.parse_args()
    from transformers import AutoTokenizer
    source = json.loads(args.base_manifest.read_text(encoding='utf-8'))
    tokenizer = AutoTokenizer.from_pretrained(source['snapshot'], local_files_only=True, trust_remote_code=False)
    records, _ = load_training_data(args.dataset_dir)
    sizes = {}
    failures = []
    for split, rows in records.items():
        lengths = []
        for row in rows:
            try:
                encoded = encode_record(tokenizer,row,args.max_length)
                lengths.append(len(encoded['input_ids']))
                if not any(label != -100 for label in encoded['labels']):
                    raise ValueError('NO_ASSISTANT_TOKENS')
            except ValueError as exc:
                failures.append({'id':row['id'], 'capability_id':row['capability_id'], 'reason':str(exc)})
        sizes[split] = {'count':len(rows), 'encoded_count':len(lengths),
                        'max_tokens':max(lengths,default=0), 'min_tokens':min(lengths,default=0)}
    result = {'status':'PASS' if not failures else 'FAIL', 'splits':sizes,
              'sequence_limit':args.max_length, 'truncation_allowed':False, 'failures':failures}
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(result, ensure_ascii=False, indent=2)+'\n', encoding='utf-8')
    print(json.dumps(result, ensure_ascii=False))
    return 0 if not failures else 1


if __name__ == '__main__':
    raise SystemExit(main())
