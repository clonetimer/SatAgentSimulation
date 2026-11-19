#!/usr/bin/env python3
"""用途：从固定 NASA URL 获取星敏 mock-up 原始图并记录 SHA-256。
参数：--output-dir 输出目录；只做来源采集，不执行背景移除或 TripoSR 推理。
"""
from sat_sim.p9m4i_source_intake import main


if __name__ == "__main__":
    raise SystemExit(main())
