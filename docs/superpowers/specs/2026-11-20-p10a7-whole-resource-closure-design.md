# P10-A7 whole-spacecraft resource authority and evidence

Baseline: merged P10-A6, `f2b25e7f1229ac9a2b9f013c48a48d162a421766`.

## Goal

Audit every public whole-spacecraft configuration for electrical power authority,
remove redundant engineering power reconstruction, and publish consistent
construction-derived capability/runtime evidence. Preserve real Basilisk execution,
native dynamics and explicit engineering model assumptions.

## Verified baseline

Unified with propulsion off/on: zero legacy electrical-power proxies. Composite
Comm native/fallback crossed with network/reduced thermal has 0/1/1/2 such modules.
The two remaining classes are `DataActivityPowerBridge` and `HeaterPowerFeedback`.
The existing `proxy_coupling_count` counts links and is not a module inventory.
Both default thermal networks contain two project physical heater authorities;
these are not upstream official modules and must be listed honestly.

The complete registry has13 whole-spacecraft contracts:9 active,4 deprecated,
6 active and Agent-visible. Two other active products intentionally retain local
electrical equations (`power_thermal_orbit_coupled`, `comm_payload_mission_coupled`).
Confirm and disclose these scoped models rather than silently retire them without
physical feature parity. Four deprecated electrical capabilities remain direct-API
compatibility paths; ordinary registered product execution excludes them. Other
orbit/attitude or prescribed-heat capabilities are not electrical-provider models.
Do not mix a count of capability paths with a count of instantiated providers.

Native probes additionally establish: reduced heater scale0.25 produces3W heat
but the feedback proxy draws12W; PDU-off does not stop reduced heater actuation;
unconfigured reduced nodes inherit18W heater actuators; one10W electrical input is
deposited in each of three nodes and is incorrectly suppressed in eclipse. The
default composite thermal network adds43W standalone internal heat on top of EPS
messages. A short16J propulsion pulse can be missed by the10s thermal task. The
composite battery report misreads a written zero-capacity t=0 startup payload as
actual zero stored energy and produces a false518400J balance residual.

## Decisions

1. Replace compatibility Comm's uncalibrated rate-to-watts curve with a declared
   fixed state profile:32W active,2W permitted idle,0W PDU-off. A status-only adapter
   reads actual finite nonpositive transmitter baud; official SimplePowerSink
   nodes own watts. Fractional negative baud still means the active profile.
2. Let each configured reduced thermal heater owner publish actual electrical
   draw from the same thermostat/forced-state/PDU/scale used for heat. Keep its
   existing unity electrical-to-heat efficiency; no invented hardware efficiency.
   Remove the nominal-status reconstruction bridge and phantom heater hardware.
3. Route internal electrical heat once to its destination; eclipse affects only
   external solar heating. Remove standalone internal-heat placeholders only in
   the coupled whole-spacecraft configuration. Preserve standalone component demos.
4. Accumulate externally supplied power at dynamics cadence, and deliver interval
   average heat to coarse thermal steps. Retain pending energy at a partial final
   thermal interval explicitly. Do not fabricate a final off-grid thermal update.
5. Preserve raw battery recorders. Only the exact t=0 zero payload is NOT_READY;
   later invalid/missing/stale native data fails evidence validation. Use dense
   native battery evidence for SOC health and an explicitly reported contiguous
   valid observed conservation interval. True empty batteries and native clamping
   must still fail the existing operational/conservation conditions.
6. Publish a separate electrical-authority inventory with actual classes, ports,
   connection roles, assumptions and evidence. Count official providers, project
   physical providers and engineering proxies separately. Status adapters and heat
   conversion are not electrical power authorities. Keep legacy link counts named
   as such; never relabel a watts equation to obtain zero proxies.

## Acceptance and scope

Official Basilisk2.11.0 matrix tests cover all four composite configurations,
unified propulsion off/on, real parameter entry, native electrical A/B invariants,
PDU and fault scaling, correct heat routing and eclipse independence, pulse energy
across thermal/export cadences, invalid native evidence, real depletion, and
source-derived inventory. Existing resource trust, event/Run Bundle, native long
regression and relevant browser gates run on the accepted PR head. Project heater
physics, fixed auxiliary profiles and heat fractions remain engineering models;
this work is not flight calibration or a full-platform Stable certification.

The alternate constant solar configuration must supply the same real EPS PDU
contract, or be rejected specifically at validation rather than fail inside a
newer status adapter. Deprecated capability direct APIs remain separately disclosed.
