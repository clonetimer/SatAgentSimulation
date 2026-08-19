#!/usr/bin/env python3
"""
用途：检查正式仿真所需 Basilisk 发行包、导入根和受支持版本。
参数：无命令行参数。
输出：向标准输出写入机器可读 JSON；运行时不可用时返回退出码 2。
"""
from __future__ import annotations

import json

from sat_sim.simulation_fidelity import inspect_basilisk_runtime


def main() -> int:
    status = inspect_basilisk_runtime()
    print(json.dumps(status.to_dict(), ensure_ascii=False, indent=2))
    return 0 if status.ready else 2


if __name__ == "__main__":
    raise SystemExit(main())
