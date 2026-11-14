# P9-F real-data contract closure ledger

Baseline: PR #134, b95bebb495e088324bc21b0665e00d3902f41f9a. Main remains unchanged.

## Ordered acceptance

1. Native publication and session metadata: repaired and verified with actual Basilisk 2.11.0 session endpoints, without a runtime double.
2. Globe satellite/trail height binding: repaired; configured `alt` accessors now consume the live data. Frame guards additionally reject missing metadata, decreasing simulation time and stale pre-ready frames.
3. Full native browser journeys: still pending. A component test that supplies hand-written frames is not evidence for the end-to-end native chain.

## Reproduction and verification

- Red baseline run: https://github.com/clonetimer/SatAgentSimulation/actions/runs/43562210574
- Two genuine native API tests failed for missing published positions and missing continuous metadata.
- Five Node contract tests failed for the bounded default stream, height binding, time rollback, missing reference frame and pre-ready sequence rollback.
- Verified candidate run: https://github.com/clonetimer/SatAgentSimulation/actions/runs/43562624028
- Native publication/API regressions, the new Node contracts, existing P9-C scene lifecycle, coordinate conversion and existing Chromium component checks completed successfully.
- Evidence artifact: p9f-contract-closure-evidence, artifact ID 14100733472. Candidate manifest records every resulting blob and SHA-256.

## Implementation boundary

The isolated audit branch prepares exact-anchor candidates and stores verified Git blobs. It never moves a target branch. Only the reviewed source/test/workflow blobs are integrated into PR #134; the preparation script and audit-only workflow are excluded.

No physics model, TaskSpec execution path or main-branch pointer was replaced. These checks do not establish that two native UI-created sessions have driven the 3-D renderer; that remains the next acceptance gate.
