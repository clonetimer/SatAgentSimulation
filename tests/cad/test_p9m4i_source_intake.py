from __future__ import annotations

import hashlib
import importlib.util
import json
from pathlib import Path

import pytest
import yaml

_SOURCE_SCRIPT = (
    Path(__file__).resolve().parents[2]
    / "scripts" / "cad" / "fetch_p9m4i_source_image.py"
)
_WORKFLOW_PATH = (
    Path(__file__).resolve().parents[2]
    / ".github" / "workflows" / "p9m4i-synthetic-assets.yml"
)
_SPEC = importlib.util.spec_from_file_location("p9m4i_source_intake_test_module", _SOURCE_SCRIPT)
assert _SPEC is not None and _SPEC.loader is not None
_SOURCE_MODULE = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(_SOURCE_MODULE)
MAX_SOURCE_BYTES = _SOURCE_MODULE.MAX_SOURCE_BYTES
fetch_nasa_star_tracker_source = _SOURCE_MODULE.fetch_nasa_star_tracker_source
jpeg_dimensions = _SOURCE_MODULE.jpeg_dimensions


def _minimal_jpeg(width: int = 16, height: int = 12) -> bytes:
    # SOI + one baseline SOF0 segment + EOI; enough for deterministic
    # signature/dimension tests without checking in a NASA source image.
    frame = (
        bytes([8])
        + height.to_bytes(2, "big")
        + width.to_bytes(2, "big")
        + bytes([1, 1, 0x11, 0])
    )
    return (
        b"\xff\xd8"
        + b"\xff\xc0"
        + (len(frame) + 2).to_bytes(2, "big")
        + frame
        + b"\xff\xd9"
    )


class _Response:
    def __init__(self, payload: bytes, *, content_type: str = "image/jpeg", url: str | None = None):
        self.payload = payload
        self.headers = {"Content-Type": content_type}
        self.status = 200
        self.url = url or (
            "https://assets.science.nasa.gov/content/dam/science/missions/"
            "europa-clipper/images/Star-Tracker-3D-Print-Full.JPG"
        )
        self.offset = 0

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return False

    def geturl(self):
        return self.url

    def read(self, size: int = -1) -> bytes:
        if size < 0:
            size = len(self.payload) - self.offset
        block = self.payload[self.offset : self.offset + size]
        self.offset += len(block)
        return block


def test_jpeg_dimensions_reads_sof0_dimensions():
    assert jpeg_dimensions(_minimal_jpeg(640, 480)) == (640, 480)


def test_jpeg_dimensions_allows_metadata_after_eoi():
    assert jpeg_dimensions(_minimal_jpeg(640, 480) + (b"metadata" * 16)) == (640, 480)


@pytest.mark.parametrize(
    ("payload", "message"),
    [
        (b"not-a-jpeg", "P9M4I_SOURCE_JPEG_SIGNATURE_INVALID"),
        (b"\xff\xd8\xff\xd9", "P9M4I_SOURCE_JPEG_SOF_NOT_FOUND"),
    ],
)
def test_jpeg_dimensions_rejects_invalid_or_incomplete_payload(payload, message):
    with pytest.raises(ValueError, match=message):
        jpeg_dimensions(payload)


def test_fetch_nasa_star_tracker_source_writes_original_bytes_and_receipt(tmp_path):
    payload = _minimal_jpeg(1000, 750)

    def opener(request, timeout):
        assert request.full_url.endswith("Star-Tracker-3D-Print-Full.JPG")
        assert request.get_header("User-agent").startswith("SatAgentSimulation-P9M4I")
        assert timeout == 45
        return _Response(payload)

    receipt = fetch_nasa_star_tracker_source(
        tmp_path,
        opener=opener,
        expected_sha256=hashlib.sha256(payload).hexdigest(),
    )
    image = tmp_path / "nasa-europa-clipper-star-tracker-mockup.jpg"
    metadata = json.loads((tmp_path / "source-intake.json").read_text(encoding="utf-8"))

    assert image.read_bytes() == payload
    assert receipt == metadata
    assert receipt["size_bytes"] == len(payload)
    assert receipt["width_px"] == 1000
    assert receipt["height_px"] == 750
    assert receipt["sha256"] == hashlib.sha256(payload).hexdigest()
    assert receipt["source_bytes_retained"] is True
    assert receipt["source_credit"] == "NASA/JPL-Caltech"
    assert receipt["source_rights_assessment"]["status"] == (
        "policy_basis_found_pending_project_owner_signoff"
    )
    assert receipt["source_rights_assessment"]["candidate_permitted_uses"] == [
        "model_generation",
        "internal_preview",
    ]
    assert receipt["source_rights_assessment"]["external_distribution_allowed"] is False
    assert receipt["preprocessing"]["background_removed"] is False
    assert receipt["inference_eligible"] is False


