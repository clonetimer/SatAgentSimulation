"""Command-line management for the scoped experience reservoir."""
from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any, Sequence

from .experience import ExperienceScope, ExperienceStore
from .experience_lessons import ExperienceLessonService


def _scope(args: argparse.Namespace) -> ExperienceScope:
    return ExperienceScope(tenant_id=args.tenant, project_id=args.project)


def _print(value: Any) -> None:
    print(json.dumps(value, ensure_ascii=False, sort_keys=True, indent=2))


def _add_scope(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--tenant", required=True)
    parser.add_argument("--project", required=True)


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="sat-experience")
    parser.add_argument("--store", type=Path, required=True)
    commands = parser.add_subparsers(dest="command", required=True)

    capture = commands.add_parser("capture")
    _add_scope(capture)
    capture.add_argument("--manifest", type=Path, required=True)
    capture.add_argument("--actor", default="system")

    capture_bundle = commands.add_parser("capture-bundle")
    _add_scope(capture_bundle)
    capture_bundle.add_argument("--bundle", type=Path, required=True)
    request_source = capture_bundle.add_mutually_exclusive_group(required=True)
    request_source.add_argument("--request")
    request_source.add_argument("--request-file", type=Path)
    capture_bundle.add_argument("--actor", default="system")
    capture_bundle.add_argument("--model")
    capture_bundle.add_argument("--model-base-url")
    capture_bundle.add_argument("--environment-tag", action="append", default=[])

    listing = commands.add_parser("list")
    _add_scope(listing)
    listing.add_argument("--include-revoked", action="store_true")

    search = commands.add_parser("search")
    _add_scope(search)
    search.add_argument("--capability-id")
    search.add_argument("--mode")
    search.add_argument("--effect")
    search.add_argument("--error-code")
    search.add_argument("--validation-result")
    search.add_argument("--trust-level", choices=("raw", "verified", "approved", "revoked"))
    search.add_argument("--include-revoked", action="store_true")
    search.add_argument("--limit", type=int, default=100)

    inspect = commands.add_parser("inspect")
    _add_scope(inspect)
    inspect.add_argument("experience_id")

    verify = commands.add_parser("verify")
    _add_scope(verify)
    verify.add_argument("experience_id")

    revoke = commands.add_parser("revoke")
    _add_scope(revoke)
    revoke.add_argument("experience_id")
    revoke.add_argument("--actor", required=True)
    revoke.add_argument("--reason", required=True)

    purge = commands.add_parser("purge-expired")
    _add_scope(purge)
    purge.add_argument("--actor", required=True)
    purge.add_argument("--as-of")

    backup = commands.add_parser("backup")
    backup.add_argument("archive", type=Path)

    restore = commands.add_parser("restore")
    restore.add_argument("archive", type=Path)

    lesson_compile = commands.add_parser("lesson-compile")
    _add_scope(lesson_compile)
    lesson_compile.add_argument("--experience-id", action="append", required=True)
    lesson_compile.add_argument("--actor", required=True)

    lesson_evaluate = commands.add_parser("lesson-evaluate")
    _add_scope(lesson_evaluate)
    lesson_evaluate.add_argument("lesson_id")
    lesson_evaluate.add_argument("--evaluator", required=True)
    lesson_evaluate.add_argument("--held-out-case", action="append", required=True)
    lesson_evaluate.add_argument("--baseline-metrics", type=Path, required=True)
    lesson_evaluate.add_argument("--candidate-metrics", type=Path, required=True)
    lesson_evaluate.add_argument("--evidence", type=Path, required=True)

    lesson_review = commands.add_parser("lesson-review")
    _add_scope(lesson_review)
    lesson_review.add_argument("lesson_id")
    lesson_review.add_argument("--reviewer", required=True)
    lesson_review.add_argument("--decision", choices=("APPROVE", "REJECT"), required=True)
    lesson_review.add_argument("--reason", required=True)

    lesson_revoke = commands.add_parser("lesson-revoke")
    _add_scope(lesson_revoke)
    lesson_revoke.add_argument("lesson_id")
    lesson_revoke.add_argument("--actor", required=True)
    lesson_revoke.add_argument("--reason", required=True)

    lesson_snapshot = commands.add_parser("lesson-snapshot")
    _add_scope(lesson_snapshot)
    lesson_snapshot.add_argument("--actor", required=True)

    lesson_retrieve = commands.add_parser("lesson-retrieve")
    _add_scope(lesson_retrieve)
    lesson_retrieve.add_argument("--capability-id", required=True)
    lesson_retrieve.add_argument("--mode")
    lesson_retrieve.add_argument("--task-spec-version", default="1.0.0")
    lesson_retrieve.add_argument("--limit", type=int, default=10)

    lesson_reuse = commands.add_parser("lesson-reuse")
    _add_scope(lesson_reuse)
    lesson_reuse.add_argument("--enabled", action=argparse.BooleanOptionalAction, required=True)
    lesson_reuse.add_argument("--actor", required=True)

    lesson_rollback = commands.add_parser("lesson-rollback")
    _add_scope(lesson_rollback)
    lesson_rollback.add_argument("snapshot_id")
    lesson_rollback.add_argument("--actor", required=True)
    return parser


