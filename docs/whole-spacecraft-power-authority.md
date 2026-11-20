# Whole-spacecraft electrical authority and resource scope

Every `whole_spacecraft.*` capability declares `resource_accounting`, and
`capability_summary_payload()` exposes that declaration in the machine catalog.
Its `evidence_scope` is always
`capability_declaration_not_runtime_measurement`. A catalog entry describes an
implementation and its supported scope; a completed native-product run supplies the actual
electrical inventory and resource evidence.

## The thirteen capability contracts

The counts below are **capability-contract counts**, not electrical provider or
scheduled-model counts. Existing product routing and direct API availability
are preserved.

| Capability suffix (`whole_spacecraft.<suffix>.v1`) | Route scope | Electrical accounting decision |
|---|---|---|
| `unified_native` | Native product; recommended | Retain official Basilisk electrical nodes plus actual project heater outputs; publish the runtime inventory. |
| `composite_digital_twin` | Native product | Retain the same authority boundary across native/fallback Comm and network/reduced thermal branches. |
| `power_thermal_orbit_coupled` | Active local electrical product model | Retain local solar, load, battery and thermal equations; optional source-provider configurations keep their declared module scope. |
| `comm_payload_mission_coupled` | Active local electrical product model | Retain local payload/Comm/bus loads and compact solar/battery equations. |
| `orbit_adcs_fidelity` | Product without EPS | Retain orbit and ADCS scope; no electrical closure claim. |
| `maneuver_orbit_attitude` | Product without EPS | Retain maneuver, propellant and attitude scope; thrust mechanics are not an electrical load model. |
| `basilisk_6dof` | Hidden, explicit adapter only | Retain native dynamics scope without an EPS model. |
| `bsksim_foundation` | Hidden, explicit adapter only | Retain native foundation/engineering test scope without an EPS model. |
| `orbit_attitude_thermal` | Hidden, explicit adapter only | Retain prescribed internal/heater heat as thermal boundary inputs, without battery accounting. |
| `basic_power_orbit` | Deprecated direct API | Retain historical local orbit/EPS reproduction. |
| `basic_power_attitude_orbit` | Deprecated direct API | Retain historical orbit/ADCS/EPS reproduction; current INT-1 and native product routes already exist. |
| `basic_power_thermal_orbit` | Deprecated direct API | Retain historical local orbit/EPS/thermal reproduction. |
| `bsksim_coupled` | Deprecated direct API | Retain historical project coupled equations; the BSKSim-style name does not imply official native electrical authority. |

This is a 2 + 2 + 2 + 3 + 4 partition. In particular, the two active local
electrical models remain available. They are not silently retired or relabeled
as native power providers.

The declarations list `route_scope`, `electrical_model`, `decision`,
`implementation_references`, and `evidence_scope`. Only the two native product
routes advertise `runtime_inventory_field: power_authority_inventory` and
`runtime_resource_evidence_field: resource_closure_evidence`.

## What the native runtime counts

[`power_authority_inventory`](../src/integration/power_authority_inventory.py)
identifies a provider by its actual model and electrical output-message
identity. Aliases do not create extra providers. Configured zero-power hardware
still counts; absent reduced heater hardware has no electrical output port.
The scheduled census also detects electrical outputs omitted from the declared
bindings.

| Runtime category | Meaning |
|---|---|
| Official electrical provider | An official Basilisk electrical output, such as `SimplePowerSink`, `SimpleSolarPanel`, `ReactionWheelPower`, or `AntennaPower`. |
| Project physical electrical provider | Actual project heater electrical output; thermostat, forced state, PDU permission and power scaling determine its consumption. |
| Engineering electrical proxy | A scheduled legacy activity-to-watts or nominal heater-feedback model. These are counted separately when actually present. |
| Status adapter | Selects a device state from native messages; it owns no electrical watts. |
| Heat conversion | Converts existing electrical input to deposited heat using explicit fractions; it creates no second electrical load. |
| Scheduled model | Every unique scheduled runtime model, including recorders and non-electrical dynamics, control and thermal modules. |

The full scheduled census is
`power_authority_inventory.scheduled_models`. Unified-native's older
`official_module_count`, `project_native_module_count`,
`external_or_proxy_module_count`, and `engineering_proxy_module_count` belong
to its legacy construction ledger. They must not be substituted for the full
scheduled census or the inventory's electrical-provider counts.

Composite's legacy `proxy_coupling_count` has unit **link records**. It counts
coupling-matrix records, not power-provider instances or electrical proxy
modules. Provider counts are under `power_authority_inventory.counts`, whose
scope is `unique_model_and_electrical_output_message`.

### Measured public configurations

| Executed configuration | Electrical providers | Official | Project physical | Engineering proxies |
|---|---:|---:|---:|---:|
| Unified, propulsion disabled | 10 | 8 | 2 | 0 |
| Unified, propulsion enabled | 12 | 10 | 2 | 0 |
| Composite, native downlink, thermal network, propulsion disabled | 16 | 14 | 2 | 0 |
| Composite, native downlink, thermal network, propulsion enabled | 18 | 16 | 2 | 0 |
| Composite, fallback transmitter, thermal network, propulsion disabled | 15 | 13 | 2 | 0 |
| Composite, fallback transmitter, thermal network, propulsion enabled | 17 | 15 | 2 | 0 |
| Composite, native downlink, reduced thermal, propulsion disabled | 15 | 14 | 1 | 0 |
| Composite, native downlink, reduced thermal, propulsion enabled | 17 | 16 | 1 | 0 |
| Composite, fallback transmitter, reduced thermal, propulsion disabled | 14 | 13 | 1 | 0 |
| Composite, fallback transmitter, reduced thermal, propulsion enabled | 16 | 15 | 1 | 0 |

