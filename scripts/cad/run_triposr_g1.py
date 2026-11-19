#!/usr/bin/env python3
"""Run a locally pinned TripoSR checkout for an isolated G1 preview candidate.

The input manifest must prove source-image reuse permission and pin the model,
license blob and local weights. The output is not registered or physically scaled.
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from sat_sim.triposr_g1 import (
    probe_cuda,
    run_triposr_g1,
    validate_image_to_3d_manifest,
)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--manifest", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument(
        "--preflight-only",
        action="store_true",
        help="Validate source permission, pinned model/weights and available CUDA memory without inference.",
    )
    args = parser.parse_args()
    try:
        if args.preflight_only:
            validated = validate_image_to_3d_manifest(args.manifest)
            gpu = probe_cuda(validated["minimum_free_vram_gb"])
            result = {
                "preflight": "PASS",
                "asset_id": validated["record"]["asset_id"],
                "source_sha256": validated["record"]["source_image"]["sha256"],
                "model_revision": validated["record"]["model"]["revision"],
                "gpu": gpu,
                "inference_started": False,
            }
        else:
            receipt = run_triposr_g1(args.manifest, args.output_dir)
            result = {
                "preflight": "PASS",
                "asset_id": receipt["asset_id"],
                "glb_sha256": receipt["glb_audit"]["sha256"],
                "scale_status": receipt["scale_status"],
                "registration_status": receipt["registration_status"],
                "as_built_eligible": False,
            }
        print(json.dumps(result, ensure_ascii=False, sort_keys=True))
        return 0
    except Exception as exc:
        print(f"TripoSR G1 worker rejected: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
