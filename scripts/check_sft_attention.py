#!/usr/bin/env python3
"""用途：对本机 cuDNN 预填充/数学解码注意力执行数值、梯度和因果性检查。
参数：--output 指定 JSON 证据路径；需要可用的 CUDA 训练环境。
输出：BF16 与数学 SDPA 的误差、未来 token 隔离结果，不证明模型任务精度。
"""
import argparse
import json
from pathlib import Path
import sys
from types import SimpleNamespace

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'src'))


def main():
    import torch
    from torch.nn.attention import sdpa_kernel, SDPBackend
    from transformers.modeling_utils import ALL_ATTENTION_FUNCTIONS
    from transformers.integrations.sdpa_attention import sdpa_attention_forward
    from sat_sim_ml.attention import register_cudnn_prefill_attention
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output',type=Path,default=ROOT/'reports/local_requirements_completion/sft-attention-equivalence.json')
    args = parser.parse_args()
    torch.manual_seed(719)
    module = SimpleNamespace(num_key_value_groups=2, is_causal=True)
    function = ALL_ATTENTION_FUNCTIONS[register_cudnn_prefill_attention()]
    rows = []
    for length in (127,1):
        inputs = [torch.randn(1,heads,seq,64,device='cuda',dtype=torch.bfloat16,requires_grad=True)
                  for heads,seq in ((4,length),(2,127),(2,127))]
        with sdpa_kernel(SDPBackend.MATH):
            expected,_ = sdpa_attention_forward(module,*inputs,None,scaling=64**-.5)
            gradients = torch.autograd.grad(expected.float().square().mean(),inputs)
        actual,_ = function(module,*inputs,None,scaling=64**-.5)
        observed = torch.autograd.grad(actual.float().square().mean(),inputs)
        def relative(delta,reference):
            return float(delta.detach().float().norm()/reference.detach().float().norm().clamp_min(1e-12))
        row = {'query_length':length,'output_relative_error':relative(actual-expected,expected),
               'gradient_relative_errors':[relative(a-e,e) for a,e in zip(observed,gradients,strict=True)]}
        row['passed'] = max(row['output_relative_error'],*row['gradient_relative_errors']) < .01
        rows.append(row)
        if length > 1:
            changed = [item.detach().clone() for item in inputs]
            changed[1][:,:,64:] += 10
            changed[2][:,:,64:] += 10
            perturbed,_ = function(module,*changed,None,scaling=64**-.5)
            unchanged = bool(torch.equal(actual[:,:64].detach(),perturbed[:,:64]))
            rows.append({'future_tokens_do_not_change_past_outputs':unchanged,'passed':unchanged})
    result = {'status':'PASS' if all(row['passed'] for row in rows) else 'FAIL', 'checks':rows,
              'scope':'Synthetic BF16 attention equivalence/causality; not model task accuracy.',
              'torch':torch.__version__,'gpu':torch.cuda.get_device_name(0)}
    args.output.parent.mkdir(parents=True,exist_ok=True)
    args.output.write_text(json.dumps(result,indent=2)+'\n',encoding='utf-8')
    print(json.dumps(result))
    return 0 if result['status']=='PASS' else 1


if __name__ == '__main__':
    raise SystemExit(main())
