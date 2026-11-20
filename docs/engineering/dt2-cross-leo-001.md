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

### GMAT exact executable discovery and input encoding

- Native [CI 44817225430](https://github.com/clonetimer/SatAgentSimulation/actions/runs/44817225430) fetched the authentic SourceForge Ubuntu archive (**401,664,992 bytes**, SHA-256 `fe124b4a606b2e3b704a6fbb1c37b87598d5df0d18cb661c304b5f60074a7754`), but its launcher is **`GmatConsole-R2026a`** rather than unsuffixed `GmatConsole`. This discovery is based on the actual extracted archive, not an assumed naming convention.
- Follow-up real [CI 44817699249](https://github.com/clonetimer/SatAgentSimulation/actions/runs/44817699249) found, launched and version-checked `GMAT/R2026a/bin/GmatConsole-R2026a` (binary SHA-256 `f7cf5b3188902353ed4245bb21c22c1afa0451efdd221b7e19f6a74dc7ed1d93`). The real GMAT Console engine initialized successfully but rejected the candidate script because a comment contained Unicode `—`: **GMAT R2026a only accepts ASCII source scripts**. The engine emitted `contains characters outside of the ASCII character set`; no `gmat_raw.txt` was written.
- The `_gmat_script` generator now uses ASCII-only source comments and explicitly fails before any GMAT run if future generated text is not ASCII. Unit test asserts `script.isascii()`. This changes no orbital initial state, `earth.mu`, numeric integrator selection, sample cadence or flight-model assumptions.
- The above is a genuine failed first GMAT script execution, **not** a completed external comparison. The next exact-head native GMAT workflow must actually produce 91 authentic GMAT rows before the result can advance beyond `NOT_RUN`/execution-blocked.

### Real GMAT execution exposed two more input-contract defects (2026-11-21)

[CI 44818023876](https://github.com/clonetimer/SatAgentSimulation/actions/runs/44818023876) **actually invoked** NASA R2026a `GmatConsole-R2026a` and stdout confirms "Successfully interpreted the script", "Mission run completed", and real **0.665-second** console runtime. It wrote a genuine `gmat_raw.txt`, 91 lines. The parser correctly rejected this as invalid: **it was not a 5400 s comma-separated trajectory**.

Measured from authentic `gmat_raw.txt` (artifact **#14670805290**):
1. The generated `ReportFile.Delimiter = Comma` (an earlier documentation-driven assumption) produced the literal character **`C`** between all 7 fields, e.g. `0C6650C0C0C0C7.742...C0`. The only reliable supported form for this release is `Delimiter = ','`; this restores the original quoted comma character with an explicit regression assertion.
2. Setting `CrossSat.ElapsedSecs = 60,120,180,...5400` in consecutive `Propagate` commands **does not mean absolute elapsed time**: the real report had approximately `0,60,180,360,600,900,...,245700` seconds. A GMAT propagation stop condition is relative to each Propagate start, so the script now repeats the **60-second** duration exactly 90 times. The new unit regression asserts 90 repeated `= 60` commands, no `= 120` / `= 5400` cumulative request, and unchanged 91 Report commands.

The changed script is still purely ASCII, 5,400 s requested, point-mass-only; the *actual executable version, archive and binary SHA* are the same verified R2026a inputs. No fabricated trajectory or post-hoc tolerance adjustment. A fresh current-head CI must produce 91 true 60-second-spaced GMAT states and an **INCONCLUSIVE** independent diagnostic before the case can move beyond GMAT execution-blocked.

### Bounded **input-time representation** pilot (not physics acceptance tolerance)

[Real GMAT CI 44818433025](https://github.com/clonetimer/SatAgentSimulation/actions/runs/44818433025) produced 91 genuine R2026a rows with the intended comma format and 0–5400 s cadence. Its last *original* `ElapsedSecs` was **5399.999989313073 s**, i.e. **-10.686927 microseconds** relative to the nominal 5400-second tick, because the real GMAT Console reports elapsed time after calendar/time-scale round trips. Original 10-microsecond exact-grid parser limit correctly failed at index 85. This is **not** a flight dynamics error.

For the **next run only**, a separate and explicit **20 microsecond GMAT input-representation limit** is frozen by test and source constant. Every *raw GMAT timestamp remains unmodified* and is checked for finite values, monotonic cadence, expected 91 rows, and max 20 microsecond skew. The stricter existing Basilisk exact-grid guard is **unchanged**, and a synthetic negative control at 1 millisecond skew must still FAIL. The real GMAT raw and normalized reports retain timestamps; diagnostic differences include both original engine clocks and `clock_skew_s`, plus the maximum velocity-equivalent positional clock offset. No interpolation/extrapolation or invented samples are introduced.

**This 20 microsecond figure is only a representation/readability bound, selected after an explicit real pilot and predeclared for the upcoming new run; it is NOT a position/velocity error budget, physical tolerance relaxation, independent clock/frame approval, or a retroactive PASS on the pilot.** Even if the next run successfully produces two authentic 91-state datasets, `comparison_diagnostic.json` will stay **INCONCLUSIVE** with `paired_samples=91`, `aligned_samples=0` until frame/GM/time alignment and independent mission tolerances are approved.

The evolving manifest will mark GMAT executed only after the actual 91-row raw report is verified; absent/invalid GMAT outputs keep `NOT_RUN`/failed execution status and may not be promoted to comparison acceptance.

## Earth-Mu matched genuine GMAT control (independent second execution)

The first genuine GMAT-versus-Basilisk data on exact #215 head 6cad46b1 provides **91 real state pairs** and a **1.151936372 m final position difference**. An independent high-precision pure-central-gravity integration from the *recorded, true shared initial state* predicts **1.153071928 m** final relative position if the only model difference is **Basilisk's measured Earth mu 398600.436 km^3/s^2** versus the GMAT documented default **398600.4415 km^3/s^2**. This leaves **~1.14 mm** in the *vector residual after removing the theoretical constant-induced difference*; it is not a physically approved correction or a synthetic substitute for running GMAT.

The exact constants and method are grounded in GMAT's CelestialBody and Propagator documentation (point masses use `Earth.Mu`), and the mature open-source Stanford-NavLab **LuPNT/GMAT cross-validation** explicitly uses `GMAT Earth.Mu = ...` to ensure constant alignment. References:
- https://documentation.help/GMAT/CelestialBody.html
- https://documentation.help/GMAT/Propagator.html
- https://github.com/Stanford-NavLab/LuPNT/blob/main/cpp/test/gmat/gen_gmat_reference.py

The new `gmat-mu-control` CLI will actually invoke **the same verified R2026a executable a second time** with the same original measured Cartesian initial state and 90x60s propagation commands; the *only intentional force-model change* is an **explicit `GMAT Earth.Mu` override** from the actual Basilisk `case_manifest.json` mu, before force model initialization. The first default-GMAT original raw CSV and all SHA hashes remain untouched. The second real GMAT output is recorded separately as `gmat_mu_control_raw.txt`, `gmat_mu_control_state.csv`, full stdout/stderr, binary/script/report hashes and an auditable execution JSON.

The `compare-mu-control` path **requires both genuine GMAT executions and the Basilisk source evidence**, checks exact file SHA identities and two independent 91-point grids, then produces an `INCONCLUSIVE` diagnostic and side-by-side raw residual CSV. Even if observed residuals become millimeter-scale, no PASS threshold is inferred from the result, **0 samples are marked independently frame/time aligned**, and no UTC/TT frame certification or flight grade correlation is asserted. The separate time-readability bound stays 20 microseconds; raw elapsed values are never modified. This is a limited one-variable sensitivity control, not a new physical model or a general-purpose validation framework.


## Independent exact circular two-body reference (analytic, **not** flight truth)

After two genuinely executed GMAT runs and the actual Basilisk source trace, the
new command `python scripts/dt2_cross_leo_001.py analytic-reference --output
reports/dt2-cross-leo-001` checks the original manifest, exact file SHA
identities, two real GMAT run receipts and a circular Earth point-mass state
before creating a **mathematical closed-form comparison**. It never generates
GMAT data or substitutes an analytic solution as externally measured truth.

For this circular-equal-plane case, with the *actual* initial position r0,
velocity v0 and Earth mu from Basilisk, the unique exact two-body solution is

- n = sqrt(mu / |r0|^3)
- r(t) = r0*cos(n*t) + (v0/n)*sin(n*t)
- v(t) = v0*cos(n*t) - n*r0*sin(n*t)

The code strictly rejects non-circular speeds, non-tangential initial states,
nonfinite mu/epoch inputs and unverified engine data. It evaluates **both**
(1) the nominal 60-second state index and (2) the unmodified original GMAT
reported `ElapsedSecs` time. It saves all 91 rows in
`analytic_reference_difference_rows.csv` and explicit provenance,
sample pairing and status in `analytic_reference_diagnostic.json`.

An independent local computation of the previously published real #215 artifact
(#14671573546) yielded **maximum position differences** relative to this
mathematical reference (all model mu set to the *Basilisk* value):

| Real recorded state vs circular closed-form | Nominal sample ticks | Using each engine's reported ElapsedSecs |
|---|---:|---:|
| Basilisk native RK4-like 5s propagation | 0.001159607 m | 0.001159607 m |
| GMAT R2026a default Earth mu | 1.153072129 m | 1.235811254 m |
| GMAT R2026a explicitly matched Earth mu | 0.0000003346 m | 0.082739452 m |

This strongly suggests the original GMAT `ElapsedSecs` 10.69 microsecond
skew is a *reported-time representation* effect for this case: its propagated
six-state agrees with the nominal 60-second exact solution at sub-micrometer
level, while treating the raw float column as exact physical epoch introduces
~8.27 cm apparent positional discrepancy. This does **not** authorize changing
or overwriting raw time values, certifying UTC/TT offsets, or automatically
assuming an absolute EarthMJ2000Eq↔Basilisk N rotation.

Scientific guard: GMAT documents `EarthMJ2000Eq` as an MJ2000Eq
IAU-1976/FK5 inertial axis system, different in definition from `EarthICRF`.
Our isolated point-mass baseline intentionally injects the SAME numeric
Cartesian initial state into both tools; that is a case-defined abstract
common frame, **not** an independently validated physical inertial frame tie
for real spacecraft data. Official frame description:
https://documentation.help/GMAT/CoordinateSystem.html .

The diagnostic remains `INCONCLUSIVE` and records
`approved_frame_time_aligned_samples=0`, `mission_residual_acceptance_limits_approved=false`;
no post-hoc residual acceptance limit is introduced. This check is an
independent mathematical interpretation of already authentic outputs, not
qualification of flight accuracy or replacement of third-party data.


## Real GMAT two-frame and numerical timescale experiment (not yet qualified)

A new (third) genuine GMAT R2026a console invocation reuses the authenticated
Basilisk six-state and both previously executed GMAT runs, with SHA-bound
inputs, identical Earth-Mu override and a wholly separate output path.
Original Basilisk, default-GMAT and matched-Mu-GMAT original trajectories
and hashes remain untouched. Its explicit additional GMAT ReportFile
records 91 simultaneous numerical observations:

- ElapsedSecs, UTCModJulian, TAIModJulian, TTModJulian and A1ModJulian.
- The same GMAT spacecraft state expressed in EarthMJ2000Eq and EarthICRF
  Cartesian position and velocity (six components per frame).
- The actual R2026a script, raw report, stdout/stderr, executable SHA,
  optionally the local packaged tai-utc.dat hash, and a diagnostic file
  whose scientific status remains INCONCLUSIVE.

Time conventions are **not interchangeable**: GMAT ModJulian = JD-2430000.0,
while conventional MJD = JD-2400000.5. For the frozen 2026-03-04T21:48 UTC
to 23:18 UTC interval, official NIST/USNO resources state TAI-UTC=37 seconds
and TT-TAI=32.184 seconds; no leap second occurs during this short arc.
A **predeclared 1 millisecond bound is ONLY a numerical report sanity check**,
not mission clock accuracy qualification. The original GMAT ElapsedSecs
10.69-microsecond raw skew is preserved; no re-timing or resampling.

The observed two-frame difference comes directly from GMAT, not an assumed
identity. EarthMJ2000Eq (IAU-1976/FK5) and EarthICRF (IAU-2000/2006) are
different realizations according to official GMAT documentation; the
actual Basilisk two-body-only N frame was numerically initialized in a
case-defined Cartesian basis but never tied to an independently measured
celestial reference vector. Thus a successful GMAT internal frame probe
**does not** qualify an absolute physical Basilisk N-to-ICRF rotation.

The output must contain 91 verified finite rows, the correct GMAT JD
offset, consistent UTC/TAI/TT numerical epoch relation, the same
EarthMJ2000Eq state in the two GMAT ReportFiles, and verified source SHAs.
Any deviation fails closed and preserves original console output. After
success the diagnostic must still contain zero independently certified
Basilisk-to-GMAT frame-aligned samples, false absolute time/axis approval
and false mission accuracy qualification. Do not fit an Earth rotation
from a 2-body-only test or retroactively invent a physical tolerance.

Documentation/provenance:
- https://documentation.help/GMAT/CoordinateSystem.html
- https://documentation.help/GMAT/CalculationParameters.html
- https://documentation.help/GMAT/SpacecraftEpoch.html
- https://www.nist.gov/pml/time-and-frequency-division/time-realization/leap-seconds
- https://maia.usno.navy.mil/information/eo-values

The scientific goal remains independent real-world frame tie and
advance-approved mission error budget; this small experiment exposes
actual GMAT frame conversion and epoch-reporting behavior without
changing any existing physics or overriding the INCONCLUSIVE verdict.


## Shared-kernel external celestial direction check — first implementation candidate

This test addresses a real gap: injecting identical *numeric* Cartesian states
in Earth point-mass models does NOT establish a physical celestial orientation.
NASA NAIF documents that in SPICE, frame J2000 uses ICRF axes; GMAT separately
defines its EarthICRF and EarthMJ2000Eq frames. Both models must query an
ephemeris direction at a specified physical epoch rather than inventing a
rotation by fitting the two-body state histories.

The existing real GMAT R2026a Linux archive contains
data/planetary_ephem/spk/DE421AllPlanets.bsp and the SPICE leap-second and
planetary-constants kernels. The new scripts/dt2_sun_anchor.py runs only after
all three existing genuine Basilisk/GMAT runs with verified SHA-256 receipts:

1. Read exactly the start, middle and end A1 epoch values from the REAL
   91-row GMAT time report, without assuming A1=UTC or deriving a new clock.
2. Run a new genuine GMAT Console mission reporting the geocentric Sun
   six-state at those A1 instants in EarthICRF and EarthMJ2000Eq, with
   SolarSystem.EphemerisSource set to SPICE and actual absolute kernel paths.
   GMAT's documented SpacePoint coordinate-dependent parameters permit
   reporting Sun.EarthICRF.X etc. The console's actual original ReportFile,
   return code, stdout/stderr and executable/SolarSystem kernel fingerprints
   remain in the artifact.
3. Execute the *actual* Basilisk 2.11.0 SpiceInterface with explicit
   referenceBase=J2000, zeroBase=earth and the SAME pinned GMAT release SPK,
   LSK and PCK, saving its actual Sun planet-state messages at 0/2700/5400 s.
4. Query the SAME underlying SPK with a separate NAIF SpiceyPy 8.4.0 wrapper
   for Earth-relative Sun in J2000 at the actual UTC instants.
5. Record the genuine three-way vectors and independent angular/distance
   differences, including GMAT EarthICRF vs GMAT EarthMJ2000Eq. All sources
   must be finite, physically plausible and SHA-bound. No artificial Sun
   vectors or "reference" trajectories are generated to satisfy tests.

**Important comparison boundary:** All three evaluations share the *same*
DE421 planetary ephemeris; this is a controlled cross-tool axis-implementation
check, NOT three statistically independent astrophysical truth sets. The
R2026a bundled DE421 differs from the DE430 assets in DT-1 #213. Therefore
this cannot replace the original spacecraft's source-orbit/frame acceptance
nor establish that its *purely numerical* point-mass N initial axes were
physically attached to the ICRF. Any observed vector discrepancy is reported
and investigated, rather than silently correcting, fitting or post-hoc
relaxing a numerical tolerance.

The CI will indicate SUCCESS only for execution, provenance and validity of
three real data sources; the diagnostic status will remain INCONCLUSIVE, with
independent physical N frame tie and mission accuracy envelope explicitly
UNAPPROVED until a separate frame/mission approval is demonstrated.

Official sources:
- https://naif.jpl.nasa.gov/pub/naif/self_training/individual_docs/16_frames_and_coordinate_systems.pdf
- https://documentation.help/GMAT/CalculationParameters.html
- https://documentation.help/GMAT/CoordinateSystem.html
- https://documentation.help/GMAT/SolarSystem.html

No change to the original 5400-second three-engine raw state data, high-order
gravity, atmosphere, radiation force, instrument defaults or approval gates.


### R2026a authentic three-engine Sun anchor startup-kernel gate

The actual R2026a run [44826078436](https://github.com/clonetimer/SatAgentSimulation/actions/runs/44826078436), preserved in failure artifact **14675775837**, completed all genuine Basilisk/GMAT two-body and Earth-mu controls, but **FAILed** the additional Sun anchor at the GMAT script parser. Exact executable stdout rejected `SolarSystem.LSKFilename` as **deprecated**, requiring the LSK filename to be supplied by the GMAT startup file. No genuine Sun anchor ReportFile existed on that head, and the three-engine vector proof is NOT complete. The residual diagnostics preceding this step remain **INCONCLUSIVE**, not a new physical PASS.

For R2026a, the [official tagged GMAT startup config](https://github.com/nasa/GMAT/blob/R2026a/application/bin/gmat_startup_file.txt) binds `PLANETARY_SPK_FILE`, `LSK_FILE` and `PLANETARY_PCK_FILE` (each to the intended packaged DE421/LSK/PCK). The replacement contract verifies the real installed startup's effective last active assignments resolve to these exact three packaged kernel paths, emits startup-file SHA-256, and **fails closed** for missing or mismatched paths. The generated Sun script selects SPICE but no longer attempts deprecated SolarSystem kernel-filename assignments. Test-only fixtures cover all three mismatch negatives. This changes no orbit state, physical parameters, original raw engine outputs, acceptance thresholds or independent-frame approval status. **The new head requires a genuine R2026a CI run before marking this Sun anchor executed.**
