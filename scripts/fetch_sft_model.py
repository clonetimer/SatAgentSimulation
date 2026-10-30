#!/usr/bin/env python3
"""用途：获取 ModelScope 基座的 safetensors、分词器和配置，并记录来源摘要。
参数：--model-id、--revision、--cache-dir；--offline 禁止下载。
输出：模型本地快照和逐文件 SHA-256；不加载或执行远程模型代码。
"""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--model-id', default='Qwen/Qwen3-0.6B')
    parser.add_argument('--revision', default='master')
    parser.add_argument('--cache-dir', type=Path, default=ROOT/'.models')
    parser.add_argument('--offline', action='store_true')
    args = parser.parse_args()
    args.cache_dir.mkdir(parents=True, exist_ok=True)
    os.environ['MODELSCOPE_CACHE'] = str(args.cache_dir.resolve())
    from modelscope import snapshot_download
    snapshot = Path(snapshot_download(args.model_id, revision=args.revision,
        cache_dir=str(args.cache_dir.resolve()), local_files_only=args.offline,
        allow_patterns=['*.json','*.safetensors','*.txt','*.model','LICENSE','*.md'],
        ignore_patterns=['*.py','*.bin','*.pt','*.pth'], max_workers=4)).resolve()
    files = []
    for path in sorted(snapshot.rglob('*')):
        if not path.is_file() or any(part.startswith('.') for part in path.relative_to(snapshot).parts):
            continue
        digest = hashlib.sha256()
        with path.open('rb') as stream:
            while block := stream.read(1024*1024):
                digest.update(block)
        files.append({'path':path.relative_to(snapshot).as_posix(), 'sha256':digest.hexdigest(), 'bytes':path.stat().st_size})
    if not any(item['path'].endswith('.safetensors') for item in files):
        raise RuntimeError('BASE_MODEL_SAFETENSORS_MISSING')
    evidence = {'schema_version':'sat-sft-base-model.v1', 'model_id':args.model_id,
                'revision_requested':args.revision, 'snapshot':str(snapshot),
                'source_url':f'https://modelscope.cn/models/{args.model_id}',
                'downloaded_at_utc':datetime.now(timezone.utc).isoformat(),
                'trust_remote_code':False, 'files':files}
    evidence['snapshot_content_sha256'] = hashlib.sha256(json.dumps(files, sort_keys=True).encode()).hexdigest()
    output = args.cache_dir/(args.model_id.replace('/','--')+'-provenance.json')
    output.write_text(json.dumps(evidence, ensure_ascii=False, indent=2)+'\n', encoding='utf-8')
    print(json.dumps({'status':'DOWNLOADED_AND_HASHED', 'snapshot':str(snapshot), 'manifest':str(output),
                      'snapshot_content_sha256':evidence['snapshot_content_sha256']}), flush=True)
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
