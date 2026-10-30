#!/usr/bin/env python3
"""Fetch the fixed NAIF and Basilisk environment data for offline operation.

用途：获取并校验离线 SPICE、WMM2025 与地球 J2 数据。
参数：--root 指定资产根目录；--offline 仅验证本地文件。
输出：生成环境资产来源、上游摘要与 SHA-256 清单。

Runtime never downloads assets. This setup command stages each file, verifies
the available upstream digest and format, then records SHA-256 provenance.
"""
from __future__ import annotations

import argparse
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import urllib.request
import time

ROOT = Path(__file__).resolve().parents[1]
NAIF = 'https://naif.jpl.nasa.gov/pub/naif/generic_kernels/'
BSK = 'https://raw.githubusercontent.com/AVSLab/basilisk/6c1511f5385dccaa73c9ae24522a74adc24fe623/supportData/'
# NAIF publishes de430's MD5 in spk/planets/aa_checksums.txt. The
# Basilisk 2.11 registry pins the WMM2025/GGM03S data digests.
ASSETS = (
    ('spice/de430.bsp', NAIF+'spk/planets/de430.bsp', '91e21181f6a96edbfeb1ff5a419ce095', b'DAF/SPK'),
    ('spice/naif0012.tls', NAIF+'lsk/naif0012.tls', None, b'KPL/LSK'),
    ('spice/pck00010.tpc', NAIF+'pck/pck00010.tpc', None, b'KPL/PCK'),
    ('spice/gm_de431.tpc', NAIF+'pck/gm_de431.tpc', None, b'KPL/PCK'),
    ('spice/de-403-masses.tpc', NAIF+'pck/de-403-masses.tpc', None, b'KPL/PCK'),
    ('wmm/WMM.COF', BSK+'MagneticField/WMM2025.COF', 'a1b6402dd23658d15affb0f31babccc7', b'2025.0'),
    ('gravity/GGM03S-J2-only.txt', BSK+'LocalGravData/GGM03S-J2-only.txt', '08e8b083c473e8f5261628e1531a38e8', None),
)


def validate(path: Path, expected_md5: str | None, prefix: bytes | None) -> dict:
    raw = path.read_bytes()
    if not raw or (prefix and not raw.lstrip().startswith(prefix)):
        raise ValueError(f'ENVIRONMENT_ASSET_FORMAT_INVALID: {path.name}')
    digest = hashlib.md5(raw, usedforsecurity=False).hexdigest()
    if expected_md5 and digest != expected_md5:
        raise ValueError(f'ENVIRONMENT_ASSET_DIGEST_MISMATCH: {path.name}; expected={expected_md5}; actual={digest}; bytes={len(raw)}')
    return {'sha256':hashlib.sha256(raw).hexdigest(), 'size_bytes':len(raw),
            'upstream_md5':expected_md5, 'upstream_digest_checked':bool(expected_md5)}


def download(url: str, target: Path, *, workers: int = 4, progress=None) -> None:
    """Bounded ranges avoid proxy truncation of large ephemeris kernels."""
    headers = {'User-Agent':'SAT-SIM-environment-setup/1', 'Accept-Encoding':'identity'}
    with urllib.request.urlopen(urllib.request.Request(url, headers=headers, method='HEAD'), timeout=30) as response:
        size = int(response.headers.get('Content-Length', 0))
    chunk_size = 8*1024*1024
    if size <= 2*chunk_size:
        with urllib.request.urlopen(urllib.request.Request(url, headers=headers), timeout=120) as response:
            raw = response.read()
        if size and len(raw) != size:
            raise ValueError(f'ENVIRONMENT_DOWNLOAD_TRUNCATED: expected={size}, actual={len(raw)}')
        target.write_bytes(raw)
        return
    parts = target.with_name(target.name+'.parts')
    parts.mkdir(parents=True, exist_ok=True)

    def fetch_part(start: int) -> Path:
        end = min(start+chunk_size, size)-1
        part = parts/f'{start:012d}'
        if part.is_file() and part.stat().st_size == end-start+1:
            return part
        for attempt in range(3):
            try:
                request = urllib.request.Request(url, headers={**headers, 'Range':f'bytes={start}-{end}'})
                with urllib.request.urlopen(request, timeout=120) as response:
                    if response.status != 206 or response.headers.get('Content-Range') != f'bytes {start}-{end}/{size}':
                        raise ValueError('ENVIRONMENT_RANGE_RESPONSE_INVALID')
                    raw = response.read(end-start+2)
                if len(raw) != end-start+1:
                    raise ValueError(f'ENVIRONMENT_RANGE_TRUNCATED: start={start}, expected={end-start+1}, actual={len(raw)}')
                staged = part.with_suffix('.pending')
                staged.write_bytes(raw)
                os.replace(staged, part)
                if progress is not None:
                    progress(start, end, size)
                return part
            except (OSError, ValueError):
                if attempt == 2:
                    raise
                time.sleep(.5*(attempt+1))
        raise RuntimeError('ENVIRONMENT_RANGE_NOT_FETCHED')

    with ThreadPoolExecutor(max_workers=workers) as executor:
        completed = list(executor.map(fetch_part, range(0, size, chunk_size)))
    with target.open('wb') as output:
        for part in completed:
            output.write(part.read_bytes())


