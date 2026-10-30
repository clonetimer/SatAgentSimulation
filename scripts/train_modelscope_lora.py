#!/usr/bin/env python3
"""用途：对本地 ModelScope 基座执行 GPU LoRA 微调，保持正式评测独立。
参数：--dataset-dir、--base-manifest、--output-dir，以及训练超参数。
输出：safetensors 适配器、训练/验证损失、显存峰值和来源摘要；不签发任务成功声明。
"""
import argparse
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'src'))
from sat_sim_ml.training import train  # noqa: E402


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--dataset-dir', type=Path, default=ROOT/'data/training/taskspec_lora')
    parser.add_argument('--base-manifest', type=Path, default=ROOT/'.models/Qwen--Qwen3-0.6B-provenance.json')
    parser.add_argument('--output-dir', type=Path, default=ROOT/'training_runs/qwen3_0_6b_taskspec_lora')
    parser.add_argument('--epochs', type=float, default=3.)
    parser.add_argument('--max-length', type=int, default=12288)
    parser.add_argument('--loss-chunk-tokens', type=int, default=128)
    parser.add_argument('--attention-backend', choices=['cudnn','auto'], default='cudnn')
    parser.add_argument('--smoke-longest', action='store_true', help='Only with positive max-steps; exercise the longest samples.')
    parser.add_argument('--rank', type=int, default=16)
    parser.add_argument('--gradient-accumulation', type=int, default=8)
    parser.add_argument('--learning-rate', type=float, default=2e-4)
    parser.add_argument('--seed', type=int, default=20261025)
    parser.add_argument('--max-steps', type=int, default=-1, help='Positive values are smoke training only.')
    parser.add_argument('--resume', type=Path)
    args = parser.parse_args()
    if args.epochs <= 0 or args.max_length < 256 or args.rank < 1 or args.gradient_accumulation < 1:
        parser.error('positive epochs/rank/accumulation and max-length >= 256 are required')
    if args.loss_chunk_tokens < 1 or (args.smoke_longest and args.max_steps <= 0):
        parser.error('positive loss-chunk-tokens required; smoke-longest requires positive max-steps')
    train(args)
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
