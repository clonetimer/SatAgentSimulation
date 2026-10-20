"""Append-only, losslessly compressed telemetry segments for continuous sessions.

The immutable, global telemetry hash chain remains on *uncompressed* JSONL rows.
A v1 index supports a mix of legacy .jsonl and verified .jsonl.gz segments.
Compression replaces a closed segment only after verifying compressed and
uncompressed checksums; interrupted transitions are fail-closed and explicitly
repairable. No code silently skips, truncates or overwrites evidence.
"""
from __future__ import annotations

import gzip
import hashlib
import json
import os
from pathlib import Path
from typing import Any, Iterator


INDEX_SCHEMA = "interactive-telemetry-segments.v1"
SEGMENTS_DIR = "telemetry_segments"
INDEX_NAME = "index.json"
_CHUNK = 1024 * 1024


def _digest(path: Path) -> str:
    checksum = hashlib.sha256()
    with path.open("rb") as source:
        for chunk in iter(lambda: source.read(_CHUNK), b""):
            checksum.update(chunk)
    return checksum.hexdigest()


def _sync_dir(path: Path) -> None:
    # Best effort directory entry durability on POSIX; Windows lacks O_DIRECTORY.
    try:
        fd = os.open(path, os.O_RDONLY | getattr(os, "O_DIRECTORY", 0))
    except OSError:
        return
    try:
        os.fsync(fd)
    except OSError:
        pass
    finally:
        os.close(fd)


def _write_index(root: Path, entries: list[dict[str, Any]]) -> None:
    folder = root / SEGMENTS_DIR
    folder.mkdir(parents=True, exist_ok=True)
    target = folder / INDEX_NAME
    temporary = folder / (INDEX_NAME + ".tmp")
    if temporary.exists():
        raise ValueError("TELEMETRY_SEGMENT_INDEX_STAGING_EXISTS")
    with temporary.open("w", encoding="utf-8") as handle:
        json.dump({"schema_version": INDEX_SCHEMA, "segments": entries}, handle, sort_keys=True)
        handle.write("\n")
        handle.flush()
        os.fsync(handle.fileno())
    temporary.replace(target)
    _sync_dir(folder)


def _filename(number: int, compressed: bool) -> str:
    return f"segment-{number:08d}.jsonl" + (".gz" if compressed else "")


def _index(root: Path) -> list[dict[str, Any]]:
    folder = Path(root) / SEGMENTS_DIR
    index_path = folder / INDEX_NAME
    actual = sorted(p.name for p in folder.glob("segment-*.jsonl*")) if folder.exists() else []
    if not index_path.exists():
        if actual:
            raise ValueError("TELEMETRY_SEGMENT_INDEX_MISSING")
        return []
    obj = json.loads(index_path.read_text(encoding="utf-8"))
    if obj.get("schema_version") != INDEX_SCHEMA or not isinstance(obj.get("segments"), list):
        raise ValueError("TELEMETRY_SEGMENT_INDEX_INVALID")
    entries = obj["segments"]
    expected = []
    for number, entry in enumerate(entries, 1):
        if not isinstance(entry, dict):
            raise ValueError("TELEMETRY_SEGMENT_INDEX_INVALID")
        name = entry.get("filename")
        if name not in (_filename(number, False), _filename(number, True)):
            raise ValueError("TELEMETRY_SEGMENT_INDEX_INVALID")
        if not isinstance(entry.get("sha256"), str) or not isinstance(entry.get("bytes"), int):
            raise ValueError("TELEMETRY_SEGMENT_INDEX_INVALID")
        if name.endswith(".gz") and (
            not isinstance(entry.get("raw_sha256"), str)
            or not isinstance(entry.get("raw_bytes"), int)
        ):
            raise ValueError("TELEMETRY_SEGMENT_INDEX_INVALID")
        expected.append(name)
    if actual != sorted(expected):
        # Includes the interim state where both raw and compressed copies exist.
        raise ValueError("TELEMETRY_SEGMENT_SET_MISMATCH")
    return entries


def files(root: Path) -> list[Path]:
    root = Path(root)
    entries = _index(root)
    result = [root / SEGMENTS_DIR / entry["filename"] for entry in entries]
    active = root / "telemetry.jsonl"
    if active.exists():
        result.append(active)
    return result


def total_bytes(root: Path) -> int:
    """Physical bytes of stored journal segments, not external staging or bundle."""
    return sum(p.stat().st_size for p in files(root))


