"""Campaign expansion and execution utilities for TaskSpec workflows.

A campaign TaskSpec is a *generator* for child TaskSpecs.  The child specs are
normal TaskSpecs, so the existing validate -> compile -> run pipeline remains
the single source of truth.  This module is intentionally import-safe without
Basilisk; only child tasks that require Basilisk will fail at execution time.
"""
from __future__ import annotations

import copy
import csv
import itertools
import json
import random
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable, Mapping, MutableMapping, Sequence

from .task_spec import DATASET_MANIFEST_VERSION, TASK_SPEC_VERSION, TaskSpecError, spec_sha256, write_json


@dataclass(frozen=True)
class CampaignCase:
    """One expanded child TaskSpec in a campaign."""

    index: int
    case_id: str
    spec: dict[str, Any]
    parameters: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class CampaignPlan:
    """Deterministic expansion result for a campaign TaskSpec."""

    campaign_id: str
    campaign_hash: str
    sampling: str
    cases: tuple[CampaignCase, ...]

    @property
    def case_count(self) -> int:
        return len(self.cases)

    def to_dict(self) -> dict[str, Any]:
        return {
            "campaign_id": self.campaign_id,
            "campaign_hash": self.campaign_hash,
            "sampling": self.sampling,
            "case_count": self.case_count,
            "cases": [case.to_dict() for case in self.cases],
        }


@dataclass(frozen=True)
class CampaignRunResult:
    """Result of executing a campaign."""

    plan: CampaignPlan
    output_root: Path
    summary: dict[str, Any]
    case_rows: tuple[dict[str, Any], ...]
    manifest: dict[str, Any]
    files: dict[str, str]

    def to_dict(self) -> dict[str, Any]:
        return {
            "campaign_id": self.plan.campaign_id,
            "output_root": str(self.output_root),
            "summary": self.summary,
            "case_rows": list(self.case_rows),
            "files": dict(self.files),
            "manifest": dict(self.manifest),
        }


def _now_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def _campaign_block(spec: Mapping[str, Any]) -> Mapping[str, Any]:
    block = spec.get("campaign")
    return block if isinstance(block, Mapping) else {}


def _outputs(spec: Mapping[str, Any]) -> Mapping[str, Any]:
    block = spec.get("outputs")
    return block if isinstance(block, Mapping) else {}


def _simulation(spec: Mapping[str, Any]) -> Mapping[str, Any]:
    block = spec.get("simulation")
    return block if isinstance(block, Mapping) else {}


def _parse_path(path: str) -> list[str | int]:
    """Parse dotted paths with optional list indexes, e.g. faults[0].magnitude."""

    if not isinstance(path, str) or not path.strip():
        raise TaskSpecError("patch path must be a non-empty string")
    tokens: list[str | int] = []
    for raw_part in path.split("."):
        part = raw_part.strip()
        if not part:
            raise TaskSpecError(f"invalid empty segment in patch path: {path!r}")
        while "[" in part:
            prefix, rest = part.split("[", 1)
            if prefix:
                tokens.append(prefix)
            idx_text, rest = rest.split("]", 1)
            if not idx_text.isdigit():
                raise TaskSpecError(f"list index must be non-negative integer in patch path: {path!r}")
            tokens.append(int(idx_text))
            part = rest
        if part:
            tokens.append(part)
    return tokens


def get_by_path(data: Mapping[str, Any] | Sequence[Any], path: str, default: Any = None) -> Any:
    """Get a value from a nested object using TaskSpec patch path syntax."""

    cur: Any = data
    for token in _parse_path(path):
        if isinstance(token, int):
            if not isinstance(cur, Sequence) or isinstance(cur, (str, bytes)) or token >= len(cur):
                return default
            cur = cur[token]
        else:
            if not isinstance(cur, Mapping) or token not in cur:
                return default
            cur = cur[token]
    return cur


