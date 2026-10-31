#!/usr/bin/env python3
"""用途：在本机提供真实 ModelScope 基座或已完成 LoRA 模型的推理接口。
参数：--base-manifest、--adapter、--model-name、--port；默认仅绑定 127.0.0.1。
输出：OpenAI-compatible /v1/models 和 /v1/chat/completions，不使用模板替代模型。
"""
import argparse
import math
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'src'))
from sat_sim_ml.serving import LocalModelEngine, create_model_app  # noqa: E402


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--base-manifest', type=Path, default=ROOT/'.models/Qwen--Qwen3-0.6B-provenance.json')
    parser.add_argument('--adapter', type=Path)
    parser.add_argument('--model-name')
    parser.add_argument('--port', type=int, default=8031)
    parser.add_argument('--max-context', type=int, default=16384)
    parser.add_argument('--attention-backend', choices=['cudnn','auto'], default='cudnn')
    parser.add_argument('--request-deadline-s', type=float, default=270.,
        help='Cooperative request budget including queue/tokenization; cannot interrupt a current CUDA pass.')
    parser.add_argument('--max-waiting-requests', type=int, default=0,
        help='Bounded waiting capacity; zero rejects concurrent requests immediately with HTTP429.')
    parser.add_argument('--disconnect-poll-interval-s', type=float, default=.1)
    args = parser.parse_args()
    if not math.isfinite(args.request_deadline_s) or args.request_deadline_s <= 0:
        parser.error('--request-deadline-s must be finite and positive')
    if not 0 <= args.max_waiting_requests <= 64:
        parser.error('--max-waiting-requests must be between 0 and 64')
    if not math.isfinite(args.disconnect_poll_interval_s) or not 0 < args.disconnect_poll_interval_s <= 1:
        parser.error('--disconnect-poll-interval-s must be finite and in (0, 1]')
    engine = LocalModelEngine(args.base_manifest, args.adapter, model_name=args.model_name, max_context=args.max_context,
                              attention_backend=args.attention_backend)
    import uvicorn
    # One model/GPU worker only. Admission is process-local, not a shared
    # scheduler for multiple uvicorn workers.
    uvicorn.run(create_model_app(engine, request_deadline_s=args.request_deadline_s,
        max_waiting_requests=args.max_waiting_requests,
        disconnect_poll_interval_s=args.disconnect_poll_interval_s),
        host='127.0.0.1', port=args.port, access_log=False)


if __name__ == '__main__':
    main()
