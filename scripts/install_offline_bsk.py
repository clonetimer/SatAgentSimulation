#!/usr/bin/env python3
"""
用途：从 third_party/wheels 离线安装与当前平台匹配且构建报告为 PASS 的 satfix1 Basilisk Wheel。
参数：无命令行参数；依据当前操作系统自动选择 Linux 或 Windows Wheel。
输出：执行离线 pip 安装并打印已安装 bsk 版本和 Basilisk 模块路径；失败时返回非零退出码。
"""
from __future__ import annotations
import json, platform, subprocess, sys
from pathlib import Path


def main() -> int:
    root=Path(__file__).resolve().parents[1]
    wheels=root/'third_party'/'wheels'
    report_path=wheels/'bsk_2.11.0_satfix1_build_report.json'
    if not report_path.is_file(): raise FileNotFoundError(report_path)
    report=json.loads(report_path.read_text(encoding='utf-8'))
    if report.get('status')!='PASS': raise RuntimeError('Basilisk compatibility build report is not PASS')
    tag='win_amd64' if platform.system().lower().startswith('win') else 'manylinux_2_27_x86_64'
    matches=[wheels/item['target'] for item in report.get('results',[]) if tag in item.get('target','')]
    if len(matches)!=1 or not matches[0].is_file(): raise FileNotFoundError(f'platform wheel not present for {tag}')
    subprocess.run([sys.executable,'-m','pip','install','--no-index','--no-deps','--force-reinstall',str(matches[0])],check=True)
    subprocess.run([sys.executable,'-c','import importlib.metadata as m; import Basilisk; print(m.version("bsk")); print(Basilisk.__path__)'],check=True)
    return 0

if __name__=='__main__': raise SystemExit(main())