def set_by_path(data: MutableMapping[str, Any] | list[Any], path: str, value: Any) -> None:
    """Set a value using dotted/list-index path syntax, creating mappings as needed."""

    tokens = _parse_path(path)
    if not tokens:
        raise TaskSpecError("patch path cannot be empty")
    cur: Any = data
    for i, token in enumerate(tokens[:-1]):
        nxt = tokens[i + 1]
        if isinstance(token, int):
            if not isinstance(cur, list):
                raise TaskSpecError(f"path segment [{token}] requires a list in {path!r}")
            while len(cur) <= token:
                cur.append({} if isinstance(nxt, str) else [])
            if cur[token] is None:
                cur[token] = {} if isinstance(nxt, str) else []
            cur = cur[token]
        else:
            if not isinstance(cur, MutableMapping):
                raise TaskSpecError(f"path segment {token!r} requires a mapping in {path!r}")
            if token not in cur or cur[token] is None:
                cur[token] = {} if isinstance(nxt, str) else []
            cur = cur[token]
    last = tokens[-1]
    if isinstance(last, int):
        if not isinstance(cur, list):
            raise TaskSpecError(f"final path segment [{last}] requires a list in {path!r}")
        while len(cur) <= last:
            cur.append(None)
        cur[last] = value
    else:
        if not isinstance(cur, MutableMapping):
            raise TaskSpecError(f"final path segment {last!r} requires a mapping in {path!r}")
        cur[last] = value


def apply_overrides(spec: Mapping[str, Any], overrides: Mapping[str, Any] | None) -> dict[str, Any]:
    """Return a deep-copied spec with path-based overrides applied."""

    out = copy.deepcopy(dict(spec))
    for path, value in (overrides or {}).items():
        set_by_path(out, str(path), value)
    return out


def _case_id(campaign_id: str, index: int, fmt: str | None = None, manual_id: str | None = None) -> str:
    if manual_id:
        return manual_id
    fmt = fmt or "{campaign_id}_case_{index:04d}"
    return fmt.format(campaign_id=campaign_id, task_id=campaign_id, index=index)


def _base_child_spec(campaign_spec: Mapping[str, Any]) -> dict[str, Any]:
    block = _campaign_block(campaign_spec)
    base = block.get("base_spec")
    if not isinstance(base, Mapping):
        raise TaskSpecError("campaign.base_spec must be provided and must be an object")
    child = copy.deepcopy(dict(base))
    child.setdefault("schema_version", TASK_SPEC_VERSION)
    child.setdefault("simulation", copy.deepcopy(dict(_simulation(campaign_spec))))
    child.setdefault("outputs", {"output_root": "datasets/_campaign_child", "trace_format": "csv", "include_summary": True, "include_trace": True, "include_labels": True, "include_manifest": True})
    return child


def _set_child_identity(child: dict[str, Any], *, campaign_id: str, case_id: str, index: int, campaign_output_root: str | None) -> None:
    child["task_id"] = case_id
    metadata = child.setdefault("metadata", {})
    if isinstance(metadata, MutableMapping):
        metadata.update({"campaign_id": campaign_id, "campaign_case_index": index})
    sim = child.setdefault("simulation", {})
    preserve_seed = bool(metadata.get("campaign_preserve_seed")) if isinstance(metadata, Mapping) else False
    if isinstance(sim, MutableMapping) and not preserve_seed:
        seed = sim.get("seed")
        if seed is None:
            seed = 0
        if isinstance(seed, int) and seed >= 0:
            sim["seed"] = seed + index
    outputs = child.setdefault("outputs", {})
    if isinstance(outputs, MutableMapping):
        outputs.setdefault("trace_format", "csv")
        outputs.setdefault("include_summary", True)
        outputs.setdefault("include_trace", True)
        outputs.setdefault("include_labels", True)
        outputs.setdefault("include_manifest", True)
        if campaign_output_root:
            outputs["output_root"] = str(Path(campaign_output_root) / "cases" / case_id)
        else:
            outputs.setdefault("output_root", str(Path("datasets") / campaign_id / "cases" / case_id))


def _manual_cases(campaign_spec: Mapping[str, Any], base: Mapping[str, Any]) -> list[tuple[str | None, dict[str, Any], dict[str, Any]]]:
    block = _campaign_block(campaign_spec)
    cases = block.get("cases") or []
    out: list[tuple[str | None, dict[str, Any], dict[str, Any]]] = []
    if not isinstance(cases, list):
        raise TaskSpecError("campaign.cases must be a list")
    for item in cases:
        if not isinstance(item, Mapping):
            raise TaskSpecError("each manual campaign case must be an object")
        case_id = item.get("case_id")
        overrides = item.get("overrides", item.get("patch", {}))
        if overrides is None:
            overrides = {}
        if not isinstance(overrides, Mapping):
            raise TaskSpecError("campaign case overrides must be an object")
        spec = apply_overrides(base, overrides)
        out.append((str(case_id) if case_id else None, spec, dict(overrides)))
    return out