def total_logical_bytes(root: Path) -> int:
    """Uncompressed journal bytes; quota is never reset by compression."""
    root = Path(root)
    entries = _index(root)
    active = root / "telemetry.jsonl"
    return sum(int(e.get("raw_bytes", e["bytes"])) for e in entries) + (
        active.stat().st_size if active.exists() else 0
    )


def roll_active(root: Path) -> Path:
    root = Path(root)
    active = root / "telemetry.jsonl"
    if not active.exists() or active.stat().st_size == 0:
        raise ValueError("TELEMETRY_ROTATION_EMPTY_ACTIVE")
    entries = _index(root)
    folder = root / SEGMENTS_DIR
    folder.mkdir(parents=True, exist_ok=True)
    destination = folder / _filename(len(entries) + 1, False)
    if destination.exists():
        raise ValueError("TELEMETRY_SEGMENT_ALREADY_EXISTS")
    size = active.stat().st_size
    checksum = _digest(active)
    active.rename(destination)
    _sync_dir(folder)
    entries.append({"filename": destination.name, "bytes": size, "sha256": checksum})
    _write_index(root, entries)
    return destination


def _raw_chunks(path: Path) -> Iterator[bytes]:
    opener = gzip.open if path.name.endswith(".gz") else open
    with opener(path, "rb") as source:
        for chunk in iter(lambda: source.read(_CHUNK), b""):
            yield chunk


def _verify_compressed(path: Path, entry: dict[str, Any]) -> None:
    if path.stat().st_size != entry["bytes"] or _digest(path) != entry["sha256"]:
        raise ValueError("TELEMETRY_SEGMENT_DIGEST_MISMATCH")


def _plain_chunks(root: Path) -> Iterator[bytes]:
    """Stream and verify all source bytes; compressed files are verified twice."""
    entries = _index(root)
    for index, path in enumerate(files(root)):
        entry = entries[index] if index < len(entries) else None
        if entry is not None and path.name.endswith(".gz"):
            _verify_compressed(path, entry)
        digest = hashlib.sha256()
        length = 0
        try:
            for chunk in _raw_chunks(path):
                length += len(chunk)
                digest.update(chunk)
                yield chunk
        except (OSError, EOFError) as exc:
            raise ValueError("TELEMETRY_SEGMENT_DECOMPRESSION_FAILED") from exc
        if entry is not None:
            expected_hash = entry.get("raw_sha256", entry["sha256"])
            expected_size = entry.get("raw_bytes", entry["bytes"])
            if length != expected_size or digest.hexdigest() != expected_hash:
                raise ValueError("TELEMETRY_SEGMENT_DIGEST_MISMATCH")


def iter_entries(root: Path) -> Iterator[dict[str, Any]]:
    """Verify global sequence and hash chain across compressed/plain file borders."""
    from io import BytesIO

    previous_hash = "0" * 64
    sequence = 0
    # Reading chunks rather than gzip.readline also enforces bounded decompression.
    pending = b""
    for chunk in _plain_chunks(Path(root)):
        pending += chunk
        parts = pending.split(b"\n")
        pending = parts.pop()
        for raw in parts:
            if not raw:
                raise ValueError("TELEMETRY_HASH_CHAIN_INVALID")
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
    if pending:
        raise ValueError("TELEMETRY_HASH_CHAIN_TRUNCATED")


def logical_sha256(root: Path) -> str:
    digest = hashlib.sha256()
    for chunk in _plain_chunks(Path(root)):
        digest.update(chunk)
    return digest.hexdigest()


def copy_logical(root: Path, destination: Path) -> None:
    destination.parent.mkdir(parents=True, exist_ok=True)
    with destination.open("wb") as target:
        for chunk in _plain_chunks(Path(root)):
            target.write(chunk)


def segments_metadata(root: Path) -> list[dict[str, Any]]:
    return list(_index(Path(root)))


def _verify_raw_and_compressed(raw: Path, packed: Path) -> tuple[int, str, int, str]:
    raw_bytes = raw.stat().st_size
    raw_hash = _digest(raw)
    compressed_bytes = packed.stat().st_size
    compressed_hash = _digest(packed)
    if compressed_bytes <= 0:
        raise ValueError("TELEMETRY_SEGMENT_COMPRESSION_EMPTY")
    checksum = hashlib.sha256()
    size = 0
    try:
        for chunk in _raw_chunks(packed):
            checksum.update(chunk)
            size += len(chunk)
    except (OSError, EOFError) as exc:
        raise ValueError("TELEMETRY_SEGMENT_DECOMPRESSION_FAILED") from exc
    if size != raw_bytes or checksum.hexdigest() != raw_hash:
        raise ValueError("TELEMETRY_SEGMENT_COMPRESSION_VERIFY_FAILED")
    return raw_bytes, raw_hash, compressed_bytes, compressed_hash


