#!/usr/bin/env python3
"""用途：从固定上游 commit 下载并校验 BIRDS-4 FM Assembly Procedure。
参数：--manifest、--output。
输出：精确匹配清单字节数和 Git blob SHA-1 的 DOCX 文件；不修改清单或源文件。
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


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--manifest", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    manifest = json.loads(args.manifest.read_text(encoding="utf-8"))
    record = manifest["assembly_procedure"]
    relative = record["path"]
    encoded_path = urllib.parse.quote(relative, safe="/")
    repository = record["repository"].removeprefix("https://github.com/")
    url = f"https://raw.githubusercontent.com/{repository}/{record['source_commit']}/{encoded_path}"
    args.output.parent.mkdir(parents=True, exist_ok=True)
    request = urllib.request.Request(url, headers={"User-Agent": "SatAgentSimulation-P9M3-evidence/1.0"})
    with urllib.request.urlopen(request, timeout=180) as response, args.output.open("wb") as output:
        while chunk := response.read(1024 * 1024):
            output.write(chunk)
    actual_size = args.output.stat().st_size
    actual_sha1 = git_blob_sha1(args.output)
    if actual_size != record["size_bytes"]:
        args.output.unlink(missing_ok=True)
        raise ValueError(f"P9M3_PROCEDURE_SIZE_MISMATCH:{actual_size}")
    if actual_sha1 != record["git_blob_sha1"]:
        args.output.unlink(missing_ok=True)
        raise ValueError(f"P9M3_PROCEDURE_GIT_BLOB_SHA1_MISMATCH:{actual_sha1}")
    print(json.dumps({
        "source_url": url,
        "source_commit": record["source_commit"],
        "size_bytes": actual_size,
        "git_blob_sha1": actual_sha1,
        "verification_status": "source_bytes_verified",
    }, ensure_ascii=False, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
