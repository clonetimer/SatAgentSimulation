"""Durable, hash-verified rotation for continuous interactive telemetry.

The live `telemetry.jsonl` remains the active append file. Closed segments are
immutable numbered JSONL files, with an atomic index recording their hashes.
The logical journal is closed segments followed by the active file; every row
keeps the original global sequence and previous_hash chain.
"""
from __future__ import annotations

import hashlib
import json
import os
import shutil
from pathlib import Path
from typing import Any, Iterator


INDEX_SCHEMA = "interactive-telemetry-segments.v1"
SEGMENTS_DIR = "telemetry_segments"
INDEX_NAME = "index.json"


def _digest(path: Path) -> str:
    checksum = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            checksum.update(chunk)
    return checksum.hexdigest()


def _index(root: Path) -> list[dict[str, Any]]:
    folder = root / SEGMENTS_DIR
    index_path = folder / INDEX_NAME
    actual = sorted(path.name for path in folder.glob("segment-*.jsonl")) if folder.exists() else []
    if not index_path.exists():
        if actual:
            raise ValueError("TELEMETRY_SEGMENT_INDEX_MISSING")
        return []
    obj = json.loads(index_path.read_text(encoding="utf-8"))
    if obj.get("schema_version") != INDEX_SCHEMA or not isinstance(obj.get("segments"), list):
        raise ValueError("TELEMETRY_SEGMENT_INDEX_INVALID")
    entries = obj["segments"]
    expected = [f"segment-{i:08d}.jsonl" for i in range(1, len(entries) + 1)]
    if actual != expected or [entry.get("filename") for entry in entries] != expected:
        raise ValueError("TELEMETRY_SEGMENT_SET_MISMATCH")
    return entries


def files(root: Path) -> list[Path]:
    """Closed segments followed by the active journal, even when active is empty."""
    root = Path(root)
    entries = _index(root)
    paths = [root / SEGMENTS_DIR / str(entry["filename"]) for entry in entries]
    active = root / "telemetry.jsonl"
    if active.exists():
        paths.append(active)
    return paths


def total_bytes(root: Path) -> int:
    return sum(path.stat().st_size for path in files(root))


def roll_active(root: Path) -> Path:
    """Close the current journal and atomically register its digest.

    If index publication fails after rename, inspection refuses the workspace
    rather than silently losing an unregistered segment. No log is deleted.
    """
    root = Path(root)
    active = root / "telemetry.jsonl"
    if not active.exists() or active.stat().st_size == 0:
        raise ValueError("TELEMETRY_ROTATION_EMPTY_ACTIVE")
    entries = _index(root)
    folder = root / SEGMENTS_DIR
    folder.mkdir(parents=True, exist_ok=True)
    destination = folder / f"segment-{len(entries)+1:08d}.jsonl"
    if destination.exists():
        raise ValueError("TELEMETRY_SEGMENT_ALREADY_EXISTS")
    size = active.stat().st_size
    checksum = _digest(active)
    active.rename(destination)
    entries.append({
        "filename": destination.name,
        "bytes": size,
        "sha256": checksum,
    })
    index_path = folder / INDEX_NAME
    temporary = folder / (INDEX_NAME + ".tmp")
    try:
        with temporary.open("w", encoding="utf-8") as handle:
            json.dump({"schema_version": INDEX_SCHEMA, "segments": entries}, handle, sort_keys=True)
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        temporary.replace(index_path)
    finally:
        if temporary.exists():
            temporary.unlink()
    return destination


def iter_entries(root: Path) -> Iterator[dict[str, Any]]:
    """Verify every segment digest and the cross-file global hash/sequence chain."""
    root = Path(root)
    entries = _index(root)
    previous_hash = "0" * 64
    sequence = 0
    for index, path in enumerate(files(root)):
        checksum = hashlib.sha256()
        byte_count = 0
        with path.open("rb") as handle:
            for raw in handle:
                checksum.update(raw)
                byte_count += len(raw)
                row = json.loads(raw)
                entry_hash = str(row.pop("entry_hash"))
                encoded = json.dumps(row, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
                if (int(row.get("sequence", -1)) != sequence
                        or row.get("previous_hash") != previous_hash
                        or hashlib.sha256(encoded).hexdigest() != entry_hash):
                    raise ValueError("TELEMETRY_HASH_CHAIN_INVALID")
                row["entry_hash"] = entry_hash
                previous_hash = entry_hash
                sequence += 1
                yield row
        if index < len(entries):
            expected = entries[index]
            if byte_count != expected["bytes"] or checksum.hexdigest() != expected["sha256"]:
                raise ValueError("TELEMETRY_SEGMENT_DIGEST_MISMATCH")


def logical_sha256(root: Path) -> str:
    digest = hashlib.sha256()
    for path in files(root):
        with path.open("rb") as handle:
            for chunk in iter(lambda: handle.read(1024 * 1024), b""):
                digest.update(chunk)
    return digest.hexdigest()


def copy_logical(root: Path, destination: Path) -> None:
    """Copy all physical segments to a legacy-compatible logical JSONL artifact."""
    destination.parent.mkdir(parents=True, exist_ok=True)
    with destination.open("wb") as target:
        for path in files(root):
            with path.open("rb") as source:
                shutil.copyfileobj(source, target, length=1024 * 1024)


def segments_metadata(root: Path) -> list[dict[str, Any]]:
    return list(_index(Path(root)))
