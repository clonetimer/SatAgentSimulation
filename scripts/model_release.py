#!/usr/bin/env python3
"""用途：下载并校验已发布的微调适配器和固定基座，生成可迁移的本机配置。
参数：install/check/serve；--lock指定清单，--cache-dir指定目录；install可用--offline。
输出：模型安装/校验JSON；serve调用现有CUDA模型服务，不执行远程代码或pickle。
"""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path, PurePosixPath
import re
import shutil
import stat
import subprocess
import sys
import tempfile
import urllib.parse
import urllib.request
import zipfile

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_LOCK = ROOT / 'configs/models/satagent-v5-release.json'
DEFAULT_CACHE = ROOT / '.models/satagent-distributed'


def sha256(path):
    h = hashlib.sha256()
    with Path(path).open('rb') as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b''):
            h.update(block)
    return h.hexdigest()


def relative_path(value):
    if not isinstance(value, str) or not value or '\\' in value or ':' in value:
        raise ValueError('UNSAFE_RELEASE_PATH')
    path = PurePosixPath(value)
    if path.is_absolute() or any(p in ('.', '..') or p.endswith(('.', ' ')) for p in value.split('/')):
        raise ValueError('UNSAFE_RELEASE_PATH')
    return value


def records(rows):
    seen = set()
    if not isinstance(rows, list) or not rows:
        raise ValueError('RELEASE_FILE_LIST_MISSING')
    for row in rows:
        name = relative_path(row['path'])
        if name.casefold() in seen or not re.fullmatch('[0-9a-f]{64}', row['sha256']):
            raise ValueError('RELEASE_FILE_IDENTITY_INVALID')
        if type(row['bytes']) is not int or not 0 <= row['bytes'] <= 8 * 1024**3:
            raise ValueError('RELEASE_FILE_SIZE_INVALID')
        seen.add(name.casefold())


def read_lock(path):
    lock = json.loads(Path(path).read_bytes())
    if lock.get('schema_version') != 'sat-model-release.v1':
        raise ValueError('RELEASE_SCHEMA_INVALID')
    name = lock['release_id']
    if not re.fullmatch('[A-Za-z0-9][A-Za-z0-9_.-]{0,100}', name) or name.endswith('.'):
        raise ValueError('RELEASE_ID_INVALID')
    records(lock['payload'])
    records(lock['base']['files'])
    base_files = [{k: row[k] for k in ('path', 'sha256', 'bytes')} for row in lock['base']['files']]
    content_hash = hashlib.sha256(json.dumps(base_files, sort_keys=True).encode()).hexdigest()
    if content_hash != lock['base']['snapshot_content_sha256']:
        raise ValueError('BASE_MANIFEST_HASH_MISMATCH')
    archive = lock['archive']
    records([{'path': 'archive.zip', 'sha256': archive['sha256'], 'bytes': archive['bytes']}])
    return lock, base_files


def file_path(root, name):
    path = root / relative_path(name)
    if not path.resolve().is_relative_to(root.resolve()) or path.is_symlink():
        raise ValueError('UNSAFE_RELEASE_TARGET')
    return path


def verify_file(path, row):
    if not path.is_file() or path.stat().st_size != row['bytes'] or sha256(path) != row['sha256']:
        raise ValueError('MODEL_FILE_HASH_OR_SIZE_MISMATCH: ' + row.get('path', path.name))


def copy_stream(stream, path, row):
    path.parent.mkdir(parents=True, exist_ok=True)
    total = 0
    h = hashlib.sha256()
    with path.open('xb') as out:
        for block in iter(lambda: stream.read(1024 * 1024), b''):
            total += len(block)
            if total > row['bytes']:
                raise ValueError('DOWNLOAD_SIZE_MISMATCH')
            h.update(block)
            out.write(block)
    if total != row['bytes'] or h.hexdigest() != row['sha256']:
        raise ValueError('DOWNLOAD_HASH_OR_SIZE_MISMATCH: ' + row.get('path', path.name))