def test_fetch_rejects_source_digest_not_matching_pinned_revision(tmp_path):
    payload = _minimal_jpeg()

    def opener(_request, timeout):
        return _Response(payload)

    with pytest.raises(ValueError, match="P9M4I_SOURCE_SHA256_NOT_PINNED"):
        fetch_nasa_star_tracker_source(
            tmp_path,
            opener=opener,
            expected_sha256="0" * 64,
        )
    assert not list(tmp_path.iterdir())


def test_fetch_rejects_non_jpeg_content_type(tmp_path):
    def opener(_request, timeout):
        return _Response(_minimal_jpeg(), content_type="application/octet-stream")

    with pytest.raises(ValueError, match="P9M4I_SOURCE_CONTENT_TYPE_INVALID"):
        fetch_nasa_star_tracker_source(tmp_path, opener=opener)
    assert not list(tmp_path.iterdir())


def test_fetch_rejects_redirect_outside_pinned_nasa_asset_host(tmp_path):
    def opener(_request, timeout):
        return _Response(
            _minimal_jpeg(),
            url="https://example.invalid/Star-Tracker-3D-Print-Full.JPG",
        )

    with pytest.raises(ValueError, match="P9M4I_SOURCE_REDIRECT_TARGET_INVALID"):
        fetch_nasa_star_tracker_source(tmp_path, opener=opener)


def test_fetch_rejects_redirect_to_different_path_on_same_host(tmp_path):
    def opener(_request, timeout):
        return _Response(
            _minimal_jpeg(),
            url="https://assets.science.nasa.gov/content/dam/other-image.jpg",
        )

    with pytest.raises(ValueError, match="P9M4I_SOURCE_REDIRECT_TARGET_INVALID"):
        fetch_nasa_star_tracker_source(tmp_path, opener=opener)


def test_fetch_rejects_source_larger_than_limit(tmp_path):
    payload = _minimal_jpeg() + (b"x" * (MAX_SOURCE_BYTES + 1))

    def opener(_request, timeout):
        return _Response(payload)

    with pytest.raises(ValueError, match="P9M4I_SOURCE_IMAGE_TOO_LARGE"):
        fetch_nasa_star_tracker_source(tmp_path, opener=opener)
    assert not list(tmp_path.iterdir())


def test_fetch_refuses_to_overwrite_existing_source_evidence(tmp_path):
    (tmp_path / "source-intake.json").write_text("{}", encoding="utf-8")

    def opener(_request, timeout):
        raise AssertionError("network must not be called when output exists")

    with pytest.raises(ValueError, match="P9M4I_SOURCE_INTAKE_OUTPUT_ALREADY_EXISTS"):
        fetch_nasa_star_tracker_source(tmp_path, opener=opener)



def test_fetch_receipt_only_mode_does_not_persist_original_source(tmp_path):
    payload = _minimal_jpeg(1000, 750)

    def opener(_request, timeout):
        assert timeout == 45
        return _Response(payload)

    receipt = fetch_nasa_star_tracker_source(
        tmp_path,
        opener=opener,
        expected_sha256=hashlib.sha256(payload).hexdigest(),
        retain_source_bytes=False,
    )
    image = tmp_path / "nasa-europa-clipper-star-tracker-mockup.jpg"
    metadata_path = tmp_path / "source-intake.json"
    metadata = json.loads(metadata_path.read_text(encoding="utf-8"))

    assert not image.exists()
    assert metadata_path.is_file()
    assert receipt == metadata
    assert receipt["source_bytes_retained"] is False
    assert receipt["size_bytes"] == len(payload)
    assert receipt["width_px"] == 1000
    assert receipt["height_px"] == 750
    assert receipt["sha256"] == hashlib.sha256(payload).hexdigest()
    assert receipt["inference_eligible"] is False


def test_public_source_intake_workflow_uploads_metadata_receipt_only():
    workflow = yaml.safe_load(_WORKFLOW_PATH.read_text(encoding="utf-8"))
    steps = workflow["jobs"]["nasa-source-intake"]["steps"]
    fetch_step = next(
        step for step in steps
        if step.get("name", "").startswith("Fetch and hash NASA source")
    )
    assert "--receipt-only" in fetch_step["run"]

    uploads = [
        step for step in steps
        if str(step.get("uses", "")).startswith("actions/upload-artifact@")
    ]
    assert len(uploads) == 1
    upload = uploads[0]["with"]
    assert upload["name"] == "p9m4i-nasa-star-tracker-intake-receipt"
    assert upload["path"] == "reports/p9m4i/source-intake/source-intake.json"
    assert upload["retention-days"] == 7

    cleanup_step = next(
        step for step in steps
        if step.get("name") == "Verify superseded public artifacts are deleted when marked deleted"
    )
    assert "actions/artifacts/" in cleanup_step["run"]
    assert 'status == "delete_required"' in cleanup_step["run"]
