#!/usr/bin/env python3
"""用途：从 PyTorch 官方索引下载并核验 CUDA wheel，支持大文件分段续传。
参数：--version 固定版本；--output-dir 指定本地 wheelhouse。
输出：经过官方索引 SHA-256 核验的 Windows CPython 3.12 wheel 与来源记录。
"""
from __future__ import annotations

import argparse
import hashlib
from html.parser import HTMLParser
import json
import os
from pathlib import Path
from urllib.parse import parse_qs, unquote, urljoin, urlsplit, urlunsplit
import urllib.request

from fetch_environment_assets import download

ROOT = Path(__file__).resolve().parents[1]
INDEX = 'https://download.pytorch.org/whl/cu128/torch/'


class Links(HTMLParser):
    def __init__(self):
        super().__init__()
        self.links = []
    def handle_starttag(self, tag, attrs):
        if tag == 'a':
            value = dict(attrs).get('href')
            if value:
                self.links.append(value)


def sha256(path):
    result = hashlib.sha256()
    with path.open('rb') as stream:
        while block := stream.read(1024*1024):
            result.update(block)
    return result.hexdigest()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--version', default='2.11.0+cu128')
    parser.add_argument('--output-dir', type=Path, default=ROOT/'third_party/wheels')
    parser.add_argument('--workers', type=int, default=4)
    args = parser.parse_args()
    if not 1 <= args.workers <= 8:
        parser.error('--workers must be between 1 and 8')
    filename = f'torch-{args.version}-cp312-cp312-win_amd64.whl'
    with urllib.request.urlopen(INDEX, timeout=30) as response:
        html = response.read().decode('utf-8')
    links = Links()
    links.feed(html)
    matches = [urljoin(INDEX, link) for link in links.links if unquote(urlsplit(link).path).rsplit('/',1)[-1] == filename]
    if len(matches) != 1:
        raise RuntimeError(f'EXACT_OFFICIAL_TORCH_WHEEL_NOT_FOUND: {filename}')
    parsed = urlsplit(matches[0])
    expected = parse_qs(parsed.fragment).get('sha256', [None])[0]
    if expected is None or len(expected) != 64:
        raise RuntimeError('OFFICIAL_TORCH_WHEEL_SHA256_MISSING')
    url = urlunsplit(parsed._replace(fragment=''))
    args.output_dir.mkdir(parents=True, exist_ok=True)
    target = args.output_dir/filename
    if not target.exists():
        staged = target.with_name(target.name+'.part')
        print(json.dumps({'state':'DOWNLOADING', 'file':filename, 'expected_sha256':expected}), flush=True)
        download(url, staged, workers=args.workers, progress=lambda start,end,size:print(
            json.dumps({'state':'RANGE_COMPLETE', 'start':start, 'end':end, 'total_bytes':size}), flush=True))
        actual = sha256(staged)
        if actual != expected:
            raise ValueError(f'TORCH_WHEEL_SHA256_MISMATCH: {actual}')
        os.replace(staged, target)
    actual = sha256(target)
    if actual != expected:
        raise ValueError(f'TORCH_WHEEL_SHA256_MISMATCH: {actual}')
    evidence = {'schema_version':'sat-torch-wheel-source.v1', 'status':'PASS',
                'index_url':INDEX, 'source_url':url, 'filename':filename,
                'sha256':actual, 'expected_sha256':expected, 'size_bytes':target.stat().st_size}
    (args.output_dir/'torch_cuda_source.json').write_text(json.dumps(evidence, indent=2)+'\n', encoding='utf-8')
    print(json.dumps(evidence), flush=True)
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
