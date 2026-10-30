#!/usr/bin/env python3
"""用途：验证分块监督损失与完整交叉熵的数值和梯度等价性。
参数：--output 指定 JSON 证据路径；仅需 CPU PyTorch。
输出：合成张量上的损失/梯度误差；不证明学生模型任务能力。
"""
import argparse
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'src'))
from sat_sim_ml.training import completion_head_loss  # noqa: E402


def main():
    import torch
    from torch.nn import functional as F
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', type=Path, default=ROOT/'reports/local_requirements_completion/sft-loss-equivalence.json')
    args = parser.parse_args()
    torch.manual_seed(730)
    rows = []
    for chunk in (1,7,128):
        hidden = torch.randn(2,37,16, requires_grad=True)
        target = torch.randint(0,53,(2,37))
        target[:,:24] = -100
        target[1,-4:] = -100
        head = torch.nn.Linear(16,53,bias=False)
        dense = F.cross_entropy(head(hidden[:,:-1,:]).reshape(-1,53), target[:,1:].reshape(-1), ignore_index=-100)
        dense.backward()
        expected_hidden, expected_head = hidden.grad.clone(), head.weight.grad.clone()
        hidden.grad = None
        head.weight.grad = None
        bounded = completion_head_loss(hidden,target,head,chunk_tokens=chunk)
        bounded.backward()
        row = {'chunk_tokens':chunk,'loss_abs_error':abs(dense.item()-bounded.item()),
               'hidden_grad_max_error':(hidden.grad-expected_hidden).abs().max().item(),
               'head_grad_max_error':(head.weight.grad-expected_head).abs().max().item()}
        row['passed'] = max(row[key] for key in ('loss_abs_error','hidden_grad_max_error','head_grad_max_error')) < 1e-6
        rows.append(row)
    result = {'status':'PASS' if all(row['passed'] for row in rows) else 'FAIL', 'checks':rows,
              'scope':'Synthetic tensor loss/gradient equivalence; not student-model performance.'}
    args.output.parent.mkdir(parents=True,exist_ok=True)
    args.output.write_text(json.dumps(result,indent=2)+'\n',encoding='utf-8')
    print(json.dumps(result))
    return 0 if result['status'] == 'PASS' else 1


if __name__ == '__main__':
    raise SystemExit(main())
