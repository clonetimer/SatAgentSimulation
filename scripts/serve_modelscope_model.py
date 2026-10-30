#!/usr/bin/env python3
"""用途：在本机提供真实 ModelScope 基座或已完成 LoRA 模型的推理接口。
参数：--base-manifest、--adapter、--model-name、--port；默认仅绑定 127.0.0.1。
输出：OpenAI-compatible /v1/models 和 /v1/chat/completions，不使用模板替代模型。
"""
import argparse
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
    args = parser.parse_args()
    engine = LocalModelEngine(args.base_manifest, args.adapter, model_name=args.model_name, max_context=args.max_context,
                              attention_backend=args.attention_backend)
    import uvicorn
    uvicorn.run(create_model_app(engine), host='127.0.0.1', port=args.port, access_log=False)


if __name__ == '__main__':
    main()