def fetch_asset(root: Path, asset: tuple, *, offline: bool = False) -> dict:
    relative, url, digest, prefix = asset
    target = root / relative
    target.parent.mkdir(parents=True, exist_ok=True)
    if not target.is_file():
        if offline:
            raise FileNotFoundError(f'ENVIRONMENT_ASSET_MISSING: {relative}')
        staged = target.with_name(target.name + '.part')
        try:
            download(url, staged)
            validate(staged, digest, prefix)
            os.replace(staged, target)
        finally:
            staged.unlink(missing_ok=True)
    evidence = validate(target, digest, prefix)
    return {'path':relative, 'source_url':url, **evidence}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--root', type=Path, default=ROOT/'third_party')
    parser.add_argument('--offline', action='store_true')
    args = parser.parse_args()
    root = args.root.resolve()
    with ThreadPoolExecutor(max_workers=4) as executor:
        records = list(executor.map(lambda item: fetch_asset(root, item, offline=args.offline), ASSETS))
    manifest = {'schema_version':'sat-environment-assets.v1', 'status':'PASS',
                'generated_at_utc':datetime.now(timezone.utc).isoformat(),
                'assets':records, 'runtime_network_required':False,
                'scope':'Public environmental reference data; not spacecraft calibration or flight validation.'}
    path = root/'environment_assets_manifest.json'
    path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2)+'\n', encoding='utf-8')
    # The recovery verifier consumes paths relative to the project root.
    # Mandatory environmental data comes from the just-verified records;
    # optional runtime bundles must never become mandatory by being listed.
    files = [{**record, 'path':f'third_party/{record["path"]}', 'required':True}
             for record in records]
    for name in (
        'bsk-2.11.0+satfix2-cp39-abi3-win_amd64.whl',
        'bsk-2.11.0+satfix2-cp39-abi3-manylinux_2_27_x86_64.manylinux_2_28_x86_64.whl',
        'bsk_2.11.0_satfix2_build_report.json',
    ):
        optional = root/'wheels'/name
        files.append({'path':f'third_party/wheels/{name}', 'required':False,
                      'sha256':hashlib.sha256(optional.read_bytes()).hexdigest() if optional.is_file() else None,
                      'digest_scope':'local artifact identity; not independent build approval'})
    recovery_manifest = {
        'schema_version':'sat-sim.third-party-manifest.v1',
        'status':'PASS_MANDATORY_ASSETS_EXTERNAL_BSK_POLICY',
        'generated_at_utc':manifest['generated_at_utc'],
        'runtime_dependency_mode':'external_python_environment',
        'scope':manifest['scope'], 'files':files,
    }
    (root/'THIRD_PARTY_MANIFEST.json').write_text(
        json.dumps(recovery_manifest, ensure_ascii=False, indent=2)+'\n', encoding='utf-8')
    print(json.dumps({'status':'PASS', 'manifest':str(path), 'asset_count':len(records)}))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
