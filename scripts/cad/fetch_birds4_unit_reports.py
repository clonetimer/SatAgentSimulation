#!/usr/bin/env python3
"""用途：下载并校验固定 commit 的 BIRDS-4 三颗卫星 VT 报告及 FM TVT 报告。
参数：--manifest、--output-dir。
输出：逐文件 Git blob SHA-1 校验收据；不将测试报告自动认定为 as-built BOM。
"""
from __future__ import annotations

import argparse
import hashlib
import json
import urllib.parse
import urllib.request
from pathlib import Path


def git_blob_sha1(path: Path) -> str:
    size = path.stat().st_size
    digest = hashlib.sha1()
    digest.update(f"blob {size}\0".encode("ascii"))
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--manifest", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    args = parser.parse_args()
    manifest = json.loads(args.manifest.read_text(encoding="utf-8"))
    repository = manifest["source_repository"].removeprefix("https://github.com/")
    commit = manifest["source_commit"]
    args.output_dir.mkdir(parents=True, exist_ok=True)
    receipts = []
    for record in manifest["documents"]:
        relative = record["path"]
        url = f"https://raw.githubusercontent.com/{repository}/{commit}/{urllib.parse.quote(relative, safe='/')}"
        output = args.output_dir / Path(relative).name
        request = urllib.request.Request(url, headers={"User-Agent": "SatAgentSimulation-P9M3-evidence/1.0"})
        with urllib.request.urlopen(request, timeout=180) as response, output.open("wb") as stream:
            while chunk := response.read(1024 * 1024):
                stream.write(chunk)
        actual = git_blob_sha1(output)
        if actual != record["git_blob_sha1"]:
            output.unlink(missing_ok=True)
            raise ValueError(f"P9M3_UNIT_REPORT_GIT_BLOB_MISMATCH:{relative}:{actual}")
        receipts.append({
            **record,
            "source_repository": manifest["source_repository"],
            "source_commit": commit,
            "source_url": url,
            "local_path": str(output),
            "size_bytes": output.stat().st_size,
            "git_blob_sha1": actual,
            "sha256": sha256(output),
            "verification_status": "source_bytes_verified",
            "human_review_status": "not_reviewed",
            "configuration_authority": "test_report_not_approved_as_built_BOM",
        })
    report = {
        "schema_version": "birds4-flight-unit-evidence-receipts.v1",
        "source_commit": commit,
        "document_count": len(receipts),
        "receipts": receipts,
    }
    receipt_path = args.output_dir / "flight-unit-evidence-receipts.json"
    receipt_path.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({"document_count": len(receipts), "verified_count": len(receipts),
                      "human_review_status": "not_reviewed",
                      "receipt_path": str(receipt_path)}, ensure_ascii=False, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
