#!/usr/bin/env python3
"""CLI entry point for evidence-bounded CAD candidate mapping."""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "src"))

from sat_sim.cad_candidate_mapping import main


if __name__ == "__main__":
    raise SystemExit(main())
