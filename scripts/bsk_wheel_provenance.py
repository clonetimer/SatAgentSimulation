#!/usr/bin/env python3
"""Verify provenance and integrity of locally rebuilt Basilisk compatibility wheels.

用途：检验 bsk==2.11.0+satfix1 来自固定官方发行件，仅改变依赖元数据且所有 Wheel RECORD 哈希有效。
参数：--wheelhouse 指定目录，--platform 指定 linux_x86_64 或 windows_x86_64（默认检测宿主）。
输出：验证失败则抛出异常、拒绝安装；成功时输出仅包含已批准文件名和 SHA-256 的结构化结果。
"""
from __future__ import annotations

import base64
import csv
import hashlib
import io
import json
import platform
from pathlib import Path
import zipfile

from build_bsk_compat_wheels import (
    EXPECTED_ORIGINAL_SHA256, NEW_REQUIREMENT, OLD_REQUIREMENT,
    PATCHED_VERSION, ORIGINAL_VERSION, sha256,
)

REPORT = "bsk_2.11.0_satfix1_build_report.json"
WHEEL_PLATFORMS = {
    "windows_x86_64": "win_amd64",
    "linux_x86_64": "manylinux_2_27_x86_64",
}


def host_platform() -> str:
    osname, arch = platform.system().lower(), platform.machine().lower()
    if arch not in ("amd64", "x86_64"):
        raise RuntimeError("BSK_PLATFORM_NOT_APPROVED:" + osname + "/" + arch)
    if osname == "windows":
        return "windows_x86_64"
    if osname == "linux":
        return "linux_x86_64"
    raise RuntimeError("BSK_PLATFORM_NOT_APPROVED:" + osname + "/" + arch)


def _validate_zip_payload(wheel: Path) -> int:
    metadata = f"bsk-{PATCHED_VERSION}.dist-info/METADATA"
    record = f"bsk-{PATCHED_VERSION}.dist-info/RECORD"
    with zipfile.ZipFile(wheel) as z:
        names = z.namelist()
        if (not names or len(names) != len(set(names))
                or metadata not in names or record not in names):
            raise ValueError("BSK_WHEEL_MISSING_OR_DUPLICATE_METADATA")
        if any(
            n.startswith("/") or "\\" in n or ".." in Path(n).parts
            for n in names
        ):
            raise ValueError("BSK_WHEEL_UNSAFE_MEMBER_PATH")
        info = z.read(metadata).decode("utf-8")
        if (f"Name: bsk" not in info or
                f"Version: {PATCHED_VERSION}" not in info or
                NEW_REQUIREMENT not in info or OLD_REQUIREMENT in info):
            raise ValueError("BSK_WHEEL_METADATA_POLICY_MISMATCH")
        records = list(csv.reader(io.StringIO(z.read(record).decode("utf-8"))))
        by_name = {}
        for entry in records:
            if len(entry) != 3 or entry[0] in by_name:
                raise ValueError("BSK_WHEEL_RECORD_DUPLICATE_OR_INVALID")
            by_name[entry[0]] = (entry[1], entry[2])
        contents = set(names) - {n for n in names if n.endswith("/")}
        if set(by_name) != contents:
            raise ValueError("BSK_WHEEL_RECORD_FILES_MISMATCH")
        for name in contents:
            digest, size = by_name[name]
            if name == record:
                if digest or size:
                    raise ValueError("BSK_WHEEL_RECORD_SELF_HASH")
                continue
            actual_hash = hashlib.sha256()
            actual_size = 0
            with z.open(name) as handle:
                for chunk in iter(lambda: handle.read(1024 * 1024), b""):
                    actual_hash.update(chunk)
                    actual_size += len(chunk)
            expected_hash = base64.urlsafe_b64encode(actual_hash.digest()).rstrip(b"=").decode()
            if digest != "sha256=" + expected_hash or size != str(actual_size):
                raise ValueError("BSK_WHEEL_RECORD_CONTENT_CORRUPT:" + name)
        return len(contents) - 1


def verify_approved_wheel(wheelhouse: Path, *, platform_key: str | None = None) -> dict[str, object]:
    wheelhouse = Path(wheelhouse).resolve()
    key = platform_key or host_platform()
    if key not in WHEEL_PLATFORMS:
        raise ValueError("BSK_PLATFORM_NOT_APPROVED:" + key)
    report_file = wheelhouse / REPORT
    manifest = json.loads(report_file.read_text(encoding="utf-8"))
    if (manifest.get("schema_version") != "sat-sim.bsk-compat-wheel-build.v1"
            or manifest.get("status") != "PASS"
            or manifest.get("scope") != "distribution metadata only; Basilisk payload bytes unchanged"):
        raise ValueError("BSK_REPORT_PROVENANCE_INVALID")
    results = manifest.get("results")
    if not isinstance(results, list):
        raise ValueError("BSK_REPORT_RESULTS_INVALID")
    matches = []
    for entry in results:
        if not isinstance(entry, dict):
            raise ValueError("BSK_REPORT_ENTRY_INVALID")
        source, target = entry.get("source"), entry.get("target")
        if not isinstance(source, str) or source not in EXPECTED_ORIGINAL_SHA256:
            raise ValueError("BSK_UNKNOWN_OFFICIAL_SOURCE")
        if (not isinstance(target, str) or Path(target).name != target
                or target != source.replace(f"bsk-{ORIGINAL_VERSION}-",
                                           f"bsk-{PATCHED_VERSION}-", 1)):
            raise ValueError("BSK_REPORT_TARGET_UNAPPROVED")
        if (entry.get("source_sha256") != EXPECTED_ORIGINAL_SHA256[source]
                or entry.get("original_version") != ORIGINAL_VERSION
                or entry.get("patched_version") != PATCHED_VERSION
                or entry.get("metadata_change") != {
                    "from": OLD_REQUIREMENT, "to": NEW_REQUIREMENT,
                }
                or entry.get("payload_bytes_unchanged") is not True
                or type(entry.get("payload_file_count")) is not int
                or entry["payload_file_count"] < 1):
            raise ValueError("BSK_REPORT_METADATA_ONLY_ATTESTATION_MISSING")
        if WHEEL_PLATFORMS[key] in source:
            matches.append(entry)
    if len(matches) != 1:
        raise ValueError("BSK_PLATFORM_WHEEL_AMBIGUOUS_OR_MISSING")
    item = matches[0]
    wheel = wheelhouse / item["target"]
    if not wheel.is_file():
        raise FileNotFoundError("BSK_APPROVED_WHEEL_NOT_FOUND:" + str(wheel))
    digest = sha256(wheel)
    if digest != item.get("target_sha256"):
        raise ValueError("BSK_WHEEL_SHA256_MISMATCH")
    count = _validate_zip_payload(wheel)
    if count < item["payload_file_count"]:
        raise ValueError("BSK_WHEEL_UNEXPECTED_FILE_COUNT")
    return {
        "schema_version": "sat-sim.bsk-installed-wheel-attestation.v1",
        "status": "PASS", "wheel_path": str(wheel),
        "filename": wheel.name, "sha256": digest,
        "official_source": item["source"],
        "official_sha256": item["source_sha256"],
        "package_version": PATCHED_VERSION,
        "metadata_only_change": True, "record_verified": True,
        "wheel_members_verified": count,
        "native_restart_supported": False,
    }