def compress_closed(root: Path, *, max_segments: int | None = None) -> dict[str, int]:
    """Offline only: replace closed plaintext segments with verified gzip copies.

    The original is deleted only after the new file is validated and its
    authoritative index committed. Interrupted file/index publication is
    intentionally fail-closed, and repair_interrupted_compression() can
    discard the duplicate copy after proving its bytes identical.
    """
    root = Path(root)
    if max_segments is not None and max_segments < 1:
        raise ValueError("max_segments must be positive")
    entries = _index(root)
    compressed = 0
    before = total_bytes(root)
    for offset, previous in enumerate(entries):
        if previous["filename"].endswith(".gz"):
            continue
        if max_segments is not None and compressed >= max_segments:
            break
        folder = root / SEGMENTS_DIR
        raw = folder / previous["filename"]
        packed = folder / (previous["filename"] + ".gz")
        temp = folder / (".compress-" + previous["filename"] + ".tmp")
        if packed.exists() or temp.exists():
            raise ValueError("TELEMETRY_COMPRESSION_STAGING_EXISTS")
        # Compression must never mutate the input bytes or normalize JSONL.
        with raw.open("rb") as source, temp.open("wb") as dest:
            with gzip.GzipFile(filename="", mode="wb", fileobj=dest, mtime=0) as zipped:
                for chunk in iter(lambda: source.read(_CHUNK), b""):
                    zipped.write(chunk)
            dest.flush()
            os.fsync(dest.fileno())
        _verify_raw_and_compressed(raw, temp)
        temp.replace(packed)
        _sync_dir(folder)
        raw_size, raw_hash, zipped_size, zipped_hash = _verify_raw_and_compressed(raw, packed)
        entry = {
            "filename": packed.name,
            "bytes": zipped_size,
            "sha256": zipped_hash,
            "raw_bytes": raw_size,
            "raw_sha256": raw_hash,
        }
        if raw_hash != previous["sha256"] or raw_size != previous["bytes"]:
            raise ValueError("TELEMETRY_SEGMENT_DIGEST_MISMATCH")
        entries[offset] = entry
        _write_index(root, entries)
        raw.unlink()
        _sync_dir(folder)
        compressed += 1
    return {"compressed": compressed, "bytes_before": before, "bytes_after": total_bytes(root)}


def repair_interrupted_compression(root: Path) -> int:
    """Explicitly reconcile duplicate old/new compressed copies, never skip data.

    This operation does not reconstruct lost segments or repair broken
    hash chains. It only removes a redundant copy after both forms have
    independently verified the same uncompressed SHA-256 and length.
    """
    root = Path(root)
    folder = root / SEGMENTS_DIR
    index_path = folder / INDEX_NAME
    if not index_path.exists():
        raise ValueError("TELEMETRY_SEGMENT_INDEX_MISSING")
    obj = json.loads(index_path.read_text(encoding="utf-8"))
    if obj.get("schema_version") != INDEX_SCHEMA:
        raise ValueError("TELEMETRY_SEGMENT_INDEX_INVALID")
    entries = obj["segments"]
    repaired = 0
    for number, entry in enumerate(entries, 1):
        if entry["filename"] not in (_filename(number, False), _filename(number, True)):
            raise ValueError("TELEMETRY_SEGMENT_INDEX_INVALID")
        raw = folder / _filename(number, False)
        packed = folder / _filename(number, True)
        if not raw.exists() or not packed.exists():
            continue
        raw_len, raw_hash, zipped_len, zipped_hash = _verify_raw_and_compressed(raw, packed)
        if entry["filename"].endswith(".gz"):
            if (entry.get("raw_sha256") != raw_hash
                    or entry.get("raw_bytes") != raw_len
                    or entry["sha256"] != zipped_hash
                    or entry["bytes"] != zipped_len):
                raise ValueError("TELEMETRY_COMPRESSION_INDEX_CONFLICT")
            raw.unlink()
        else:
            if entry["sha256"] != raw_hash or entry["bytes"] != raw_len:
                raise ValueError("TELEMETRY_COMPRESSION_INDEX_CONFLICT")
            packed.unlink()
        repaired += 1
    _sync_dir(folder)
    _index(root)
    return repaired
