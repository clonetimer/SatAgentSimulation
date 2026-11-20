# DT-2 / BE-26 — CROSS-LEO-001 independent two-body comparison

**Scope:** One initial, 5,400-second two-body propagation from a real upstream
Basilisk 2.11.0 execution, paired with a generated but **not yet executed**
GMAT R2026a input script. This is independent of PR #213's merge gate. It does
not use synthetic data as external-engine truth, nor qualify flight accuracy.

## Reused and newly declared inputs

- The date **2026-03-04T21:48:00Z**, semi-major axis **6,650,000 m**, circular
  equatorial orbit, **5 s** Basilisk task and **60 s** telemetry cadence come
  from the actual 5,400 s four-effect test on PR #213.
- Unlike #213's *SPICE Sun-aligned* initial true anomaly, CROSS-LEO-001 **newly
  declares f = 0 degrees** to make the two-body comparison fully reproducible
  without borrowing undocumented/orbit-specific SPICE output. It is **not**
  the same six-dimensional initial condition as the four-effect full-orbit run.
- Earth gravitational parameter and full initial r/v are obtained from the
  **actually running Basilisk** `gravBodyFactory.createEarth()` plus
  `orbitalMotion.elem2rv`, saved verbatim in `case_manifest.json` and used to
  generate GMAT's Cartesian initial state. No trajectory points are invented.
- Both force-model lists have **only Earth point mass enabled**. Drag,
  J2/J3/J4, SRP, third-body, tides, atmosphere/winds and magnetic force are
  OFF, even though the broader simulator can enable some of them. Both tools
  use a 5 s maximum step; Basilisk uses its native spacecraft integrator,
  whereas GMAT script requests RungeKutta89, accuracy `1e-12`. They are not
  asserted to be identical integrators.

## Already implemented (not evidence of an external GMAT run)

`python scripts/dt2_cross_leo_001.py basilisk --output reports/dt2-cross-leo-001 --checkout-sha <actual 40-hex git SHA>`

This path imports the actual Basilisk Python package, requires `bsk==2.11.0`,
instantiates the real `Spacecraft`/Earth point mass/recorder, executes 5,400 s,
then writes **91 observed full six-dimensional states** (including t=0 and
5,400) to `basilisk_state.csv`. The SHA manifest binds the resulting CSV,
script, input epoch, orbit elements, real `earth.mu`, actual initial state,
frame metadata, all force-model switches, package version, Python and Git SHA.
It also writes `gmat_two_body.script` with the same actual initial Cartesian
state converted to GMAT km and km/s. The script is only an input artifact;
`external_vv_status=NOT_RUN` and `gmat_executed=false` remain explicit.

The GitHub workflow `.github/workflows/dt2-cross-leo-001.yml` runs the
pure-Python fail-closed tests, **actually executes Basilisk** and uploads its
raw six-state CSV, manifest and GMAT script. A `SUCCESS` on that workflow
qualifies only the **Basilisk output half**.

## Independently execute GMAT (requires a real locally installed GMAT)

From an environment with NASA's real `GmatConsole` binary:

`python scripts/dt2_cross_leo_001.py gmat --output reports/dt2-cross-leo-001 --executable /path/to/GmatConsole`

On Windows use an absolute path to `GmatConsole.exe`. The script's CLI uses
GMAT's documented `--run <script>` operation. It records the actual binary
SHA-256, command, executed script SHA, version stdout, run stdout/stderr,
return code, raw ReportFile SHA and **only if real GMAT output exists** a
converted SI `gmat_state.csv`. Existing `gmat_raw.txt` refuses overwrite to
avoid a stale-success artifact. GMAT's native ReportFile is comma-separated,
headerless elapsed seconds, EarthMJ2000Eq km/km/s; the conversion requires all
91 finite records on the correct 60 s grid. GMAT syntax and runtime behavior
remain **unverified until this command actually succeeds**. Its release and
builtin Earth GM must also be independently verified; a script merely being
successfully generated is not GMAT qualification.

`python scripts/dt2_cross_leo_001.py compare --output reports/dt2-cross-leo-001`

This explicitly requires the GMAT execution provenance, raw GMAT file, both
normalized CSVs and matching SHA-256. It then computes the raw Earth-centered
3-D position/velocity residual and RTN position components at every matched
sample, and max/P95/P99/first/mid/last diagnostics. **It always outputs
`INCONCLUSIVE` until independent time/frame/GM alignment and a pre-approved
mission accuracy envelope are documented**; it cannot create fake GMAT data
or automatically label a numerical match `PASS`.

## Status and validation boundary

| Gate | Status until proven | Completion evidence |
| --- | --- | --- |
| Basilisk real 91-state two-body execution | **NOT_RUN before native CI** | SHA-bound `basilisk_state.csv`, run log and manifest |
| GMAT script generation | GENERATED only | Script SHA; no claim of execution |
| GMATConsole R2026a real execution | **NOT_RUN** | Executable hash/version, command, raw report/log |
| Independent coordinate/GM/time consistency | **NOT_VERIFIED** | versioned environment, EOP/time/frame mapping, GM constant |
| Numerical comparison and predeclared tolerances | **INCONCLUSIVE** | two real traces, frozen error budget, residual audit |
| Flight-grade or hardware qualification | **UNSUPPORTED** | Never inferred from this exercise |

No extra SPICE/GMAT asset download happens in the first two-body case.
Failure to install `bsk==2.11.0`, missing actual GMAT executable or
unaligned reference frames **cannot** be rebranded as PASS. Model comparison
comes *after* both independent tools have truly run.

The official NASA GMAT script conventions for Cartesian EarthMJ2000Eq state,
`ForceModel.PointMasses={Earth}`, `ReportFile`, `Propagate` and `GmatConsole
--run` were used as references, not as evidence that this generated script
has already executed:

- https://github.com/nasa/GMAT/blob/main/application/samples/Ex_LunarTransfer.script
- https://github.com/nasa/GMAT/discussions/9
- https://documentation.help/GMAT/CommandLine.html

## Genuine GMAT R2026a CI execution attempt (separate fail-closed gate)

The separate `.github/workflows/dt2-gmat-cross-leo-001.yml` is a **real, opt-in-engine validation attempt** on this Draft PR, not a flight accuracy release gate. It executes a fresh real bsk==2.11.0 two-body baseline, downloads the official Ubuntu GMAT R2026a binary from the NASA team's SourceForge release, records archive + actual executable SHA-256 and download URL, invokes actual `GmatConsole --run`, requires 91 authentic finite GMAT ReportFile rows, and only then computes an **INCONCLUSIVE** two-engine residual diagnostic. The external software is ~400 MB compressed, is not vendored into Git, and is never embedded in evidence artifacts. If download, initialization, script parser, or execution fails, the CI job fails and uploads available raw logs; it cannot fabricate a matching GMAT trajectory.

The independent GMAT ReportFile reference specifies enum `Delimiter = Comma`, **not** the earlier syntactically unverified `Delimiter = ','`. The generated script and parser test now use the official enumerated value. Passing the script-generator unit test alone is insufficient; the actual GmatConsole run must produce the real 91-row report. No claim of frame/μ equivalence or approved mission acceptance tolerances is made in this initial execution attempt.

Official release file: https://sourceforge.net/projects/gmat/files/GMAT/GMAT-R2026a/gmat-ubuntu-x64-R2026a.tar.gz/download
Official report-field reference: https://documentation.help/GMAT/ReportFile.html