These are configuration-specific measurements from the
[public compiler/registered-adapter matrix](../tests/test_whole_resource_inventory_matrix.py),
not catalog constants. The [evidence snapshot](../reports/p10a7-resource-closure-evidence.json)
contains inputs, source hashes, actual authorities, thermal transfers and battery
observations. All ten inventories report zero unknown or unregistered providers.
Enabling propulsion instantiates two configured auxiliary load nodes, including
the zero-on-time thruster; providers count hardware, not only positive consumption.
Different hardware or configuration requires its own completed-run inventory.

At the P10-A6 baseline, Unified had zero reconstruction proxies; Composite had
zero with native downlink/network thermal, one with either fallback Comm or
reduced thermal, and two with both. P10-A7 removes the scheduled
`DataActivityPowerBridge` and `HeaterPowerFeedback` from these branches. Their
legacy class definitions remain available to compatibility APIs; no claim is
made that the entire repository contains no engineering electrical equations.

## Electrical profiles and thermal boundaries

The [composite builder](../src/whole_spacecraft/builder.py) uses official
`AntennaPower` plus separate base and idle sinks for native downlink. Its
fallback native transmitter uses official sinks with **32 W active, 2 W
PDU-enabled idle, and 0 W off**. Any finite negative transmitter baud rate
selects the full active load. There is no proportional baud-to-watts equation.
Malformed input fails closed and remains visible in cumulative diagnostics.

Payload and propulsion auxiliary loads use configured fixed profiles. Thruster
electrical loads represent valves/controllers; chemical energy and mechanical
thrust power are separate quantities. Reaction-wheel electrical behavior comes
from official `ReactionWheelPower` and its configured parameters. These choices
identify electrical authority; they do not establish flight calibration.
Composite retains its configured 5 W thermal-service allocation in the EPS load
table, routed to electronics, separately from the actual heater outputs. It is
a fixed engineering load assumption, not measured heater consumption.

Network heaters publish their actual electrical consumption. The reduced
heater owner publishes the exact thermostat/forced/PDU/scaled heat term as
electrical consumption under an explicit **unity efficiency** assumption.
Unconfigured reduced nodes have no heater hardware or heater fault targets.

`ThermalPowerBridge` routes each declared input to a destination. The global
source budget groups actual message identities and rejects total heat fractions
above one, including accidental duplication across different destinations. The native
dynamics sampler retains source energy between thermal updates and presents
interval-average heat to the receiver. Internal electrical deposition does not
vanish in eclipse. Whole-coupled network configurations remove the standalone
43 W internal-heat placeholders; standalone thermal examples keep their own
prescribed inputs. The reduced whole-spacecraft branch has no external solar
heat model. Its existing cooling term is thermal rejection, without an invented
electrical conversion.
Both network and reduced heaters integrate the previous held actuator output
over the completed thermal interval before publishing the next commanded state.
A switch at the current update must not retroactively heat the preceding interval.
Completed electrical/heat energy and the unconsumed final partial interval are
reported separately; the latter is pending energy, not received node heat.

## Reading resource evidence

Both native product routes advertise `power_authority_inventory` and
`resource_closure_evidence` as structured runtime JSON. Inventory provenance,
message-read health, actual source energy and receiving-node energy support
the result; the existence of a catalog field is not proof of a passing run.
Intentionally disabled electrical/thermal coupling edges remain separate from
hardware existence and do not establish full resource closure.

| Runtime | Scope of `resource_closure_evidence` |
|---|---|
| Unified | Electrical authority census and measured internal-heat transfer. This field does not add a full native battery-balance audit; existing battery-bound diagnostics retain their own scope. |
| Composite | Electrical authority census, measured native battery balance and actual internal-heat transfer. Explicitly disabled resource edges return `PARTIAL`. |

The same scope is exposed as `resource_accounting.runtime_resource_evidence_scope`
in each native capability declaration. A `PASS` is limited to that declared scope.

Composite publishes `battery_capacity_j` in joules and
`battery_source_time_s` in seconds in both trace declarations. Its summary and
operator QoI views expose `battery_evidence_status`,
`battery_evidence_sample_count`, `battery_startup_excluded_sample_count`,
`battery_evidence_start_s`, and `battery_evidence_end_s`. Conservation is judged
on the observed native battery-message interval, with startup exclusion
reported explicitly. Public recorder spacing does not redefine the native
battery audit cadence.

The [catalog regression](../tests/test_whole_spacecraft_resource_catalog.py)
checks all thirteen scopes, source references, machine exposure, evidence
fields, lifecycle consistency and actual composite coupling IDs. Native
behavior is checked separately by the
[composite branch regressions](../tests/test_composite_resource_power_branches.py),
[battery evidence tests](../tests/test_composite_battery_evidence.py),
[power inventory tests](../tests/test_power_authority_inventory.py), and
[thermal sampling tests](../tests/test_thermal_power_sampling.py).
