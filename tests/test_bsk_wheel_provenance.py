"""Issue #37: attest satfix1 provenance before any Basilisk wheel installation.

Synthetic archives here test validation logic only; they NEVER represent
Basilisk physics. The release workflow separately downloads official wheels
by pinned SHA-256 and verifies real native binary imports on both OSes.
"""
from __future__ import annotations

import base64
import csv
import hashlib
import io
import json
from pathlib import Path
import sys
import zipfile

import pytest

SCRIPTS = Path(__file__).resolve().parents[1] / "scripts"
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

from bsk_wheel_provenance import verify_approved_wheel, REPORT
from build_bsk_compat_wheels import (
    EXPECTED_ORIGINAL_SHA256, OLD_REQUIREMENT, NEW_REQUIREMENT,
    sha256,
)

SRC = "bsk-2.11.0-cp39-abi3-win_amd64.whl"
TARGET = "bsk-2.11.0+satfix1-cp39-abi3-win_amd64.whl"


def _make_wheel(root: Path, *, metadata=None, omit_record=False):
    dst = root / TARGET
    files = {
        "Basilisk/__init__.py": b"__version__ = '2.11.0'\n",
        "bsk-2.11.0+satfix1.dist-info/METADATA": (
            metadata or (
                "Metadata-Version: 2.3\nName: bsk\nVersion: 2.11.0+satfix1\n"
                + NEW_REQUIREMENT + "\n"
            )
        ).encode(),
        "bsk-2.11.0+satfix1.dist-info/WHEEL":
            b"Wheel-Version: 1.0\nTag: cp39-abi3-win_amd64\n",
    }
    record = "bsk-2.11.0+satfix1.dist-info/RECORD"
    if not omit_record:
        buf = io.StringIO()
        writer = csv.writer(buf, lineterminator="\n")
        for name, data in sorted(files.items()):
            digest = base64.urlsafe_b64encode(hashlib.sha256(data).digest()).rstrip(b"=").decode()
            writer.writerow([name, "sha256=" + digest, str(len(data))])
        writer.writerow([record, "", ""])
        files[record] = buf.getvalue().encode()
    with zipfile.ZipFile(dst, "w", compression=zipfile.ZIP_DEFLATED) as z:
        for name, data in files.items():
            z.writestr(name, data)
    return dst


def _make_report(root, wheel, **updates):
    report = {
        "schema_version": "sat-sim.bsk-compat-wheel-build.v1",
        "status": "PASS",
        "scope": "distribution metadata only; Basilisk payload bytes unchanged",
        "results": [{
            "source": SRC,
            "source_sha256": EXPECTED_ORIGINAL_SHA256[SRC],
            "target": wheel.name,
            "target_sha256": sha256(wheel),
            "original_version": "2.11.0",
            "patched_version": "2.11.0+satfix1",
            "metadata_change": {"from": OLD_REQUIREMENT, "to": NEW_REQUIREMENT},
            "payload_file_count": 1,
            "payload_bytes_unchanged": True,
        }],
    }
    report["results"][0].update(updates)
    (root / REPORT).write_text(json.dumps(report), encoding="utf-8")


def test_verified_local_compatibility_wheel_passes_no_install(tmp_path):
    wheel = _make_wheel(tmp_path)
    _make_report(tmp_path, wheel)
    result = verify_approved_wheel(tmp_path, platform_key="windows_x86_64")
    assert result["filename"] == TARGET
    assert result["sha256"] == sha256(wheel)
    assert result["record_verified"] is True
    assert result["metadata_only_change"] is True
    assert result["native_restart_supported"] is False


@pytest.mark.parametrize("tamper", [
    {"source_sha256": "0"*64}, {"payload_bytes_unchanged": False},
    {"patched_version": "2.11.0"}, {"target": "../bsk.whl"},
    {"metadata_change": {"from": OLD_REQUIREMENT, "to": "pillow"}},
])
def test_untrusted_manifest_cannot_pass_validation(tmp_path, tamper):
    wheel = _make_wheel(tmp_path)
    _make_report(tmp_path, wheel, **tamper)
    with pytest.raises((ValueError, FileNotFoundError), match="BSK_"):
        verify_approved_wheel(tmp_path, platform_key="windows_x86_64")


def test_corrupt_wheel_sha_rejected_before_any_install(tmp_path):
    wheel = _make_wheel(tmp_path)
    _make_report(tmp_path, wheel)
    with wheel.open("ab") as f:
        f.write(b"tampered archive")
    with pytest.raises(ValueError, match="BSK_WHEEL_SHA256_MISMATCH"):
        verify_approved_wheel(tmp_path, platform_key="windows_x86_64")


def test_record_content_tamper_rejected_even_if_manifest_sha_recomputed(tmp_path):
    wheel = _make_wheel(tmp_path)
    _make_report(tmp_path, wheel)
    with zipfile.ZipFile(wheel, "a") as z:
        z.writestr("Basilisk/__init__.py", b"evil")
    _make_report(tmp_path, wheel)
    with pytest.raises(ValueError, match="BSK_WHEEL_MISSING_OR_DUPLICATE_METADATA"):
        verify_approved_wheel(tmp_path, platform_key="windows_x86_64")


def test_missing_record_rejected(tmp_path):
    wheel = _make_wheel(tmp_path, omit_record=True)
    _make_report(tmp_path, wheel)
    with pytest.raises(ValueError, match="BSK_WHEEL_MISSING_OR_DUPLICATE_METADATA"):
        verify_approved_wheel(tmp_path, platform_key="windows_x86_64")


def test_mismatched_actual_wheel_metadata_rejected(tmp_path):
    wheel = _make_wheel(tmp_path, metadata=(
        "Metadata-Version: 2.3\nName: bsk\nVersion: 2.11.0+satfix1\n"
        + OLD_REQUIREMENT + "\n"
    ))
    _make_report(tmp_path, wheel)
    with pytest.raises(ValueError, match="BSK_WHEEL_METADATA_POLICY_MISMATCH"):
        verify_approved_wheel(tmp_path, platform_key="windows_x86_64")


def test_reject_linux_selection_when_only_windows_wheel_exists(tmp_path):
    wheel = _make_wheel(tmp_path)
    _make_report(tmp_path, wheel)
    with pytest.raises(ValueError, match="BSK_PLATFORM_WHEEL_AMBIGUOUS_OR_MISSING"):
        verify_approved_wheel(tmp_path, platform_key="linux_x86_64")
