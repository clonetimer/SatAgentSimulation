#!/usr/bin/env python3
"""
用途：从固定官方地址获取已批准的 Basilisk 2.11.0 Wheel，并构建可审计的 metadata-only satfix1 离线包。
参数：--platform 选择 linux、windows 或 all；--offline 仅使用已存在的官方 Wheel，不进行网络下载。
输出：在 third_party/wheels 下生成 satfix1 Wheel、逐文件载荷一致性证据和 JSON 构建报告。
"""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import ssl
import subprocess
import sys
import urllib.request

OFFICIAL_RELEASE = {
    "version": "2.11.0",
    "source_commit": "6c1511f5385dccaa73c9ae24522a74adc24fe623",
    "source_tag": "v2.11.0",
    "pypi_project": "https://pypi.org/project/bsk/2.11.0/",
    "files": {
        "linux_x86_64": {
            "filename": "bsk-2.11.0-cp39-abi3-manylinux_2_27_x86_64.manylinux_2_28_x86_64.whl",
            "url": "https://files.pythonhosted.org/packages/80/84/8fedfa36aae20953d6a087cfbfd3fb6da2391b2f6a518ecd2aa29bb8d846/bsk-2.11.0-cp39-abi3-manylinux_2_27_x86_64.manylinux_2_28_x86_64.whl",
            "sha256": "d51fe1ffa1b0b03dc415034b210497b28a2504428d4d8e71b481542a08c08a0a",
        },
        "windows_x86_64": {
            "filename": "bsk-2.11.0-cp39-abi3-win_amd64.whl",
            "url": "https://files.pythonhosted.org/packages/f9/b8/11bb1a7cc9ac88f94ae15ba78b3b509b6b8a32a62c42e431a311e91bd078/bsk-2.11.0-cp39-abi3-win_amd64.whl",
            "sha256": "5558268290474206a1a600c6bbf1847ed02e2c99c10a062e1d98a38076f58eb0",
        },
    },
}


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def fetch(url: str, target: Path) -> None:
    target.parent.mkdir(parents=True, exist_ok=True)
    request = urllib.request.Request(url, headers={"User-Agent": "SAT-SIM-offline-bundle/1.0"})
    context = ssl.create_default_context()
    with urllib.request.urlopen(request, context=context, timeout=180) as response, target.open("wb") as output:
        while True:
            block = response.read(1024 * 1024)
            if not block:
                break
            output.write(block)


def main() -> int:
    root = Path(__file__).resolve().parents[1]
    parser = argparse.ArgumentParser()
    parser.add_argument("--platform", choices=("linux_x86_64", "windows_x86_64", "all"), default="all")
    parser.add_argument("--download-dir", type=Path, default=root / "third_party" / "official_downloads")
    parser.add_argument("--output-dir", type=Path, default=root / "third_party" / "wheels")
    parser.add_argument("--offline", action="store_true", help="Do not download; require approved original wheels already present.")
    args = parser.parse_args()

    keys = list(OFFICIAL_RELEASE["files"]) if args.platform == "all" else [args.platform]
    originals: list[Path] = []
    retrieval: list[dict[str, object]] = []
    for key in keys:
        item = OFFICIAL_RELEASE["files"][key]
        target = args.download_dir / item["filename"]
        if not target.exists():
            if args.offline:
                raise FileNotFoundError(f"approved original wheel is missing in offline mode: {target}")
            fetch(str(item["url"]), target)
        actual = sha256(target)
        if actual != item["sha256"]:
            target.unlink(missing_ok=True)
            raise ValueError(f"official wheel SHA-256 mismatch for {item['filename']}: {actual}")
        originals.append(target)
        retrieval.append({"platform": key, "filename": item["filename"], "sha256": actual, "source_url": item["url"], "verified": True})

    report_path = args.output_dir / "bsk_2.11.0_satfix1_build_report.json"
    command = [sys.executable, str(root / "scripts" / "build_bsk_compat_wheels.py"), *map(str, originals), "--output-dir", str(args.output_dir), "--report", str(report_path)]
    subprocess.run(command, cwd=root, check=True)
    build_report = json.loads(report_path.read_text(encoding="utf-8"))
    bundle_report = {
        "schema_version": "sat-sim.bsk-offline-bundle.v1",
        "status": "PASS",
        "official_release": OFFICIAL_RELEASE,
        "retrieval": retrieval,
        "compatibility_build": build_report,
    }
    bundle_path = args.output_dir / "OFFLINE_BSK_BUNDLE_MANIFEST.json"
    bundle_path.write_text(json.dumps(bundle_report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({"status": "PASS", "manifest": str(bundle_path), "patched_wheels": [row["target"] for row in build_report["results"]]}, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
