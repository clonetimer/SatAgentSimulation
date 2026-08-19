#!/usr/bin/env python3
"""Build the audited Basilisk 2.11.0 Pillow-compatibility wheels.

用途：对官方 bsk 2.11.0 Wheel 只修改发行元数据，将 Pillow 上限从
      ``<=12.2.0`` 放宽为 ``<13``，并将本地版本标记为
      ``2.11.0+satfix1``；不修改任何 Basilisk Python、共享库或数据文件。
参数：一个或多个官方 bsk 2.11.0 Wheel 路径，以及可选 ``--output-dir``。
输出：重算 RECORD 后的 ``bsk-2.11.0+satfix1-*.whl`` 和 JSON 构建报告。
"""
from __future__ import annotations

import argparse
import base64
import csv
import hashlib
import io
import json
from pathlib import Path, PurePosixPath
import zipfile

ORIGINAL_VERSION = "2.11.0"
PATCHED_VERSION = "2.11.0+satfix1"
OLD_REQUIREMENT = "Requires-Dist: pillow<=12.2.0,>=10.4.0"
NEW_REQUIREMENT = "Requires-Dist: pillow<13,>=10.4.0"
EXPECTED_ORIGINAL_SHA256 = {
    "bsk-2.11.0-cp39-abi3-manylinux_2_27_x86_64.manylinux_2_28_x86_64.whl":
        "d51fe1ffa1b0b03dc415034b210497b28a2504428d4d8e71b481542a08c08a0a",
    "bsk-2.11.0-cp39-abi3-win_amd64.whl":
        "5558268290474206a1a600c6bbf1847ed02e2c99c10a062e1d98a38076f58eb0",
}


def sha256(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            h.update(block)
    return h.hexdigest()


def record_digest(data: bytes) -> str:
    value = base64.urlsafe_b64encode(hashlib.sha256(data).digest()).rstrip(b"=").decode("ascii")
    return f"sha256={value}"


def renamed_path(name: str) -> str:
    prefix = f"bsk-{ORIGINAL_VERSION}.dist-info/"
    if name.startswith(prefix):
        return f"bsk-{PATCHED_VERSION}.dist-info/{name[len(prefix):]}"
    return name


def clone_info(info: zipfile.ZipInfo, filename: str) -> zipfile.ZipInfo:
    copied = zipfile.ZipInfo(filename=filename, date_time=info.date_time)
    copied.compress_type = info.compress_type
    copied.comment = info.comment
    copied.extra = info.extra
    copied.create_system = info.create_system
    copied.create_version = info.create_version
    copied.extract_version = info.extract_version
    copied.flag_bits = info.flag_bits
    copied.volume = info.volume
    copied.internal_attr = info.internal_attr
    copied.external_attr = info.external_attr
    return copied


def build_one(source: Path, output_dir: Path) -> dict[str, object]:
    source = source.resolve()
    expected = EXPECTED_ORIGINAL_SHA256.get(source.name)
    actual = sha256(source)
    if expected is None:
        raise ValueError(f"未批准的官方 Wheel 文件名：{source.name}")
    if actual != expected:
        raise ValueError(f"官方 Wheel SHA-256 不匹配：{source.name}: {actual}")

    output_dir.mkdir(parents=True, exist_ok=True)
    target_name = source.name.replace(f"bsk-{ORIGINAL_VERSION}-", f"bsk-{PATCHED_VERSION}-", 1)
    target = output_dir / target_name
    members: dict[str, tuple[zipfile.ZipInfo, bytes]] = {}

    with zipfile.ZipFile(source, "r") as src:
        metadata_seen = False
        payload_file_hashes: dict[str, str] = {}
        for info in src.infolist():
            if info.is_dir():
                continue
            original_name = info.filename
            if original_name.endswith(".dist-info/RECORD"):
                continue
            data = src.read(original_name)
            output_name = renamed_path(original_name)
            if original_name.endswith(".dist-info/METADATA"):
                text = data.decode("utf-8")
                if f"Version: {ORIGINAL_VERSION}" not in text:
                    raise ValueError("METADATA 中未找到预期版本")
                if OLD_REQUIREMENT not in text:
                    raise ValueError("METADATA 中未找到预期 Pillow 约束")
                text = text.replace(f"Version: {ORIGINAL_VERSION}", f"Version: {PATCHED_VERSION}", 1)
                text = text.replace(OLD_REQUIREMENT, NEW_REQUIREMENT, 1)
                data = text.encode("utf-8")
                metadata_seen = True
            members[output_name] = (clone_info(info, output_name), data)
            if ".dist-info/" not in output_name:
                payload_file_hashes[output_name] = hashlib.sha256(data).hexdigest()
        if not metadata_seen:
            raise ValueError("未修改 METADATA")

    record_name = f"bsk-{PATCHED_VERSION}.dist-info/RECORD"
    record_buffer = io.StringIO(newline="")
    writer = csv.writer(record_buffer, lineterminator="\n")
    for name in sorted(members):
        data = members[name][1]
        writer.writerow([name, record_digest(data), str(len(data))])
    writer.writerow([record_name, "", ""])
    record_data = record_buffer.getvalue().encode("utf-8")
    record_info = zipfile.ZipInfo(record_name)
    record_info.compress_type = zipfile.ZIP_DEFLATED
    record_info.external_attr = 0o100644 << 16
    members[record_name] = (record_info, record_data)

    with zipfile.ZipFile(target, "w", allowZip64=True) as dst:
        for name in sorted(members):
            info, data = members[name]
            dst.writestr(info, data)

    # Re-open and prove payload bytes are unchanged from the official Wheel.
    with zipfile.ZipFile(target, "r") as patched:
        patched_payload_hashes = {
            n: hashlib.sha256(patched.read(n)).hexdigest()
            for n in patched.namelist()
            if not n.endswith("/") and ".dist-info/" not in n
        }
    if patched_payload_hashes != payload_file_hashes:
        raise RuntimeError("重打包后 Basilisk 载荷文件发生变化")

    return {
        "source": source.name,
        "source_sha256": actual,
        "target": target.name,
        "target_sha256": sha256(target),
        "original_version": ORIGINAL_VERSION,
        "patched_version": PATCHED_VERSION,
        "metadata_change": {"from": OLD_REQUIREMENT, "to": NEW_REQUIREMENT},
        "payload_file_count": len(payload_file_hashes),
        "payload_bytes_unchanged": True,
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("wheels", nargs="+", type=Path)
    parser.add_argument("--output-dir", type=Path, default=Path("third_party/wheels"))
    parser.add_argument("--report", type=Path)
    args = parser.parse_args()
    results = [build_one(path, args.output_dir) for path in args.wheels]
    report = {
        "schema_version": "sat-sim.bsk-compat-wheel-build.v1",
        "status": "PASS",
        "results": results,
        "scope": "distribution metadata only; Basilisk payload bytes unchanged",
    }
    report_path = args.report or args.output_dir / "bsk_2.11.0_satfix1_build_report.json"
    report_path.parent.mkdir(parents=True, exist_ok=True)
    report_path.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
