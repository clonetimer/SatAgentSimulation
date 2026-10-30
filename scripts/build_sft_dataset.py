#!/usr/bin/env python3
"""用途：从已校验注册模板构建 TaskSpec 微调集，不使用正式评测答案。
参数：--output-dir 指定 JSONL 和清单目录。
输出：按语义场景族隔离的 train/validation 与来源、拒绝记录和 SHA-256。
"""
import argparse
import json
from pathlib import Path
from sat_sim.sft_data import build_dataset

ROOT = Path(__file__).resolve().parents[1]


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output-dir', type=Path, default=ROOT/'data/training/taskspec_lora')
    args = parser.parse_args()
    result = build_dataset(args.output_dir, evaluation_manifest=ROOT/'evals/agent_golden/golden_set.json')
    print(json.dumps({k:v for k,v in result.items() if k != 'rejected'}, ensure_ascii=False))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
