# P0 Agent Input and Run Consistency Implementation Plan

> **For agentic workers:** Use superpowers:subagent-driven-development to implement and review this plan task-by-task. Steps use checkbox syntax for tracking.

**Goal:** Apply pending Agent instructions before execution, bind accepted generated configurations to successful parse receipts, and preserve explicitly requested optional parameters.

**Architecture:** Extend existing entry and validation boundaries without replacing the TaskSpec or execution stores. Browser state tracks the accepted parse source; a small server helper validates the persisted parse report before run creation. The refinement merge continues to use the registered parameter contract.

**Tech Stack:** Python, FastAPI, vanilla JavaScript, pytest, Chromium/Playwright.

**Spec:** docs/superpowers/specs/2026-11-05-agent-automation-p0-design.md

## Global Constraints

- Python >=3.11,<3.14; Windows-compatible filesystem operations.
- No new runtime dependency; preserve canonical JSON precision.
- No changes to native runners, physical thresholds, model weights, or historical scores.
- All new execution binding checks happen before task/run persistence.
- Existing manual/API run requests remain supported without a parse receipt.

## Review Focus

- Existing configuration plus pending input must submit the newly accepted configuration.
- Failed or stale model responses must not overwrite the accepted configuration or launch it implicitly.
- Empty Agent input must permit rerunning the accepted configuration without another model call.
- Manual edits invalidate an incompatible source receipt; invalid/missing/failed receipts cannot create task/run artifacts.
- Optional parameters absent from the base can be added only by an explicit registered request.

## Task 1: Pending instructions and accepted configuration

**Files:** Modify src/sat_sim/web/app.js; test tests/test_agent_pending_input_browser.py and a focused Node VM behavioral test with a pytest wrapper.

**Interfaces:** Reuse parseNaturalLanguage(), its single-owner promise, projectTaskSpecToForm(), and runSimulation(). Nonempty trimmed agentInput is pending because successful application clears it.

- [x] Write browser regressions for an existing 60-second configuration followed by a 120-second instruction and Run, failed/stale answers, blank rerun, and manual editing. Use real app/API projection and intercept only generation/execution boundaries.
- [x] Run the new tests; verify old source submits duration 60 or overwrites accepted state. Chromium launch is blocked locally by socket permissions; exercise the actual JavaScript functions in Node with mocked DOM/network boundaries for local RED/GREEN evidence and retain actual-browser tests for CI. Do not use source-string assertions or claim Node results are browser acceptance.
- [x] Make Run apply pending Agent text through the existing generation path; retain accepted state on failure and show pending-input status.
- [x] Run new tests plus existing ownership, responsiveness, and refinement browser tests.
- [x] Commit verified Task 1.

## Task 2: Successful parse receipt binding

**Files:** Create src/sat_sim/task_source.py; modify src/sat_sim/api.py, src/sat_sim/run_bundle.py and src/sat_sim/web/app.js; test tests/test_task_source_binding.py and browser/Node receipt assertions.

**Interfaces:** RunCreateBody.source_request_id: str | None. validate_task_source(artifact_root: Path, request_id: str, task_spec: Mapping[str, Any]) -> dict[str, str] verifies the persisted unified_agent_report.json. On absent/failed/corrupt/mismatched source, raise a typed validation error mapped to a clear 409 response before writes. Return request_id and task_spec_sha256 on success.

- [x] Write API tests for a genuine successful parse followed by prepare-only Run, changed values, failed/missing/corrupt report, malformed ID, and legacy run without source. Assert rejected requests create no task/run state.
- [x] Run and observe missing receipt support failures.
- [x] Implement receipt validation and source association in the existing Run Bundle input/provenance.json before execution/sealing; preserve exact TaskSpec and canonical transport. Add an optional prepare_run keyword for the already validated source ID. The existing provenance task_spec_sha256 binds the submitted task, and manifest/seal coverage protects the receipt.
- [x] Track successful request ID with the applied spec snapshot in UI; send only while current spec matches; test manual changes and stale responses.
- [x] Run API/canonical transport/task-center tests and relevant browser tests.
- [x] Commit verified Task 2.

## Task 3: Explicit optional parameter additions

**Files:** Modify src/sat_sim/unified_agent.py; test tests/test_explicit_optional_parameter_refinement.py.

**Interfaces:** Existing _merge_natural_language_refinement(base_task_spec, candidate_task_spec, instruction) and explicit parameter guard remain authoritative; candidate values are never synthesized from user text to hide a model omission.

- [x] Reproduce missing solar_efficiency from a native EPS base with explicit candidate value 0.3. Assert 0.3 and a changed path are retained, and unrelated candidate values do not replace the base.
- [x] Verify the explicit-addition test fails on current merge.
- [x] Iterate over supported explicitly requested candidate keys as well as existing base keys; retain existing final validation and legacy existing-key semantics. Capability identity repair stays authoritative; only new keys are bounded by the base registry.
- [x] Run the new tests and existing explicit parameter/event/refinement suites.
- [x] Commit verified Task 3.

## Completion

- [x] Run a relevant combined suite and attempt project-wide pytest, reporting concrete environment or unrelated baseline limitations. Combined suite: 390 passed; full pytest stopped during collection because Basilisk is absent.
- [x] Fresh independent whole-branch review: no Critical/Important/Minor findings; ready for a reviewable draft PR, with merge readiness conditional on current-head CI.
- [x] Record exact commands, counts, and limitations in docs/agent_automation_p0.md; add a dedicated actual-Chromium CI workflow.
- Publication target: `feat/agent-automation-p0-20261105` into `main`. The PR description and current-head checks record publication and remote acceptance status.