def _sweep_cases(campaign_spec: Mapping[str, Any], base: Mapping[str, Any]) -> list[tuple[str | None, dict[str, Any], dict[str, Any]]]:
    block = _campaign_block(campaign_spec)
    sweeps = block.get("parameter_sweeps") or []
    if not isinstance(sweeps, list) or not sweeps:
        raise TaskSpecError("grid campaigns require campaign.parameter_sweeps")
    paths: list[str] = []
    value_lists: list[list[Any]] = []
    for item in sweeps:
        if not isinstance(item, Mapping):
            raise TaskSpecError("each parameter_sweep must be an object")
        path = item.get("path")
        values = item.get("values")
        if not isinstance(path, str) or not path.strip():
            raise TaskSpecError("parameter_sweep.path is required")
        if not isinstance(values, list) or not values:
            raise TaskSpecError(f"parameter_sweep.values for {path!r} must be a non-empty list")
        paths.append(path)
        value_lists.append(list(values))
    out: list[tuple[str | None, dict[str, Any], dict[str, Any]]] = []
    for values in itertools.product(*value_lists):
        overrides = dict(zip(paths, values))
        out.append((None, apply_overrides(base, overrides), overrides))
    return out


def _sample_value(rng: random.Random, item: Mapping[str, Any], *, index: int, count: int, lhs_bucket: int | None = None) -> Any:
    distribution = str(item.get("distribution", "uniform"))
    if distribution == "choice":
        choices = item.get("choices")
        if not isinstance(choices, list) or not choices:
            raise TaskSpecError("choice randomization requires non-empty choices")
        return rng.choice(choices)
    if distribution == "randint":
        lo = item.get("min")
        hi = item.get("max")
        if not isinstance(lo, int) or not isinstance(hi, int) or lo > hi:
            raise TaskSpecError("randint randomization requires integer min <= max")
        return rng.randint(lo, hi)
    if distribution == "normal":
        mean = item.get("mean")
        std = item.get("std")
        if not isinstance(mean, (int, float)) or isinstance(mean, bool) or not isinstance(std, (int, float)) or isinstance(std, bool) or std < 0:
            raise TaskSpecError("normal randomization requires numeric mean and non-negative std")
        return rng.gauss(float(mean), float(std))
    if distribution == "uniform":
        lo = item.get("min")
        hi = item.get("max")
        if not isinstance(lo, (int, float)) or isinstance(lo, bool) or not isinstance(hi, (int, float)) or isinstance(hi, bool) or lo > hi:
            raise TaskSpecError("uniform randomization requires numeric min <= max")
        if lhs_bucket is not None and count > 0:
            u = (lhs_bucket + rng.random()) / count
            return float(lo) + (float(hi) - float(lo)) * u
        return rng.uniform(float(lo), float(hi))
    raise TaskSpecError(f"unsupported randomization distribution: {distribution!r}")


def _random_cases(campaign_spec: Mapping[str, Any], base: Mapping[str, Any], *, lhs: bool = False) -> list[tuple[str | None, dict[str, Any], dict[str, Any]]]:
    block = _campaign_block(campaign_spec)
    sim = _simulation(campaign_spec)
    count = block.get("count")
    if not isinstance(count, int) or count <= 0:
        raise TaskSpecError("random/lhs campaigns require campaign.count > 0")
    randomizations = block.get("randomizations") or []
    if not isinstance(randomizations, list) or not randomizations:
        raise TaskSpecError("random/lhs campaigns require campaign.randomizations")
    seed = sim.get("seed", 0)
    rng = random.Random(seed if isinstance(seed, int) else 0)

    lhs_orders: dict[str, list[int]] = {}
    if lhs:
        for item in randomizations:
            if isinstance(item, Mapping) and item.get("path"):
                order = list(range(count))
                rng.shuffle(order)
                lhs_orders[str(item["path"])] = order

    out: list[tuple[str | None, dict[str, Any], dict[str, Any]]] = []
    for index in range(count):
        overrides: dict[str, Any] = {}
        for item in randomizations:
            if not isinstance(item, Mapping):
                raise TaskSpecError("each randomization must be an object")
            path = item.get("path")
            if not isinstance(path, str) or not path.strip():
                raise TaskSpecError("randomization.path is required")
            bucket = lhs_orders.get(path, [index])[index] if lhs else None
            overrides[path] = _sample_value(rng, item, index=index, count=count, lhs_bucket=bucket)
        out.append((None, apply_overrides(base, overrides), overrides))
    return out


