# P9-N1 CI lessons — 2026-11-17

## Scope

This note records the first integrated CI review of the guided Basilisk-native reaction-wheel workflow. It distinguishes physical-runtime evidence from browser-navigation and legacy-contract failures.

## Findings

1. **Physical execution passed independently.** The finite-duration official Basilisk run completed, and the native reaction-wheel state/torque/inertia A/B checks, TaskSpec-to-sealed-Run Bundle contract, and selectable native-template contract passed. A failing aggregate workflow must not be reported as a failed physics model when the physics job itself is green.
2. **Wizard option count is a brittle UI contract.** Adding the explicit `component_native` goal increased the welcome-step option count from seven to eight. Tests should assert the named capability/goal and only assert a total count when the exact inventory is intentionally part of the product contract.
3. **Guided wizard and full-workbench navigation are distinct surfaces.** The guided wizard owns the visible application surface; its global sidebar is not an available navigation control while the wizard is active. A browser journey that needs Run Center must explicitly enter the full workbench through the visible `专业建模` action before interacting with the sidebar. Do not use hidden elements or DOM-forced clicks to bypass the actual UI.
4. **Shared journey coverage must be triggered together.** A UI addition to the wizard can affect P9-L4 three-mode journeys, the single-run creator workflow, and native-component A/B workflow. Keep those suites in the same regression batch.
5. **Keep the evidence boundary explicit.** A green native physics job does not by itself prove the complete guided user journey, and a browser fixture does not prove native physics. Require both independent physical evidence and end-to-end browser evidence before calling P9-N1 accepted.

## Changes in this CI repair

- Update the wizard inventory assertion to explicitly include the single-component native entry.
- Correct the native reaction-wheel history journey to enter the full workbench through its visible header action before opening Run Center.
- Re-run the P9-L4, finite-run, native wheel A/B, browser, static, and native Basilisk regression workflows on the new PR head.

## Acceptance rule

Do not merge P9-N1 on partial green checks. Require all relevant jobs on the exact latest head to complete successfully, then review the sealed Run evidence and confirm the same backend provenance and physical charts remain available from history.
