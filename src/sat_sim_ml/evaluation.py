"""Real execution evidence for generated tasks, independent of model training."""
from __future__ import annotations

import hashlib
import json
import math
from pathlib import Path
from typing import Any, Mapping

from .data import sha256


def _finite(value: Any) -> bool:
    if isinstance(value, float):
        return math.isfinite(value)
    if isinstance(value, Mapping):
        return all(_finite(item) for item in value.values())
    if isinstance(value, (list, tuple)):
        return all(_finite(item) for item in value)
    return True


def _observed_physical_checks_passed(physical: Mapping[str, Any]) -> bool:
    """Require real, warning-free checks; missing optional checks are unassessed.

    A partial report may have overall ``warning`` solely because other default
    checks have no applicable telemetry. Never count those missing checks as a
    pass, and never accept an actual warning/failure or empty coverage.
    """
    observed = physical.get('checks_run') or []
    results = physical.get('check_results') or {}
    return bool(observed) and (
        physical.get('status') in {'pass','warning'}
        and physical.get('fail_count') == 0
        and physical.get('warning_count') == 0
        and all(results.get(name, {}).get('evaluated') is True
                and results[name].get('status') == 'PASS' for name in observed)
    )


def registry_bound_messages(messages: list[dict[str, Any]], capability_id: str):
    """Add declared backend information, never reference-output information."""
    from sat_sim.capability_registry import get_capability
    if [item.get('role') for item in messages] != ['system','user']:
        raise ValueError('ONLY_SYSTEM_AND_USER_MESSAGES_MAY_BE_SENT')
    if capability_id not in messages[1]['content']:
        raise ValueError('REGISTRY_CONSTRAINT_CAPABILITY_MUST_BE_PRESENT_IN_INPUT')
    contract = get_capability(capability_id).data
    allowed = contract.get('implementation', {}).get('accepted_task_backends') or []
    copied = json.loads(json.dumps(messages))
    evidence = {'capability_id':capability_id, 'accepted_task_backends':list(allowed),
        'source':'authoritative_capability_registry_not_reference_label',
        'contract_sha256':hashlib.sha256(json.dumps(contract,sort_keys=True).encode()).hexdigest()}
    if allowed:
        copied[1]['content'] += ('\nAuthoritative execution constraint from the capability registry: '
            'simulation.backend must be one of '+json.dumps(list(allowed))+'. '
            'Use the sole value exactly if only one is declared. Keep all other requested settings unchanged.')
    return copied, evidence


def verify_task_execution(spec: Mapping[str, Any], directory: Path) -> dict[str, Any]:
    """Replay the exact model task through production planning and validation.

    This is additional to executing the exported script. It establishes basic
    runtime/physical checks and sealed evidence, never target-flight validity.
    No executor override, teacher repair, proxy substitution or retry is used.
    """
    from sat_sim.run_bundle import execute_prepared_run, prepare_run, verify_run_bundle
    from sat_sim.task_models import canonicalize_task_spec

    prepared = prepare_run(spec, output_root=directory/'runtime-verification')
    result = execute_prepared_run(prepared.bundle_root,
        expected_plan_sha256=prepared.execution_plan_sha256, max_attempts=1, hard_timeout=True)
    root = Path(prepared.bundle_root)
    seal = json.loads((root/'SEALED.json').read_text(encoding='utf-8'))
    port = json.loads((root/'runtime/execution_port.json').read_text(encoding='utf-8'))
    bound_spec = json.loads((root/'input/task_spec.json').read_text(encoding='utf-8'))
    physical = result.validation.physical_validation
    checks = {
        'runtime_generated_spec_bound': bound_spec == canonicalize_task_spec(spec),
        'runtime_execution_succeeded': str(result.run_record.execution_status) == 'SUCCEEDED',
        'runtime_validation_passed': str(result.validation.result) == 'PASS',
        'runtime_physical_checks_passed': _observed_physical_checks_passed(physical),
        'runtime_trace_present': len(result.trace_rows) > 1,
        'runtime_numeric_outputs_finite': _finite(result.trace_rows) and _finite(result.summary),
        'runtime_real_adapter': port.get('status') == 'SUCCEEDED' and port.get('test_integration_seam') is not True,
        'runtime_bundle_integrity': verify_run_bundle(root)['ok'] is True,
        'runtime_seal_bound': seal.get('bundle_manifest_sha256') == sha256(root/'bundle_manifest.json'),
    }
    evidence = {
        'schema_version': 'sat-modelscope-execution-verification.v2',
        'passed': all(checks.values()), 'checks': checks,
        'bundle_root': str(root), 'plan_sha256': prepared.execution_plan_sha256,
        'bundle_manifest_sha256': sha256(root/'bundle_manifest.json'),
        'validation': result.validation.model_dump(mode='json'),
        'trace_row_count': len(result.trace_rows),
        'physical_coverage_status': physical.get('coverage_status'),
        'unassessed_physical_checks': list(physical.get('checks_missing') or []),
        'scope': 'Warning-free observed basic physical checks for this generated task; missing checks are not passes. No full-physics or flight qualification.',
        'can_claim_flight_validity': False,
    }
    (directory/'runtime-verification.json').write_text(
        json.dumps(evidence, ensure_ascii=False, indent=2)+'\n', encoding='utf-8')
    return evidence