def expand_campaign_spec(campaign_spec: Mapping[str, Any], *, output_root: str | Path | None = None) -> CampaignPlan:
    """Expand a campaign TaskSpec into deterministic child TaskSpecs."""

    if campaign_spec.get("task_type") != "campaign":
        raise TaskSpecError("expand_campaign_spec requires task_type='campaign'")
    campaign_id = str(campaign_spec.get("task_id") or "campaign")
    block = _campaign_block(campaign_spec)
    sampling = str(block.get("sampling", "manual"))
    base = _base_child_spec(campaign_spec)
    campaign_output_root = str(output_root or _outputs(campaign_spec).get("output_root") or "")

    if sampling == "manual":
        raw_cases = _manual_cases(campaign_spec, base)
    elif sampling == "grid":
        raw_cases = _sweep_cases(campaign_spec, base)
    elif sampling == "random":
        raw_cases = _random_cases(campaign_spec, base, lhs=False)
    elif sampling == "lhs":
        raw_cases = _random_cases(campaign_spec, base, lhs=True)
    else:
        raise TaskSpecError(f"unsupported campaign.sampling: {sampling!r}")

    max_cases = block.get("max_cases")
    if isinstance(max_cases, int) and max_cases > 0:
        raw_cases = raw_cases[:max_cases]
    expected_count = block.get("count")
    if isinstance(expected_count, int) and sampling in {"grid", "manual"}:
        raw_cases = raw_cases[:expected_count]

    fmt = block.get("case_id_format")
    child_cases: list[CampaignCase] = []
    for index, (manual_case_id, child, parameters) in enumerate(raw_cases):
        case_id = _case_id(campaign_id, index, fmt=str(fmt) if fmt else None, manual_id=manual_case_id)
        _set_child_identity(child, campaign_id=campaign_id, case_id=case_id, index=index, campaign_output_root=campaign_output_root or None)
        child_cases.append(CampaignCase(index=index, case_id=case_id, spec=child, parameters=parameters))

    return CampaignPlan(
        campaign_id=campaign_id,
        campaign_hash=spec_sha256(campaign_spec),
        sampling=sampling,
        cases=tuple(child_cases),
    )


def write_campaign_plan(path: str | Path, plan: CampaignPlan) -> Path:
    """Write an expanded campaign plan JSON file."""

    return write_json(path, plan.to_dict())


