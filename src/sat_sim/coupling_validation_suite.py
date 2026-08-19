"""End-to-end one-factor coupling validation suite.

The suite turns a declarative case matrix into immutable baseline and
perturbation Run Bundles, executes both with hard timeouts, verifies that only
the declared intervention changed, checks execution/capability/cadence identity,
and signs the resulting causal evidence with SHA-256 hashes.
"""
from __future__ import annotations

import copy
import hashlib
import json
import os
import re
import shutil
import signal
import subprocess
import sys
import time
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Mapping, Sequence

import yaml

from .coupling_causality import CausalExpectation, evaluate_coupling_causality, load_telemetry
from .run_bundle import verify_run_bundle


def _now() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _read_json(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError(f"expected mapping JSON: {path}")
    return value


def _portable_environment_payload(payload: Mapping[str, Any]) -> dict[str, Any]:
    """Redact build-host absolute paths while preserving Doctor conclusions.

    The raw Doctor report remains available to the release engineer.  The
    copied suite evidence is deliberately portable so that extracting the
    evidence package on another host does not embed misleading local paths.
    """
    copied = copy.deepcopy(dict(payload))
    environment = copied.get("environment") if isinstance(copied.get("environment"), Mapping) else {}
    project_root = str(environment.get("cwd", "") or "")
    executable = str(environment.get("executable", "") or "")
    venv_root = ""
    if executable:
        executable_path = Path(executable)
        if executable_path.parent.name in {"bin", "Scripts"}:
            venv_root = str(executable_path.parent.parent)

    replacements: list[tuple[str, str]] = []
    def looks_absolute_path(value: str) -> bool:
        return bool(value.startswith(("/", "\\")) or re.match(r"^[A-Za-z]:[\\/]", value))

    if project_root and looks_absolute_path(project_root):
        replacements.append((project_root, "$PROJECT_ROOT"))
    if venv_root and looks_absolute_path(venv_root):
        replacements.append((venv_root, "$VENV"))
    if executable and looks_absolute_path(executable):
        replacements.append((executable, "$PYTHON"))
    replacements.sort(key=lambda item: len(item[0]), reverse=True)

    def redact(value: Any) -> Any:
        if isinstance(value, Mapping):
            return {str(key): redact(item) for key, item in value.items()}
        if isinstance(value, list):
            return [redact(item) for item in value]
        if isinstance(value, tuple):
            return [redact(item) for item in value]
        if not isinstance(value, str):
            return value
        text = value
        for prefix, token in replacements:
            text = text.replace(prefix, token)
        # Any remaining host-absolute path is represented by a stable basename
        # token instead of leaking the build machine layout.
        if looks_absolute_path(text):
            try:
                text = f"$EXTERNAL/{Path(text).name}"
            except (TypeError, ValueError):
                text = "$EXTERNAL"
        return text

    portable = redact(copied)
    if isinstance(portable, dict):
        portable["path_redaction"] = {
            "status": "APPLIED",
            "tokens": [token for _prefix, token in replacements],
            "scope": "build_host_absolute_paths_only",
        }
    return portable


def _portable_execution_record(record: Mapping[str, Any], suite_root: Path) -> dict[str, Any]:
    """Remove build-host absolute paths while retaining command integrity."""
    root_text = str(suite_root.resolve())
    raw_command = [str(item) for item in record.get("command", ())]
    display_command: list[str] = []
    for item in raw_command:
        if item == str(Path(sys.executable).resolve()) or item == sys.executable:
            display_command.append("$PYTHON")
        elif root_text in item:
            display_command.append(item.replace(root_text, "$SUITE_ROOT"))
        else:
            try:
                candidate = Path(item)
                if candidate.is_absolute():
                    display_command.append(f"$EXTERNAL/{candidate.name}")
                else:
                    display_command.append(item)
            except (TypeError, ValueError):
                display_command.append(item)
    output = dict(record)
    output["command_sha256"] = hashlib.sha256("\0".join(raw_command).encode("utf-8")).hexdigest()
    output["command"] = display_command
    log_path = Path(str(record.get("log_path", "")))
    if log_path.is_absolute() and log_path.is_relative_to(suite_root):
        output["log_path"] = log_path.relative_to(suite_root).as_posix()
    elif log_path.name:
        output["log_path"] = f"$EXTERNAL/{log_path.name}"
    return output


def _set_path(payload: dict[str, Any], dotted_path: str, value: Any) -> None:
    tokens = [token for token in str(dotted_path).split(".") if token]
    if not tokens:
        raise ValueError("patch path is empty")
    current: dict[str, Any] = payload
    for token in tokens[:-1]:
        child = current.get(token)
        if child is None:
            child = {}
            current[token] = child
        if not isinstance(child, dict):
            raise ValueError(f"patch path traverses non-mapping at {token!r}: {dotted_path}")
        current = child
    current[tokens[-1]] = copy.deepcopy(value)


def _leaf_differences(left: Any, right: Any, prefix: str = "") -> list[str]:
    if isinstance(left, Mapping) and isinstance(right, Mapping):
        paths: list[str] = []
        for key in sorted(set(left) | set(right), key=str):
            path = f"{prefix}.{key}" if prefix else str(key)
            if key not in left or key not in right:
                paths.append(path)
            else:
                paths.extend(_leaf_differences(left[key], right[key], path))
        return paths
    if isinstance(left, list) and isinstance(right, list):
        if left == right:
            return []
        if len(left) != len(right):
            return [prefix]
        paths: list[str] = []
        for index, (l_value, r_value) in enumerate(zip(left, right, strict=True)):
            paths.extend(_leaf_differences(l_value, r_value, f"{prefix}[{index}]"))
        return paths or [prefix]
    return [] if left == right else [prefix]


def _allowed_difference(path: str, patch_path: str) -> bool:
    # When the baseline omits an optional parent mapping, ``_leaf_differences``
    # reports the newly created parent (for example ``parameters.coupling``)
    # rather than its only leaf.  Accept that ancestor only when it lies on the
    # exact declared patch path; unrelated sibling changes remain visible.
    return (
        path == patch_path
        or path.startswith(patch_path + ".")
        or path.startswith(patch_path + "[")
        or patch_path.startswith(path + ".")
        or patch_path.startswith(path + "[")
    )


def validate_one_factor_change(
    baseline: Mapping[str, Any],
    perturbed: Mapping[str, Any],
    *,
    patch_path: str,
    companion_patch_paths: Sequence[str] = (),
) -> dict[str, Any]:
    differences = _leaf_differences(baseline, perturbed)
    allowed_paths = (patch_path, *tuple(str(item) for item in companion_patch_paths))
    unexpected = [path for path in differences if not any(_allowed_difference(path, allowed) for allowed in allowed_paths)]
    declared_changed = any(_allowed_difference(path, patch_path) for path in differences)
    status = "PASS" if declared_changed and not unexpected else "FAIL"
    reasons: list[str] = []
    if not declared_changed:
        reasons.append("DECLARED_INTERVENTION_DID_NOT_CHANGE_SPEC")
    if unexpected:
        reasons.append("UNDECLARED_SPEC_DIFFERENCE")
    return {
        "schema_version": "v0572a.one-factor-diff.v1",
        "status": status,
        "physical_intervention_path": patch_path,
        "companion_semantic_paths": list(companion_patch_paths),
        "difference_count": len(differences),
        "differences": differences,
        "unexpected_differences": unexpected,
        "reason_codes": reasons,
    }


@dataclass(frozen=True)
class RunAcceptancePolicy:
    """Explicitly define which sealed run outcomes are admissible.

    Counterfactual perturbations can intentionally violate mission gates while
    still being structurally and numerically valid simulations.  Such cases
    must opt in to the expected non-zero CLI/validation outcome; the default
    remains a fully passing run.
    """

    accepted_return_codes: tuple[int, ...] = (0,)
    accepted_run_record_statuses: tuple[str, ...] = ("SUCCEEDED",)
    accepted_validation_results: tuple[str, ...] = ("PASS",)
    accepted_summary_statuses: tuple[str, ...] = ("PASS",)
    accepted_execution_statuses: tuple[str, ...] = ("PASS",)
    accepted_structural_statuses: tuple[str, ...] = ("PASS",)
    accepted_numerical_statuses: tuple[str, ...] = ("PASS",)
    accepted_runtime_task_cadence_statuses: tuple[str, ...] = ("PASS",)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class CouplingValidationCase:
    case_id: str
    description: str
    patch_path: str
    patch_value: Any
    expectations: tuple[CausalExpectation, ...]
    setup_patches: tuple[tuple[str, Any], ...] = ()
    companion_patches: tuple[tuple[str, Any], ...] = ()
    covers_runtime_couplings: tuple[str, ...] = ()
    perturbed_run_acceptance: RunAcceptancePolicy = RunAcceptancePolicy()


@dataclass(frozen=True)
class CouplingValidationSuiteResult:
    status: str
    report_path: str
    case_count: int
    pass_count: int
    fail_count: int
    inconclusive_count: int


def _string_tuple(payload: Mapping[str, Any], key: str, default: tuple[str, ...]) -> tuple[str, ...]:
    value = payload.get(key, default)
    if not isinstance(value, Sequence) or isinstance(value, (str, bytes)):
        raise ValueError(f"run acceptance field {key!r} must be a sequence")
    result = tuple(str(item) for item in value)
    if not result:
        raise ValueError(f"run acceptance field {key!r} must not be empty")
    return result


def _run_acceptance_from_mapping(payload: Any) -> RunAcceptancePolicy:
    if payload is None:
        return RunAcceptancePolicy()
    if not isinstance(payload, Mapping):
        raise ValueError("perturbed_run_acceptance must be a mapping")
    raw_codes = payload.get("accepted_return_codes", (0,))
    if not isinstance(raw_codes, Sequence) or isinstance(raw_codes, (str, bytes)):
        raise ValueError("accepted_return_codes must be a sequence")
    codes = tuple(int(item) for item in raw_codes)
    if not codes:
        raise ValueError("accepted_return_codes must not be empty")
    return RunAcceptancePolicy(
        accepted_return_codes=codes,
        accepted_run_record_statuses=_string_tuple(payload, "accepted_run_record_statuses", ("SUCCEEDED",)),
        accepted_validation_results=_string_tuple(payload, "accepted_validation_results", ("PASS",)),
        accepted_summary_statuses=_string_tuple(payload, "accepted_summary_statuses", ("PASS",)),
        accepted_execution_statuses=_string_tuple(payload, "accepted_execution_statuses", ("PASS",)),
        accepted_structural_statuses=_string_tuple(payload, "accepted_structural_statuses", ("PASS",)),
        accepted_numerical_statuses=_string_tuple(payload, "accepted_numerical_statuses", ("PASS",)),
        accepted_runtime_task_cadence_statuses=_string_tuple(payload, "accepted_runtime_task_cadence_statuses", ("PASS",)),
    )


def _case_from_mapping(payload: Mapping[str, Any]) -> CouplingValidationCase:
    patch = payload.get("patch")
    if not isinstance(patch, Mapping):
        raise ValueError(f"case {payload.get('case_id')} patch must be a mapping")
    expectations_raw = payload.get("expectations")
    if not isinstance(expectations_raw, Sequence) or isinstance(expectations_raw, (str, bytes)):
        raise ValueError(f"case {payload.get('case_id')} expectations must be a sequence")
    return CouplingValidationCase(
        case_id=str(payload["case_id"]),
        description=str(payload.get("description", "")),
        patch_path=str(patch["path"]),
        patch_value=copy.deepcopy(patch.get("value")),
        expectations=tuple(CausalExpectation(**dict(item)) for item in expectations_raw if isinstance(item, Mapping)),
        setup_patches=tuple(
            (str(item["path"]), copy.deepcopy(item.get("value")))
            for item in payload.get("setup_patches", ())
            if isinstance(item, Mapping)
        ),
        companion_patches=tuple(
            (str(item["path"]), copy.deepcopy(item.get("value")))
            for item in payload.get("companion_patches", ())
            if isinstance(item, Mapping)
        ),
        covers_runtime_couplings=tuple(str(item) for item in payload.get("covers_runtime_couplings", ())),
        perturbed_run_acceptance=_run_acceptance_from_mapping(payload.get("perturbed_run_acceptance")),
    )


def _bundle_evidence(bundle: Path) -> dict[str, Any]:
    files = [
        "SEALED.json",
        "bundle_manifest.json",
        "input/task_spec.json",
        "input/resolved_spec.json",
        "input/execution_plan.json",
        "runtime/environment.json",
        "runtime/dependency_versions.json",
        "runtime/capability_contract.json",
        "results/summary.json",
        "results/telemetry.jsonl",
        "validation/validation_outcome.json",
    ]
    evidence: dict[str, Any] = {}
    for relative in files:
        path = bundle / relative
        if path.is_file():
            evidence[relative] = {"size_bytes": path.stat().st_size, "sha256": _sha256(path)}
    return evidence


def _terminate_process_tree(process: subprocess.Popen[str], *, grace_s: float = 5.0) -> str:
    """Terminate the complete child process tree on Linux/macOS or Windows."""
    method = "unknown"
    if process.poll() is not None:
        return "already_exited"
    if os.name == "nt":
        method = "windows_taskkill_tree"
        subprocess.run(
            ["taskkill", "/PID", str(process.pid), "/T", "/F"],
            capture_output=True, text=True, check=False,
        )
    else:
        method = "posix_process_group_sigterm"
        try:
            os.killpg(os.getpgid(process.pid), signal.SIGTERM)
        except ProcessLookupError:
            return "already_exited"
    try:
        process.wait(timeout=max(0.1, float(grace_s)))
        return method
    except subprocess.TimeoutExpired:
        if os.name == "nt":
            subprocess.run(
                ["taskkill", "/PID", str(process.pid), "/T", "/F"],
                capture_output=True, text=True, check=False,
            )
            method = "windows_taskkill_tree_forced"
        else:
            try:
                os.killpg(os.getpgid(process.pid), signal.SIGKILL)
                method = "posix_process_group_sigkill"
            except ProcessLookupError:
                pass
        process.wait(timeout=max(0.1, float(grace_s)))
        return method


def _cleanup_residual_process_group(process_group_id: int, *, grace_s: float = 1.0) -> str | None:
    """Terminate descendants that outlive the CLI process.

    Some multiprocessing helpers inherit the CLI stdout file descriptor.  If
    stdout is captured with ``PIPE``, ``communicate()`` can wait forever even
    after the CLI has completed and sealed its Run Bundle.  The suite therefore
    writes directly to a log file and treats any process still in the isolated
    process group after the CLI exits as a leaked descendant.
    """
    if os.name == "nt":
        # The child was started in a new process group.  ``taskkill /T`` is the
        # available best-effort cleanup on Windows; a missing PID is harmless.
        completed = subprocess.run(
            ["taskkill", "/PID", str(process_group_id), "/T", "/F"],
            capture_output=True,
            text=True,
            check=False,
        )
        return "windows_taskkill_tree_post_exit" if completed.returncode == 0 else None

    try:
        os.killpg(process_group_id, 0)
    except ProcessLookupError:
        return None
    except PermissionError:
        pass

    try:
        os.killpg(process_group_id, signal.SIGTERM)
    except ProcessLookupError:
        return None
    deadline = time.monotonic() + max(0.1, float(grace_s))
    while time.monotonic() < deadline:
        try:
            os.killpg(process_group_id, 0)
        except ProcessLookupError:
            return "posix_process_group_sigterm_post_exit"
        time.sleep(0.05)
    try:
        os.killpg(process_group_id, signal.SIGKILL)
    except ProcessLookupError:
        return "posix_process_group_sigterm_post_exit"
    return "posix_process_group_sigkill_post_exit"


def _run_spec_isolated(
    spec_path: Path,
    *,
    output_root: Path,
    run_id: str,
    timeout_s: float,
    log_path: Path,
) -> dict[str, Any]:
    """Execute one Run Bundle in an isolated process group.

    The suite owns the hard timeout at the operating-system process-group
    boundary.  Inner multiprocessing hard-timeout mode is disabled here to
    avoid nested semaphore/resource-tracker leakage across a multi-case suite.
    Output is streamed directly to a file rather than captured with ``PIPE`` so
    a descendant that inherits stdout cannot deadlock ``communicate()`` after
    the main CLI process has already exited.
    """
    command = [
        sys.executable, "-m", "sat_sim.agent_cli", "run", str(spec_path),
        "--output-root", str(output_root), "--run-id", run_id,
        "--max-attempts", "1", "--no-hard-timeout",
        "--isolated-process-exit",
    ]
    started = _now()
    log_path.parent.mkdir(parents=True, exist_ok=True)
    timed_out = False
    termination_method: str | None = None
    residual_cleanup_method: str | None = None
    with log_path.open("w", encoding="utf-8") as log_handle:
        process = subprocess.Popen(
            command,
            stdout=log_handle,
            stderr=subprocess.STDOUT,
            text=True,
            start_new_session=True,
        )
        process_group_id = process.pid
        try:
            process.wait(timeout=max(1.0, float(timeout_s)))
        except subprocess.TimeoutExpired:
            timed_out = True
            termination_method = _terminate_process_tree(process)
        finally:
            # ``wait`` observes the CLI lifecycle only.  Clean any leaked
            # resource-tracker or multiprocessing descendants before the log
            # handle is closed and before the next validation case starts.
            residual_cleanup_method = _cleanup_residual_process_group(process_group_id)
            log_handle.flush()
    return {
        "command": command,
        "started_at": started,
        "finished_at": _now(),
        "return_code": process.returncode,
        "timed_out": timed_out,
        "timeout_s": float(timeout_s),
        "timeout_layer": "suite_process_group",
        "inner_hard_timeout": False,
        "termination_method": termination_method,
        "residual_process_group_cleanup": residual_cleanup_method,
        "log_path": str(log_path),
    }


def _run_ok(
    bundle: Path,
    policy: RunAcceptancePolicy | None = None,
) -> tuple[bool, list[str], dict[str, Any]]:
    acceptance = policy or RunAcceptancePolicy()
    reasons: list[str] = []
    run_record = _read_json(bundle / "run_record.json")
    validation = _read_json(bundle / "validation" / "validation_outcome.json")
    summary = _read_json(bundle / "results" / "summary.json")
    integrity = verify_run_bundle(bundle)
    observed = {
        "run_record_status": str(run_record.get("status")),
        "validation_result": str(validation.get("result")),
        "summary_status": str(summary.get("status")),
        "execution_status": str(summary.get("execution_status")),
        "structural_status": str(summary.get("structural_status")),
        "numerical_status": str(summary.get("numerical_status")),
        "runtime_task_cadence_status": str(summary.get("runtime_task_cadence_status")),
    }
    checks = (
        ("run_record_status", acceptance.accepted_run_record_statuses, "RUN_RECORD_STATUS_NOT_ACCEPTED"),
        ("validation_result", acceptance.accepted_validation_results, "RUN_VALIDATION_RESULT_NOT_ACCEPTED"),
        ("summary_status", acceptance.accepted_summary_statuses, "RUN_SUMMARY_STATUS_NOT_ACCEPTED"),
        ("execution_status", acceptance.accepted_execution_statuses, "RUN_EXECUTION_STATUS_NOT_ACCEPTED"),
        ("structural_status", acceptance.accepted_structural_statuses, "RUN_STRUCTURAL_STATUS_NOT_ACCEPTED"),
        ("numerical_status", acceptance.accepted_numerical_statuses, "RUN_NUMERICAL_STATUS_NOT_ACCEPTED"),
        ("runtime_task_cadence_status", acceptance.accepted_runtime_task_cadence_statuses, "RUNTIME_TASK_CADENCE_STATUS_NOT_ACCEPTED"),
    )
    for field_name, accepted, reason in checks:
        if observed[field_name] not in accepted:
            reasons.append(reason)
    if not bool(integrity.get("ok")):
        reasons.append("RUN_BUNDLE_INTEGRITY_FAILED")
    return not reasons, reasons, {
        **observed,
        "acceptance_policy": acceptance.to_dict(),
        "integrity": integrity,
    }


def _identity_check(baseline_bundle: Path, perturbed_bundle: Path) -> dict[str, Any]:
    b_prepared = _read_json(baseline_bundle / "runtime" / "prepared_run.json")
    p_prepared = _read_json(perturbed_bundle / "runtime" / "prepared_run.json")
    b_contract = _sha256(baseline_bundle / "runtime" / "capability_contract.json")
    p_contract = _sha256(perturbed_bundle / "runtime" / "capability_contract.json")
    b_deps = _sha256(baseline_bundle / "runtime" / "dependency_versions.json")
    p_deps = _sha256(perturbed_bundle / "runtime" / "dependency_versions.json")
    checks = {
        "same_capability_id": b_prepared.get("primary_capability_id") == p_prepared.get("primary_capability_id"),
        "same_capability_contract_sha256": b_contract == p_contract,
        "same_dependency_versions_sha256": b_deps == p_deps,
    }
    return {
        "schema_version": "v0572a.paired-run-identity.v1",
        "status": "PASS" if all(checks.values()) else "FAIL",
        "checks": checks,
        "baseline_capability_id": b_prepared.get("primary_capability_id"),
        "perturbed_capability_id": p_prepared.get("primary_capability_id"),
        "baseline_capability_contract_sha256": b_contract,
        "perturbed_capability_contract_sha256": p_contract,
        "baseline_dependency_versions_sha256": b_deps,
        "perturbed_dependency_versions_sha256": p_deps,
    }


def _runtime_coupling_coverage(
    baseline_bundle: Path,
    case_reports: Sequence[Mapping[str, Any]],
    *,
    required_runtime_couplings: Sequence[str] = (),
) -> dict[str, Any]:
    summary = _read_json(baseline_bundle / "results" / "summary.json")
    raw_catalog = summary.get("coupling_runtime_catalog")
    catalog = [dict(item) for item in raw_catalog if isinstance(item, Mapping)] if isinstance(raw_catalog, Sequence) else []
    passed_by_coupling: dict[str, list[str]] = {}
    declared_by_coupling: dict[str, list[str]] = {}
    for case in case_reports:
        case_id = str(case.get("case_id") or "")
        covered = case.get("covers_runtime_couplings")
        if not isinstance(covered, Sequence) or isinstance(covered, (str, bytes)):
            continue
        for coupling_id in (str(item) for item in covered):
            declared_by_coupling.setdefault(coupling_id, []).append(case_id)
            if str(case.get("status")) == "PASS":
                passed_by_coupling.setdefault(coupling_id, []).append(case_id)

    records: list[dict[str, Any]] = []
    active_ids: list[str] = []
    for item in catalog:
        coupling_id = str(item.get("coupling_id") or "")
        active = bool(item.get("active"))
        if active:
            active_ids.append(coupling_id)
        passed_cases = sorted(set(passed_by_coupling.get(coupling_id, ())))
        declared_cases = sorted(set(declared_by_coupling.get(coupling_id, ())))
        records.append({
            **item,
            "causal_validation_status": "PASS" if passed_cases else "NOT_VALIDATED",
            "declared_case_ids": declared_cases,
            "passed_case_ids": passed_cases,
        })

    active_set = set(active_ids)
    required = tuple(dict.fromkeys(str(item) for item in required_runtime_couplings))
    unknown_required = sorted(set(required) - active_set)
    missing_required = sorted(item for item in required if not passed_by_coupling.get(item))
    validated_active = sorted(item for item in active_ids if passed_by_coupling.get(item))
    status = "PASS" if not unknown_required and not missing_required else "FAIL"
    return {
        "schema_version": "v0573.runtime-coupling-causal-coverage.v1",
        "status": status,
        "active_runtime_coupling_count": len(active_ids),
        "causally_validated_runtime_coupling_count": len(validated_active),
        "causal_coverage_ratio": (len(validated_active) / len(active_ids)) if active_ids else 0.0,
        "required_runtime_couplings": list(required),
        "missing_required_runtime_couplings": missing_required,
        "unknown_required_runtime_couplings": unknown_required,
        "validated_runtime_couplings": validated_active,
        "not_yet_validated_runtime_couplings": sorted(active_set - set(validated_active)),
        "records": records,
        "claim_scope": "coverage only counts runtime couplings backed by at least one passing sealed paired-run case",
    }


def run_coupling_validation_suite(
    matrix_path: str | Path,
    *,
    output_root: str | Path,
    environment_evidence: str | Path | None = None,
) -> CouplingValidationSuiteResult:
    matrix_file = Path(matrix_path).resolve()
    matrix = _read_json(matrix_file)
    baseline_path = Path(str(matrix["baseline_spec"]))
    if not baseline_path.is_absolute():
        baseline_path = (matrix_file.parent / baseline_path).resolve()
    baseline_spec = yaml.safe_load(baseline_path.read_text(encoding="utf-8"))
    if not isinstance(baseline_spec, dict):
        raise ValueError("baseline TaskSpec must be a mapping")
    cases = [_case_from_mapping(item) for item in matrix.get("cases", ()) if isinstance(item, Mapping)]
    if not cases:
        raise ValueError("coupling validation matrix contains no cases")
    case_ids = [case.case_id for case in cases]
    if len(set(case_ids)) != len(case_ids):
        raise ValueError("coupling validation case_id values must be unique")
    invalid_ids = [value for value in case_ids if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._-]*", value)]
    if invalid_ids:
        raise ValueError(f"unsafe coupling validation case_id values: {invalid_ids}")
    if any(not case.expectations for case in cases):
        raise ValueError("every coupling validation case requires at least one causal expectation")

    suite_root = Path(output_root).resolve()
    if suite_root.exists() and any(suite_root.iterdir()):
        raise FileExistsError(f"coupling validation output directory must be new or empty: {suite_root}")
    suite_root.mkdir(parents=True, exist_ok=True)
    input_root = suite_root / "input"
    input_root.mkdir(parents=True, exist_ok=True)
    matrix_copy = input_root / "coupling_validation_matrix.json"
    baseline_copy = input_root / "baseline_task_spec.yaml"
    shutil.copy2(matrix_file, matrix_copy)
    shutil.copy2(baseline_path, baseline_copy)
    environment_copy: Path | None = None
    environment_source_sha256: str | None = None
    if environment_evidence is not None:
        environment_source = Path(environment_evidence).resolve()
        environment_source_sha256 = _sha256(environment_source)
        environment_copy = input_root / "environment_doctor.json"
        portable_environment = _portable_environment_payload(_read_json(environment_source))
        portable_environment["source_evidence_sha256"] = environment_source_sha256
        environment_copy.write_text(
            json.dumps(portable_environment, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
    case_reports: list[dict[str, Any]] = []

    shared_baseline_spec_path = suite_root / "shared_baseline_task_spec.yaml"
    shared_baseline_spec_path.write_text(
        yaml.safe_dump(baseline_spec, sort_keys=False, allow_unicode=True),
        encoding="utf-8",
    )
    shared_runs_root = suite_root / "shared_runs"
    shared_baseline_execution = _run_spec_isolated(
        shared_baseline_spec_path,
        output_root=shared_runs_root,
        run_id="baseline",
        timeout_s=float(matrix.get("case_run_timeout_s", 120.0)),
        log_path=suite_root / "shared_baseline_execution.log",
    )
    shared_baseline_bundle = shared_runs_root / "baseline"
    shared_baseline_ready = (
        not shared_baseline_execution["timed_out"]
        and int(shared_baseline_execution["return_code"] or 0) == 0
        and (shared_baseline_bundle / "SEALED.json").is_file()
    )

    for case in cases:
        case_root = suite_root / "cases" / case.case_id
        runs_root = case_root / "runs"
        case_root.mkdir(parents=True, exist_ok=True)
        case_baseline_spec = copy.deepcopy(baseline_spec)
        for setup_path, setup_value in case.setup_patches:
            _set_path(case_baseline_spec, setup_path, setup_value)
        perturbed_spec = copy.deepcopy(case_baseline_spec)
        _set_path(perturbed_spec, case.patch_path, case.patch_value)
        for companion_path, companion_value in case.companion_patches:
            _set_path(perturbed_spec, companion_path, companion_value)
        one_factor = validate_one_factor_change(
            case_baseline_spec,
            perturbed_spec,
            patch_path=case.patch_path,
            companion_patch_paths=tuple(path for path, _value in case.companion_patches),
        )
        (case_root / "baseline_task_spec.yaml").write_text(yaml.safe_dump(case_baseline_spec, sort_keys=False, allow_unicode=True), encoding="utf-8")
        (case_root / "perturbed_task_spec.yaml").write_text(yaml.safe_dump(perturbed_spec, sort_keys=False, allow_unicode=True), encoding="utf-8")

        report: dict[str, Any] = {
            "schema_version": "v0572a.coupling-validation-case.v1",
            "case_id": case.case_id,
            "description": case.description,
            "patch": {"path": case.patch_path, "value": case.patch_value},
            "setup_patches": [{"path": path, "value": value} for path, value in case.setup_patches],
            "companion_patches": [{"path": path, "value": value} for path, value in case.companion_patches],
            "covers_runtime_couplings": list(case.covers_runtime_couplings),
            "perturbed_run_acceptance": case.perturbed_run_acceptance.to_dict(),
            "one_factor_validation": one_factor,
            "status": "FAIL",
            "reason_codes": [],
        }
        if one_factor["status"] != "PASS":
            report["reason_codes"].append("ONE_FACTOR_VALIDATION_FAILED")
            case_reports.append(report)
            (case_root / "case_report.json").write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
            continue

        try:
            baseline_shared = not bool(case.setup_patches)
            if baseline_shared:
                if not shared_baseline_ready:
                    raise RuntimeError(
                        f"shared baseline did not produce a sealed bundle; rc={shared_baseline_execution['return_code']} "
                        f"timed_out={shared_baseline_execution['timed_out']}"
                    )
                baseline_execution = shared_baseline_execution
                baseline_bundle = shared_baseline_bundle
            else:
                baseline_execution = _run_spec_isolated(
                    case_root / "baseline_task_spec.yaml",
                    output_root=runs_root,
                    run_id="baseline",
                    timeout_s=float(matrix.get("case_run_timeout_s", 120.0)),
                    log_path=case_root / "baseline_execution.log",
                )
                baseline_bundle = runs_root / "baseline"
                if baseline_execution["timed_out"]:
                    raise TimeoutError("isolated case-specific baseline run timeout")
                baseline_return_code = int(baseline_execution["return_code"] if baseline_execution["return_code"] is not None else -999)
                if baseline_return_code != 0:
                    raise RuntimeError(f"isolated case-specific baseline returned {baseline_return_code}")
                if not (baseline_bundle / "SEALED.json").is_file():
                    raise RuntimeError("case-specific baseline did not produce a sealed bundle")
            perturbed_execution = _run_spec_isolated(
                case_root / "perturbed_task_spec.yaml",
                output_root=runs_root,
                run_id="perturbed",
                timeout_s=float(matrix.get("case_run_timeout_s", 120.0)),
                log_path=case_root / "perturbed_execution.log",
            )
            perturbed_bundle = runs_root / "perturbed"
            if perturbed_execution["timed_out"]:
                raise TimeoutError("isolated perturbed case run timeout")
            perturbed_return_code = int(perturbed_execution["return_code"] if perturbed_execution["return_code"] is not None else -999)
            if perturbed_return_code not in case.perturbed_run_acceptance.accepted_return_codes:
                raise RuntimeError(
                    f"isolated perturbed case run returned {perturbed_return_code}; "
                    f"accepted={case.perturbed_run_acceptance.accepted_return_codes}"
                )
            if not (baseline_bundle / "SEALED.json").is_file() or not (perturbed_bundle / "SEALED.json").is_file():
                raise RuntimeError(
                    f"paired run did not produce sealed bundles; baseline_rc={baseline_execution['return_code']} "
                    f"perturbed_rc={perturbed_execution['return_code']}"
                )
            baseline_ok, baseline_reasons, baseline_run = _run_ok(baseline_bundle)
            perturbed_ok, perturbed_reasons, perturbed_run = _run_ok(
                perturbed_bundle, case.perturbed_run_acceptance
            )
            identity = _identity_check(baseline_bundle, perturbed_bundle)
            causal = evaluate_coupling_causality(
                load_telemetry(baseline_bundle),
                load_telemetry(perturbed_bundle),
                case.expectations,
            )
            report.update({
                "baseline_run": {
                    "bundle_root": baseline_bundle.relative_to(suite_root).as_posix(),
                    "isolated_execution": _portable_execution_record(baseline_execution, suite_root),
                    "shared_across_cases": baseline_shared,
                    **baseline_run,
                },
                "perturbed_run": {
                    "bundle_root": perturbed_bundle.relative_to(suite_root).as_posix(),
                    "isolated_execution": _portable_execution_record(perturbed_execution, suite_root),
                    **perturbed_run,
                },
                "paired_run_identity": identity,
                "causal_evaluation": causal,
                "evidence": {
                    "baseline": _bundle_evidence(baseline_bundle),
                    "perturbed": _bundle_evidence(perturbed_bundle),
                },
            })
            reasons = [*baseline_reasons, *perturbed_reasons]
            if identity["status"] != "PASS":
                reasons.append("PAIRED_RUN_IDENTITY_FAILED")
            if causal["status"] != "PASS":
                reasons.append(f"CAUSAL_EVALUATION_{causal['status']}")
            report["reason_codes"] = list(dict.fromkeys(reasons))
            report["status"] = "PASS" if baseline_ok and perturbed_ok and identity["status"] == "PASS" and causal["status"] == "PASS" else (
                "INCONCLUSIVE" if causal["status"] == "INCONCLUSIVE" and baseline_ok and perturbed_ok and identity["status"] == "PASS" else "FAIL"
            )
        except Exception as exc:
            report["status"] = "ERROR"
            report["reason_codes"] = ["SUITE_EXECUTION_ERROR"]
            report["error"] = f"{type(exc).__name__}: {exc}"
        case_reports.append(report)
        (case_root / "case_report.json").write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")

    statuses = [str(item["status"]) for item in case_reports]
    coverage = _runtime_coupling_coverage(
        shared_baseline_bundle,
        case_reports,
        required_runtime_couplings=tuple(str(item) for item in matrix.get("required_runtime_couplings", ())),
    )
    cases_status = "PASS" if statuses and all(item == "PASS" for item in statuses) else (
        "INCONCLUSIVE" if statuses and not any(item in {"FAIL", "ERROR"} for item in statuses) else "FAIL"
    )
    overall = "PASS" if cases_status == "PASS" and coverage["status"] == "PASS" else (
        "INCONCLUSIVE" if cases_status == "INCONCLUSIVE" and coverage["status"] == "PASS" else "FAIL"
    )
    suite_report: dict[str, Any] = {
        "schema_version": "v0572a.coupling-validation-suite.v1",
        "generated_at": _now(),
        "status": overall,
        "matrix_path": matrix_copy.relative_to(suite_root).as_posix(),
        "matrix_sha256": _sha256(matrix_copy),
        "baseline_spec_path": baseline_copy.relative_to(suite_root).as_posix(),
        "baseline_spec_sha256": _sha256(baseline_copy),
        "case_count": len(case_reports),
        "pass_count": sum(item == "PASS" for item in statuses),
        "fail_count": sum(item in {"FAIL", "ERROR"} for item in statuses),
        "inconclusive_count": sum(item == "INCONCLUSIVE" for item in statuses),
        "cases": case_reports,
        "runtime_coupling_coverage": coverage,
        "claim_scope": "sealed_paired_run_one_factor_basilisk_causal_evidence_with_explicit_runtime_coupling_coverage",
        "implementation_evidence": {
            "suite_module_sha256": _sha256(Path(__file__).resolve()),
            "causality_module_sha256": _sha256(Path(__file__).with_name("coupling_causality.py").resolve()),
        },
    }
    if environment_copy is not None:
        suite_report["environment_evidence"] = {
            "path": environment_copy.relative_to(suite_root).as_posix(),
            "sha256": _sha256(environment_copy),
            "source_evidence_sha256": environment_source_sha256,
            "payload": _read_json(environment_copy),
        }
    report_path = suite_root / "coupling_causality_suite_report.json"
    report_path.write_text(json.dumps(suite_report, ensure_ascii=False, indent=2), encoding="utf-8")
    manifest_files = [
        matrix_copy, baseline_copy, report_path, shared_baseline_spec_path,
        suite_root / "shared_baseline_execution.log",
        *sorted((suite_root / "cases").rglob("case_report.json")),
        *sorted((suite_root / "cases").rglob("*_task_spec.yaml")),
        *sorted((suite_root / "cases").rglob("*_execution.log")),
        *sorted(suite_root.rglob("SEALED.json")),
        *sorted(suite_root.rglob("bundle_manifest.json")),
        *sorted(suite_root.rglob("run_record.json")),
        *sorted(suite_root.rglob("validation_outcome.json")),
        *sorted(suite_root.rglob("summary.json")),
    ]
    if environment_copy is not None:
        manifest_files.append(environment_copy)
    # Preserve order while removing duplicate paths.
    manifest_files = list(dict.fromkeys(path.resolve() for path in manifest_files))
    manifest = {
        "schema_version": "v0572a.coupling-validation-evidence-manifest.v1",
        "generated_at": _now(),
        "files": {
            path.relative_to(suite_root).as_posix(): {
                "size_bytes": path.stat().st_size,
                "sha256": _sha256(path),
            }
            for path in manifest_files if path.is_file()
        },
    }
    (suite_root / "evidence_manifest.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
    return CouplingValidationSuiteResult(
        status=overall,
        report_path=str(report_path),
        case_count=len(case_reports),
        pass_count=suite_report["pass_count"],
        fail_count=suite_report["fail_count"],
        inconclusive_count=suite_report["inconclusive_count"],
    )


__all__ = [
    "RunAcceptancePolicy", "CouplingValidationCase", "CouplingValidationSuiteResult",
    "validate_one_factor_change", "run_coupling_validation_suite",
]
