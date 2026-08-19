# Canonical TaskSpec 1.0

## 1. Status

Canonical TaskSpec `1.0.0` is the public source of truth for the simulation Agent, forms, CLI/API integrations, deterministic compilation, validation, reproducibility metadata, and future execution planning.

Legacy TaskSpec `0.1.0` remains accepted only through the deterministic migration bridge. Existing capability adapters continue to receive an internal compatibility mapping until their contracts are migrated in later versions.

## 2. Processing boundary

```text
Natural language / form / YAML / template
  -> DraftTaskSpec
  -> CanonicalTaskSpec 1.0
  -> validation and Agent guards
  -> trusted runtime compatibility mapping
  -> existing capability adapter / runner
```

The runtime compatibility mapping is not a second public specification. It is an implementation boundary used to preserve compatibility with existing adapters.

## 3. Top-level structure

```yaml
schema_version: 1.0.0
task: {}
simulation: {}
mission: {}
parameters: {}
events: {}
outputs: {}
assurance: {}
model: {}
provenance: {}
metadata: {}
```

### `task`

Contains the stable task identity, display name, description, and tags.

### `simulation`

Defines the simulation level, subsystem when applicable, duration, integration step, sample interval, random seed, backend, time basis, epoch, and solver settings.

Rules include:

- `duration_s > 0`;
- `sample_s > 0` and `sample_s <= duration_s`;
- `step_s > 0`;
- subsystem tasks require `simulation.subsystem`;
- event start time must be strictly before the simulation duration;
- a bounded event must end after it starts and no later than the simulation duration.

### `mission`

`mission.required_couplings` records the physical causal relationships that the request requires. Values are selected from the authoritative coupling catalog and are normalized from legacy aliases. Example:

```yaml
mission:
  required_couplings:
    - eps_pdu_to_payload_activity
    - payload_activity_to_eps_thermal
```

The field is preserved in the runtime TaskSpec, ResolvedSpec, ExecutionPlan and run input artifacts. A capability must explicitly declare every required coupling; whole-spacecraft level alone does not grant wildcard support. Missing causal support blocks planning before simulation execution.

### `parameters`

Separates parameter evidence profile from model values and overrides.

Supported parameter profiles are:

- `demo`;
- `engineering_estimate`;
- `ground_calibrated`;
- `flight_correlated`.

### `events`

Faults and degradations are separate typed collections. They cannot be silently interchanged. Each event carries an identity, target, effect, timing, implementation type, delivery path, and optional parameters.

### `outputs`

Defines the output root, trace format, QoI fields, files, plots, and bundle switches. QoI lists are normalized to a stable de-duplicated order.

### `assurance`

Defines the requested fidelity, claim level, validation profile, parameter profile, and whether proxy implementations are permitted.

### `model`

Binds the public task to a capability ID and target. It also carries existing model configuration that has not yet been normalized into operator contracts.

### `provenance`

Records where values came from:

- `user_provided`;
- `form_default`;
- `template_default`;
- `agent_inferred`;
- `resolver_derived`;
- `legacy_migration`.

Assumptions and pending confirmations are explicit, not hidden in model prose.

## 4. Determinism

Canonical documents are validated and serialized by Pydantic. The compiler records two hashes:

- canonical TaskSpec hash: identity of the public request;
- runtime compatibility hash: identity of the current adapter-facing mapping.

The hashes are intentionally different because the runtime bridge contains legacy-shaped fields and compatibility metadata.

## 5. Legacy migration

`migrate_legacy_task_spec()` performs a deterministic in-memory migration from `0.1.0` to `1.0.0`. It preserves:

- task identity and level;
- capability and target binding;
- simulation duration, sample rate, solver, seed, and backend;
- component or subsystem parameters;
- legacy fault and degradation payloads;
- modern modifier events;
- outputs and record fields;
- existing metadata.

Migration returns structured notices and records `provenance.migrated_from_version`.

The migration bridge does not claim to reinterpret ambiguous business semantics. Ambiguous requirements remain a later natural-language parsing concern.

## 6. Schema generation

The JSON Schema is generated from the Pydantic model:

```bash
python scripts/generate_taskspec_schema.py
```

Generated file:

```text
src/sat_sim/schemas/task_spec.schema.json
```

The previous hand-written schema is retained as:

```text
src/sat_sim/schemas/task_spec_legacy_0_1.schema.json
```

Do not edit the canonical JSON Schema manually.

## 7. Compatibility policy

- New external integrations should emit Canonical TaskSpec `1.0.0`.
- Existing `0.1.0` files are accepted through migration.
- Capability adapters remain internal implementation details.
- A future schema change requires a versioned migration, not silent field reinterpretation.