def _write_case_index(path: Path, rows: Sequence[Mapping[str, Any]]) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    fieldnames: list[str] = []
    for row in rows:
        for key in row.keys():
            if key not in fieldnames:
                fieldnames.append(str(key))
    with path.open("w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames or ["case_id"])
        writer.writeheader()
        for row in rows:
            writer.writerow({key: row.get(key) for key in fieldnames})
    return path


def _safe_summary_fields(summary: Mapping[str, Any]) -> dict[str, Any]:
    flat: dict[str, Any] = {}
    for key, value in summary.items():
        if isinstance(value, (str, int, float, bool)) or value is None:
            flat[f"summary.{key}"] = value
    return flat


def build_campaign_manifest(
    *,
    campaign_spec: Mapping[str, Any],
    plan: CampaignPlan,
    output_root: Path,
    files: Mapping[str, str],
    case_rows: Sequence[Mapping[str, Any]],
) -> dict[str, Any]:
    """Build a manifest for a multi-case campaign dataset."""

    success_count = sum(1 for row in case_rows if row.get("status") == "complete")
    failure_count = sum(1 for row in case_rows if row.get("status") != "complete")
    return {
        "manifest_version": DATASET_MANIFEST_VERSION,
        "dataset_id": str(campaign_spec.get("task_id", plan.campaign_id)),
        "created_at": _now_iso(),
        "status": "complete" if failure_count == 0 else "partial",
        "task": {
            "task_id": str(campaign_spec.get("task_id", plan.campaign_id)),
            "task_type": "campaign",
            "schema_version": str(campaign_spec.get("schema_version", TASK_SPEC_VERSION)),
            "spec_hash": spec_sha256(campaign_spec),
        },
        "run": {
            "scenario_count": len(case_rows),
            "success_count": success_count,
            "failure_count": failure_count,
            "sampling": plan.sampling,
        },
        "files": dict(files),
        "cases": [dict(row) for row in case_rows],
        "provenance": {
            "generator": "sat_sim.campaign.run_campaign_spec",
            "campaign_hash": plan.campaign_hash,
            "output_root": str(output_root),
        },
    }


def run_campaign_spec(
    campaign_spec: Mapping[str, Any],
    *,
    output_root: str | Path | None = None,
    continue_on_error: bool = True,
    dry_run: bool = False,
) -> CampaignRunResult:
    """Expand and optionally execute a campaign TaskSpec.

    Child tasks are executed through the unified model execution port.  Failures are
    recorded per case; set ``continue_on_error=False`` to fail fast.
    """

    from .task_compiler import compile_task_spec
    from .unified_execution import execute_compiled_task
    from .task_validator import validate_task_spec

    root = Path(output_root or _outputs(campaign_spec).get("output_root") or Path("datasets") / str(campaign_spec.get("task_id", "campaign")))
    plan = expand_campaign_spec(campaign_spec, output_root=root)
    root.mkdir(parents=True, exist_ok=True)

    files: dict[str, str] = {}
    campaign_spec_path = root / "campaign_task_spec.json"
    write_json(campaign_spec_path, campaign_spec)
    files["campaign_task_spec"] = str(campaign_spec_path.relative_to(root))
    plan_path = root / "campaign_plan.json"
    write_campaign_plan(plan_path, plan)
    files["campaign_plan"] = str(plan_path.relative_to(root))

    case_rows: list[dict[str, Any]] = []
    for case in plan.cases:
        case_dir = root / "cases" / case.case_id
        row: dict[str, Any] = {
            "case_index": case.index,
            "case_id": case.case_id,
            "task_type": case.spec.get("task_type"),
            "status": "planned" if dry_run else "pending",
            "output_root": str(case_dir.relative_to(root)),
            "spec_hash": spec_sha256(case.spec),
            "parameters_json": json.dumps(case.parameters, ensure_ascii=False, sort_keys=True),
        }
        try:
            validation = validate_task_spec(case.spec)
            if not validation.ok:
                row["status"] = "validation_failed"
                row["error"] = "; ".join(f"{err.path}: {err.message}" for err in validation.errors)
                if not continue_on_error:
                    raise TaskSpecError(row["error"])
            elif dry_run:
                row["status"] = "planned"
            else:
                compiled = compile_task_spec(case.spec, validate=False)
                result = execute_compiled_task(compiled, task_spec=case.spec, output_root=case_dir, write_dataset=True)
                row["status"] = "complete"
                row["dataset_manifest"] = str((case_dir / "manifest.json").relative_to(root))
                row.update(_safe_summary_fields(result.summary))
        except Exception as exc:
            row["status"] = "failed"
            row["error"] = str(exc)
            if not continue_on_error:
                case_rows.append(row)
                raise
        case_rows.append(row)

    case_index_path = root / "case_index.csv"
    _write_case_index(case_index_path, case_rows)
    files["case_index"] = str(case_index_path.relative_to(root))

    summary = {
        "campaign_id": plan.campaign_id,
        "case_count": len(case_rows),
        "success_count": sum(1 for row in case_rows if row.get("status") == "complete"),
        "failure_count": sum(1 for row in case_rows if row.get("status") not in {"complete", "planned"}),
        "planned_count": sum(1 for row in case_rows if row.get("status") == "planned"),
        "sampling": plan.sampling,
        "dry_run": dry_run,
    }
    summary_path = root / "campaign_summary.json"
    write_json(summary_path, summary)
    files["campaign_summary"] = str(summary_path.relative_to(root))

    manifest = build_campaign_manifest(campaign_spec=campaign_spec, plan=plan, output_root=root, files=files, case_rows=case_rows)
    manifest_path = root / "manifest.json"
    write_json(manifest_path, manifest)
    files["manifest"] = str(manifest_path.relative_to(root))
    manifest["files"] = dict(files)
    write_json(manifest_path, manifest)

    return CampaignRunResult(
        plan=plan,
        output_root=root,
        summary=summary,
        case_rows=tuple(case_rows),
        manifest=manifest,
        files=files,
    )


__all__ = [
    "CampaignCase",
    "CampaignPlan",
    "CampaignRunResult",
    "apply_overrides",
    "build_campaign_manifest",
    "expand_campaign_spec",
    "get_by_path",
    "run_campaign_spec",
    "set_by_path",
    "write_campaign_plan",
]