def download(url, path, row):
    parsed = urllib.parse.urlsplit(url)
    if parsed.scheme != 'https' and not (parsed.scheme == 'http' and parsed.hostname in ('127.0.0.1', 'localhost')):
        raise ValueError('DOWNLOAD_REQUIRES_HTTPS')
    request = urllib.request.Request(url, headers={'User-Agent': 'SatAgent-model-installer/1'})
    with urllib.request.urlopen(request, timeout=120) as response:
        length = response.headers.get('Content-Length')
        if length is not None and int(length) != row['bytes']:
            raise ValueError('DOWNLOAD_SIZE_MISMATCH')
        copy_stream(response, path, row)


def unpack(archive, target, payload):
    expected = {row['path']: row for row in payload}
    with zipfile.ZipFile(archive) as source:
        members = source.infolist()
        names = [info.filename for info in members]
        if len(names) != len(set(n.casefold() for n in names)) or set(names) != set(expected):
            raise ValueError('ARCHIVE_MEMBERS_MISMATCH')
        for info in members:
            mode = info.external_attr >> 16
            if info.is_dir() or stat.S_ISLNK(mode) or (stat.S_IFMT(mode) not in (0, stat.S_IFREG)):
                raise ValueError('ARCHIVE_UNSAFE_MEMBER')
            row = expected[info.filename]
            if info.file_size != row['bytes']:
                raise ValueError('ARCHIVE_SIZE_MISMATCH')
            with source.open(info) as stream:
                copy_stream(stream, file_path(target, info.filename), row)


def installation(lock_path, cache_dir, lock):
    target = Path(cache_dir).resolve() / lock['release_id']
    if target.is_symlink():
        raise ValueError('UNSAFE_RELEASE_TARGET')
    return target, {'schema_version': 'sat-model-installation.v1', 'release_id': lock['release_id'],
                    'model_name': lock['model_name'], 'release_lock_sha256': sha256(lock_path),
                    'base_manifest': str(target / 'base-manifest.json'), 'adapter': str(target / 'model/adapter')}


def check(lock_path=DEFAULT_LOCK, cache_dir=DEFAULT_CACHE):
    lock, base_files = read_lock(lock_path)
    target, expected = installation(lock_path, cache_dir, lock)
    state = json.loads(file_path(target, 'installation.json').read_bytes())
    if state != expected:
        raise ValueError('INSTALLATION_IDENTITY_MISMATCH')
    for row in lock['payload']:
        verify_file(file_path(target, row['path']), row)
    for row in base_files:
        verify_file(file_path(target / 'base', row['path']), row)
    provenance = json.loads(file_path(target, 'base-manifest.json').read_bytes())
    if (provenance['snapshot'] != str(target / 'base') or provenance['files'] != base_files
            or provenance['snapshot_content_sha256'] != lock['base']['snapshot_content_sha256']
            or provenance.get('trust_remote_code') is not False
            or provenance.get('schema_version') != 'sat-sft-base-model.v1'
            or provenance.get('model_id') != lock['base']['model_id']):
        raise ValueError('INSTALLED_BASE_MANIFEST_MISMATCH')
    return {'status': 'MODEL_FILES_VERIFIED_NOT_GPU_QUALIFIED', **expected}


