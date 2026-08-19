#!/usr/bin/env python3
"""
用途：在独立子进程中运行六项 active/internal 恢复能力，并执行 Adapter、Legacy、运行时身份和遥测质量验收。
参数：--output-root 指定证据目录；--timeout-s 指定单能力超时；--allow-missing-basilisk 仅允许生成构造态报告，不签发正式 PASS。
输出：生成逐能力 JSON/日志、汇总 JSON 和 JUnit；只有六项均通过才返回零退出码。
"""
from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import xml.etree.ElementTree as ET
from pathlib import Path
from typing import Any

PROJECT_ROOT = Path(__file__).resolve().parents[1]
SRC_ROOT = PROJECT_ROOT / "src"
if str(SRC_ROOT) not in sys.path:
    sys.path.insert(0, str(SRC_ROOT))

from sat_sim.internal_capability_acceptance import (
    SIX_ACTIVE_INTERNAL_CAPABILITIES,
    run_capability_acceptance,
    summarize_acceptance,
)


def _write_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def _write_junit(path: Path, summary: dict[str, Any]) -> None:
    records = summary.get("records", [])
    failures = sum(1 for item in records if item.get("status") not in {"PASS", "BLOCKED_RUNTIME_DEPENDENCY"})
    skipped = sum(1 for item in records if item.get("status") == "BLOCKED_RUNTIME_DEPENDENCY")
    suite = ET.Element(
        "testsuite",
        {
            "name": "six_active_internal_capability_acceptance",
            "tests": str(len(records)),
            "failures": str(failures),
            "errors": "0",
            "skipped": str(skipped),
        },
    )
    for item in records:
        case = ET.SubElement(suite, "testcase", {"classname": "sat_sim.internal_acceptance", "name": str(item.get("capability_id"))})
        status = item.get("status")
        if status == "BLOCKED_RUNTIME_DEPENDENCY":
            node = ET.SubElement(case, "skipped", {"message": "Basilisk runtime unavailable"})
            node.text = str(item.get("failure_reason"))
        elif status != "PASS":
            node = ET.SubElement(case, "failure", {"message": str(item.get("failure_reason") or status)})
            node.text = json.dumps(item.get("validation") or item.get("summary") or {}, ensure_ascii=False, indent=2)
    path.parent.mkdir(parents=True, exist_ok=True)
    ET.ElementTree(suite).write(path, encoding="utf-8", xml_declaration=True)


def _worker(capability_id: str, output_root: Path, result_path: Path) -> int:
    record = run_capability_acceptance(capability_id, output_root=output_root)
    _write_json(result_path, record)
    return 0 if record.get("passed") else 2


def _supervise(args: argparse.Namespace) -> int:
    root = args.output_root.resolve()
    root.mkdir(parents=True, exist_ok=True)
    records: list[dict[str, Any]] = []
    for capability_id in SIX_ACTIVE_INTERNAL_CAPABILITIES:
        safe = capability_id.replace(".", "_")
        record_path = root / "records" / f"{safe}.json"
        log_path = root / "logs" / f"{safe}.log"
        cmd = [
            sys.executable,
            str(Path(__file__).resolve()),
            "--worker",
            "--capability",
            capability_id,
            "--output-root",
            str(root / "runs" / safe),
            "--result-path",
            str(record_path),
        ]
        env = dict(os.environ)
        project_src = str(Path(__file__).resolve().parents[1] / "src")
        env["PYTHONPATH"] = project_src + (os.pathsep + env["PYTHONPATH"] if env.get("PYTHONPATH") else "")
        timed_out = False
        try:
            completed = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, timeout=args.timeout_s, env=env)
            worker_exit = completed.returncode
            worker_output = completed.stdout or ""
        except subprocess.TimeoutExpired as exc:
            timed_out = True
            worker_exit = 124
            captured = exc.stdout or ""
            if isinstance(captured, bytes):
                captured = captured.decode("utf-8", errors="replace")
            worker_output = str(captured) + "\nTIMEOUT\n"
        log_path.parent.mkdir(parents=True, exist_ok=True)
        log_path.write_text(worker_output, encoding="utf-8")
        if record_path.is_file() and not timed_out:
            record = json.loads(record_path.read_text(encoding="utf-8"))
        else:
            record = {
                "capability_id": capability_id,
                "status": "TIMEOUT" if timed_out else "ERROR",
                "passed": False,
                "failure_reason": f"worker_timeout={args.timeout_s}s" if timed_out else f"worker_exit={worker_exit}; evidence file missing",
            }
        record["worker_exit_code"] = worker_exit
        record["worker_timed_out"] = timed_out
        record["worker_log"] = str(log_path)
        records.append(record)
    summary = summarize_acceptance(records)
    _write_json(root / "six_capability_acceptance.json", summary)
    _write_junit(root / "six_capability_acceptance.junit.xml", summary)
    print(json.dumps({key: summary[key] for key in ("status", "passed", "capability_count", "pass_count", "status_counts")}, ensure_ascii=False, indent=2))
    if summary["passed"]:
        return 0
    blocked_only = all(item.get("status") in {"PASS", "BLOCKED_RUNTIME_DEPENDENCY"} for item in records)
    if args.allow_missing_basilisk and blocked_only:
        return 0
    return 2


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output-root", type=Path, default=Path("reports/final_internal_acceptance"))
    parser.add_argument("--timeout-s", type=float, default=300.0)
    parser.add_argument("--allow-missing-basilisk", action="store_true", help="construction mode only; blocked Basilisk cases are not counted as PASS")
    parser.add_argument("--worker", action="store_true", help=argparse.SUPPRESS)
    parser.add_argument("--capability", choices=SIX_ACTIVE_INTERNAL_CAPABILITIES, help=argparse.SUPPRESS)
    parser.add_argument("--result-path", type=Path, help=argparse.SUPPRESS)
    args = parser.parse_args()
    if args.worker:
        if not args.capability or args.result_path is None:
            parser.error("worker mode requires --capability and --result-path")
        return _worker(args.capability, args.output_root, args.result_path)
    return _supervise(args)


if __name__ == "__main__":
    raise SystemExit(main())