def _capture(store: ExperienceStore, args: argparse.Namespace) -> int:
    manifest = json.loads(args.manifest.read_text(encoding="utf-8"))
    if not isinstance(manifest, dict):
        raise ValueError("capture manifest must be a JSON object")
    request_text = manifest.pop("request_text")
    validation_result = manifest.pop("validation_result")
    artifacts = manifest.pop("artifacts", {})
    record, created = store.capture(
        request_text=request_text,
        scope=_scope(args),
        validation_result=validation_result,
        artifacts=artifacts,
        actor=args.actor,
        **manifest,
    )
    _print({"created": created, "record": record.model_dump(mode="json")})
    return 0


def main(argv: Sequence[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    if args.command == "restore":
        restored = ExperienceStore.restore(args.archive, args.store)
        _print({"restored": True, "store": str(restored.root)})
        return 0

    store = ExperienceStore(args.store)
    lessons = ExperienceLessonService(store)
    if args.command == "capture":
        return _capture(store, args)
    if args.command == "capture-bundle":
        request_text = (
            args.request_file.read_text(encoding="utf-8")
            if args.request_file is not None
            else args.request
        )
        record, created = store.capture_run_bundle(
            args.bundle,
            request_text=request_text,
            scope=_scope(args),
            actor=args.actor,
            model_identity={
                key: value
                for key, value in {
                    "model": args.model,
                    "base_url": args.model_base_url,
                }.items()
                if value
            },
            environment_tags=args.environment_tag,
        )
        _print({"created": created, "record": record.model_dump(mode="json")})
        return 0
    if args.command == "list":
        _print(store.list(scope=_scope(args), include_revoked=args.include_revoked))
        return 0
    if args.command == "search":
        _print(
            store.search(
                scope=_scope(args),
                capability_id=args.capability_id,
                mode=args.mode,
                effect=args.effect,
                error_code=args.error_code,
                validation_result=args.validation_result,
                trust_level=args.trust_level,
                include_revoked=args.include_revoked,
                limit=args.limit,
            )
        )
        return 0
    if args.command == "inspect":
        _print(store.get(args.experience_id, scope=_scope(args)).model_dump(mode="json"))
        return 0
    if args.command == "verify":
        result = store.verify(args.experience_id, scope=_scope(args))
        _print(result)
        return 0 if result["ok"] else 1
    if args.command == "revoke":
        store.revoke(
            args.experience_id,
            scope=_scope(args),
            actor=args.actor,
            reason=args.reason,
        )
        _print({"experience_id": args.experience_id, "revoked": True})
        return 0
    if args.command == "purge-expired":
        _print(
            store.purge_expired_revoked(
                scope=_scope(args),
                actor=args.actor,
                as_of=args.as_of,
            )
        )
        return 0
    if args.command == "backup":
        _print({"archive": str(store.backup(args.archive)), "backed_up": True})
        return 0
    if args.command == "lesson-compile":
        lesson, created = lessons.compile(
            args.experience_id,
            scope=_scope(args),
            actor=args.actor,
        )
        _print({"created": created, "lesson": lesson.model_dump(mode="json")})
        return 0
    if args.command == "lesson-evaluate":
        _print(
            lessons.evaluate(
                args.lesson_id,
                scope=_scope(args),
                evaluator=args.evaluator,
                held_out_case_ids=args.held_out_case,
                baseline_metrics=json.loads(args.baseline_metrics.read_text(encoding="utf-8")),
                candidate_metrics=json.loads(args.candidate_metrics.read_text(encoding="utf-8")),
                evaluation_evidence=json.loads(args.evidence.read_text(encoding="utf-8")),
            )
        )
        return 0
    if args.command == "lesson-review":
        lesson = lessons.review(
            args.lesson_id,
            scope=_scope(args),
            reviewer=args.reviewer,
            decision=args.decision,
            reason=args.reason,
        )
        _print(lesson.model_dump(mode="json"))
        return 0
    if args.command == "lesson-revoke":
        lesson = lessons.revoke(
            args.lesson_id,
            scope=_scope(args),
            actor=args.actor,
            reason=args.reason,
        )
        _print(lesson.model_dump(mode="json"))
        return 0
    if args.command == "lesson-snapshot":
        _print(lessons.create_snapshot(scope=_scope(args), actor=args.actor))
        return 0
    if args.command == "lesson-retrieve":
        _print(
            [
                lesson.model_dump(mode="json")
                for lesson in lessons.retrieve_approved(
                    scope=_scope(args),
                    capability_id=args.capability_id,
                    mode=args.mode,
                    task_spec_version=args.task_spec_version,
                    limit=args.limit,
                )
            ]
        )
        return 0
    if args.command == "lesson-reuse":
        lessons.set_reuse_enabled(
            scope=_scope(args),
            enabled=args.enabled,
            actor=args.actor,
        )
        _print({"enabled": args.enabled})
        return 0
    if args.command == "lesson-rollback":
        lessons.rollback_snapshot(
            args.snapshot_id,
            scope=_scope(args),
            actor=args.actor,
        )
        _print({"active_snapshot_id": args.snapshot_id, "enabled": True})
        return 0
    raise AssertionError(args.command)


if __name__ == "__main__":
    raise SystemExit(main())
