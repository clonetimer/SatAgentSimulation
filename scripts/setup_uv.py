#!/usr/bin/env python3
"""用途：使用 uv 安装固定依赖、官方 Basilisk 兼容 wheel 与离线环境资产。
参数：--offline 禁止网络；--without-environment 仅安装开发环境，不签发严格资产通过。
输出：项目 .venv、依赖与资产清单，以及实际 doctor 检查结果。
"""
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import platform
import shutil
import subprocess

ROOT = Path(__file__).resolve().parents[1]


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--offline', action='store_true')
    parser.add_argument('--without-environment', action='store_true')
    args = parser.parse_args()
    uv = shutil.which('uv')
    if not uv:
        raise RuntimeError('uv is required on PATH')
    env = os.environ.copy()
    env['UV_CACHE_DIR'] = str(ROOT/'.uv-cache')
    python = ROOT/'.venv'/('Scripts/python.exe' if os.name == 'nt' else 'bin/python')

    def run(command):
        subprocess.run(list(map(str, command)), cwd=ROOT, env=env, check=True)

    if not python.exists():
        run([uv, 'venv', '--python', '3.12', ROOT/'.venv', *(['--offline'] if args.offline else [])])
    platform_key = {'Windows':'windows_x86_64', 'Linux':'linux_x86_64'}.get(platform.system())
    if not platform_key or platform.machine().lower() not in ('amd64','x86_64'):
        raise RuntimeError('Approved prebuilt runtime requires Windows/Linux x86_64')
    run([python, ROOT/'scripts/fetch_official_bsk_and_build_offline_bundle.py',
         '--platform', platform_key, *(['--offline'] if args.offline else [])])
    attestation = json.loads(subprocess.check_output(
        [str(python), str(ROOT/'scripts/install_offline_bsk.py'), '--check-only'],
        cwd=ROOT, env=env, text=True))
    if attestation.get('status') != 'PASS' or attestation.get('record_verified') is not True:
        raise RuntimeError('BSK_WHEEL_ATTESTATION_REQUIRED')
    run([uv, 'pip', 'install', '--python', python, '--find-links', ROOT/'third_party/wheels',
         attestation['wheel_path'], '-r', ROOT/'requirements-lock.txt', '-e', '.[api,dev,ui-test]',
         *(['--offline'] if args.offline else [])])
    if not args.without_environment:
        run([python, ROOT/'scripts/fetch_environment_assets.py', *(['--offline'] if args.offline else [])])
    run([python, '-m', 'pip', 'check'])
    run([python, '-m', 'sat_sim.agent_cli', 'doctor', '--require-api',
         *([] if args.without_environment else ['--strict-assets'])])
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
