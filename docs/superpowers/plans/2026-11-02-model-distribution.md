# Model Distribution Implementation Plan

> Execution: implement inline in this session; user approved the concrete distribution proposal with “执行”.

**Goal:** Other users can obtain the exact trained v5 model and run it without this machine's paths.

**Architecture:** A stdlib installer consumes a committed hash-pinned release lock, installs the original LoRA package plus exact ModelScope base files atomically, and invokes the existing model server. Do not modify satellite or inference implementation.

**Tech Stack:** Python stdlib, existing uv/SFT environment, GitHub Releases API, ModelScope file download.

**Spec:** ../specs/2026-11-02-model-distribution-design.md

## Global Constraints

- Preserve original model and all previous acceptance packets unchanged.
- No new training or score relabelling; CUDA remains required.
- No dependency additions for download/install; no remote Python or pickle execution.
- Do not overwrite releases, caches or unrelated processes; verify owned process identity before any service handover.
- Report validation scope honestly; do not rerun unrelated long endurance tests.

## Review Focus

- Corrupted/truncated downloads never become accepted installations.
- Archive paths, duplicates, symlinks and unexpected payload cannot escape or override targets.
- Existing altered cache fails explicitly instead of silently passing or overwriting.
- Relocated model retains exact adapter/base identity and existing serving gates.
- Public unauthenticated release download works; GPU environment reuse is disclosed.

## Task 1: Safe installer and launcher

Files: scripts/model_release.py; tests/test_model_release_distribution.py.

- [ ] Write behavioral tests over real files and local HTTP; watch missing feature fail.
- [ ] Implement install/check/serve and run the focused tests, serving/data contracts and governance checks.
- [ ] Record red/green and any rulings in reports/local_model_distribution/ledger.md.

## Task 2: Reproducible model package and release lock

Files: configs/models/satagent-v5-release.json; docs/model_distribution.md; README.md; START_HERE.md; docs/modelscope_local_training.md.

- [ ] Verify all original adapter/base hashes and prepare deterministic ZIP, model card and license.
- [ ] Produce committed lock with exact archive/payload/base identities and public URLs.
- [ ] Test real package install in a fresh checkout and actual CUDA load/generate/execute, preserving old service restoration details.

## Task 3: Cloud distribution and final verification

- [ ] Commit reviewed source/docs/lock and push; create a new non-overwriting GitHub release.
- [ ] Upload exact package and lock, verify server digest and unauthenticated download/install/check.
- [ ] Publish compact validation evidence and verify remote main, clean tree, and usable user commands.