def install(lock_path=DEFAULT_LOCK, cache_dir=DEFAULT_CACHE, *, archive_path=None, base_directory=None, offline=False):
    lock_path = Path(lock_path)
    lock, base_files = read_lock(lock_path)
    target, state = installation(lock_path, cache_dir, lock)
    if target.exists():
        check(lock_path, cache_dir)
        return state
    if offline and (archive_path is None or base_directory is None):
        raise ValueError('OFFLINE_INSTALL_REQUIRES_ARCHIVE_AND_BASE_DIRECTORY')
    cache = Path(cache_dir).resolve()
    cache.mkdir(parents=True, exist_ok=True)
    required = sum(row['bytes'] for row in lock['payload']) + sum(row['bytes'] for row in base_files) + lock['archive']['bytes']
    if shutil.disk_usage(cache).free < required + 256 * 1024**2:
        raise ValueError('MODEL_INSTALL_INSUFFICIENT_DISK_SPACE')
    with tempfile.TemporaryDirectory(prefix='.install-', dir=cache) as temporary:
        work = Path(temporary)
        archive = work / 'adapter.zip'
        if archive_path is None:
            download(lock['archive']['url'], archive, lock['archive'])
        else:
            with Path(archive_path).open('rb') as stream:
                copy_stream(stream, archive, lock['archive'])
        staged = work / 'ready'
        staged.mkdir()
        unpack(archive, staged, lock['payload'])
        (staged / 'base').mkdir()
        for row, download_row in zip(base_files, lock['base']['files']):
            destination = file_path(staged / 'base', row['path'])
            if base_directory is not None:
                with file_path(Path(base_directory), row['path']).open('rb') as stream:
                    copy_stream(stream, destination, row)
            else:
                download(download_row['url'], destination, row)
        provenance = {'schema_version': 'sat-sft-base-model.v1', 'model_id': lock['base']['model_id'],
                      'revision_requested': lock['base']['revision'], 'snapshot': str(target / 'base'),
                      'trust_remote_code': False, 'files': base_files,
                      'snapshot_content_sha256': lock['base']['snapshot_content_sha256']}
        for name, value in [('base-manifest.json', provenance), ('installation.json', state)]:
            (staged / name).write_text(json.dumps(value, indent=2) + '\n', encoding='utf-8')
        if target.exists():
            raise ValueError('MODEL_INSTALL_TARGET_ALREADY_EXISTS')
        staged.rename(target)
    return state


def serve_command(lock_path=DEFAULT_LOCK, cache_dir=DEFAULT_CACHE, *, python=sys.executable, port=8033, attention_backend='cudnn'):
    if not 1 <= port <= 65535 or attention_backend not in ('cudnn', 'auto'):
        raise ValueError('MODEL_SERVER_OPTION_INVALID')
    state = check(lock_path, cache_dir)
    return [str(python), str(ROOT / 'scripts/serve_modelscope_model.py'),
            '--base-manifest', state['base_manifest'], '--adapter', state['adapter'],
            '--model-name', state['model_name'], '--max-context', '16384',
            '--request-deadline-s', '270', '--max-waiting-requests', '0',
            '--attention-backend', attention_backend, '--port', str(port)]


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--lock', type=Path, default=DEFAULT_LOCK)
    commands = parser.add_subparsers(dest='command', required=True)
    for name in ('install', 'check', 'serve'):
        sub = commands.add_parser(name)
        sub.add_argument('--cache-dir', type=Path, default=DEFAULT_CACHE)
        if name == 'install':
            sub.add_argument('--archive', type=Path)
            sub.add_argument('--base-directory', type=Path)
            sub.add_argument('--offline', action='store_true')
        elif name == 'serve':
            sub.add_argument('--python', default=sys.executable)
            sub.add_argument('--port', type=int, default=8033)
            sub.add_argument('--attention-backend', choices=['cudnn', 'auto'], default='cudnn')
    args = parser.parse_args()
    try:
        if args.command == 'install':
            result = install(args.lock, args.cache_dir, archive_path=args.archive,
                             base_directory=args.base_directory, offline=args.offline)
            print(json.dumps({'status': 'MODEL_INSTALLED_NOT_GPU_QUALIFIED', **result}))
        elif args.command == 'check':
            print(json.dumps(check(args.lock, args.cache_dir)))
        else:
            command = serve_command(args.lock, args.cache_dir, python=args.python,
                                    port=args.port, attention_backend=args.attention_backend)
            probe = subprocess.run([args.python, '-c',
                'import torch,transformers,peft,lmformatenforcer,uvicorn; raise SystemExit(0 if torch.cuda.is_available() else 3)'],
                capture_output=True, text=True)
            if probe.returncode:
                raise ValueError('CUDA_OR_SFT_ENVIRONMENT_UNAVAILABLE: use the locked .venv-sft environment and a compatible NVIDIA GPU; otherwise configure a remote model service')
            return subprocess.call(command)
    except (ValueError, OSError, KeyError, zipfile.BadZipFile) as error:
        print(json.dumps({'status': 'FAILED', 'reason': str(error)}), file=sys.stderr)
        return 1
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
