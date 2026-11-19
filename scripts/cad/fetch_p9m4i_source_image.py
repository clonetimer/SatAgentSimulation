#!/usr/bin/env python3
#!/usr/bin/env python3
"""P9-M4-I: hash-backed NASA Europa Clipper star-tracker mock-up source intake.

用途：下载并封存原始 JPEG，记录来源、字节数、尺寸与 SHA-256。
参数：--output-dir 输出目录；不执行图像预处理或 TripoSR 推理。
输出：原始 JPEG 与 source-intake.json；该来源仍需项目用途确认和人工背景分割后才能进入生成 runner。
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable
from urllib.parse import urlparse
from urllib.request import Request, urlopen

SOURCE_ID = "nasa_europa_clipper_star_tracker_mockup_2025"
SOURCE_PAGE_URI = (
    "https://science.nasa.gov/missions/europa-clipper/"
    "europa-clipper-resources/3d-printed-star-tracker-mock-up/"
)
SOURCE_IMAGE_URI = (
    "https://assets.science.nasa.gov/content/dam/science/missions/"
    "europa-clipper/images/Star-Tracker-3D-Print-Full.JPG"
)
USAGE_POLICY_URI = "https://www.nasa.gov/nasa-brand-center/images-and-media/"
SOURCE_CREDIT = "NASA/JPL-Caltech"
MAX_SOURCE_BYTES = 20 * 1024 * 1024
EXPECTED_SOURCE_SHA256 = "458cd3da892e5d0e75272fc9ee3b931b2c73f8eedecb03caa8d6bd21409fdfad"
USER_AGENT = "SatAgentSimulation-P9M4I-source-intake/1.0"


def sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def jpeg_dimensions(data: bytes) -> tuple[int, int]:
    """Read JPEG SOF dimensions without Pillow or other third-party packages."""
    if len(data) < 4 or not data.startswith(b"\xff\xd8"):
        raise ValueError("P9M4I_SOURCE_JPEG_SIGNATURE_INVALID")
    # Some media pipelines append metadata/padding after the JPEG EOI marker;
    # require the marker in the byte stream, not specifically in the final 32 bytes.
    if b"\xff\xd9" not in data:
        raise ValueError("P9M4I_SOURCE_JPEG_EOI_MISSING")

    sof_markers = {
        0xC0, 0xC1, 0xC2, 0xC3, 0xC5, 0xC6, 0xC7,
        0xC9, 0xCA, 0xCB, 0xCD, 0xCE, 0xCF,
    }
    offset = 2
    while offset < len(data):
        if data[offset] != 0xFF:
            offset += 1
            continue
        while offset < len(data) and data[offset] == 0xFF:
            offset += 1
        if offset >= len(data):
            break
        marker = data[offset]
        offset += 1
        if marker in {0xD8, 0xD9, 0x01} or 0xD0 <= marker <= 0xD7:
            continue
        if offset + 2 > len(data):
            raise ValueError("P9M4I_SOURCE_JPEG_SEGMENT_TRUNCATED")
        segment_length = int.from_bytes(data[offset : offset + 2], "big")
        if segment_length < 2 or offset + segment_length > len(data):
            raise ValueError("P9M4I_SOURCE_JPEG_SEGMENT_INVALID")
        if marker in sof_markers:
            if segment_length < 8:
                raise ValueError("P9M4I_SOURCE_JPEG_SOF_INVALID")
            height = int.from_bytes(data[offset + 3 : offset + 5], "big")
            width = int.from_bytes(data[offset + 5 : offset + 7], "big")
            if width <= 0 or height <= 0:
                raise ValueError("P9M4I_SOURCE_JPEG_DIMENSIONS_INVALID")
            return width, height
        if marker == 0xDA:
            break
        offset += segment_length
    raise ValueError("P9M4I_SOURCE_JPEG_SOF_NOT_FOUND")


def _read_response(response: Any) -> tuple[bytes, str, str]:
    status = getattr(response, "status", 200)
    if status != 200:
        raise ValueError(f"P9M4I_SOURCE_HTTP_STATUS_INVALID:{status}")
    headers = getattr(response, "headers", {})
    content_type = str(headers.get("Content-Type", "")).split(";", 1)[0].strip().lower()
    if content_type != "image/jpeg":
        raise ValueError(f"P9M4I_SOURCE_CONTENT_TYPE_INVALID:{content_type or 'missing'}")
    final_url = response.geturl() if hasattr(response, "geturl") else SOURCE_IMAGE_URI
    parsed = urlparse(final_url)
    expected = urlparse(SOURCE_IMAGE_URI)
    if (
        parsed.scheme != "https"
        or parsed.hostname != "assets.science.nasa.gov"
        or parsed.path != expected.path
        or parsed.query != expected.query
    ):
        raise ValueError("P9M4I_SOURCE_REDIRECT_TARGET_INVALID")

    chunks: list[bytes] = []
    total = 0
    while True:
        block = response.read(1024 * 1024)
        if not block:
            break
        total += len(block)
        if total > MAX_SOURCE_BYTES:
            raise ValueError("P9M4I_SOURCE_IMAGE_TOO_LARGE")
        chunks.append(block)
    data = b"".join(chunks)
    if not data:
        raise ValueError("P9M4I_SOURCE_IMAGE_EMPTY")
    return data, content_type, final_url


def fetch_nasa_star_tracker_source(
    output_dir: str | Path,
    *,
    opener: Callable[..., Any] = urlopen,
    expected_sha256: str = EXPECTED_SOURCE_SHA256,
) -> dict[str, Any]:
    """Fetch the exact NASA media asset and preserve a hash-backed intake receipt.

    This is source acquisition only. It does not approve project use, remove the
    background, create a TripoSR input manifest, or start model inference.
    """
    destination = Path(output_dir).resolve()
    destination.mkdir(parents=True, exist_ok=True)
    image_path = destination / "nasa-europa-clipper-star-tracker-mockup.jpg"
    receipt_path = destination / "source-intake.json"
    if image_path.exists() or receipt_path.exists():
        raise ValueError("P9M4I_SOURCE_INTAKE_OUTPUT_ALREADY_EXISTS")

    request = Request(
        SOURCE_IMAGE_URI,
        headers={"User-Agent": USER_AGENT, "Accept": "image/jpeg"},
    )
    with opener(request, timeout=45) as response:
        data, content_type, final_url = _read_response(response)
    width, height = jpeg_dimensions(data)
    digest = sha256_bytes(data)
    if digest != expected_sha256:
        raise ValueError("P9M4I_SOURCE_SHA256_NOT_PINNED")

    receipt = {
        "schema_version": "p9m4i-source-intake.v1",
        "source_id": SOURCE_ID,
        "source_page_uri": SOURCE_PAGE_URI,
        "source_image_uri": SOURCE_IMAGE_URI,
        "final_image_uri": final_url,
        "source_credit": SOURCE_CREDIT,
        "source_description": (
            "3D-printed mock-up of Europa Clipper stellar reference unit optical "
            "head and shielding; used for fit/tool-access checks, not flight hardware geometry."
        ),
        "published_asset_date": "2025-05-29",
        "retrieved_at_utc": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
        "media_type": content_type,
        "file_name": image_path.name,
        "size_bytes": len(data),
        "width_px": width,
        "height_px": height,
        "sha256": digest,
        "sha256_pinned": True,
        "source_rights_assessment": {
            "status": "policy_basis_found_pending_project_owner_signoff",
            "usage_policy_uri": USAGE_POLICY_URI,
            "candidate_permitted_uses": ["model_generation", "internal_preview"],
            "external_distribution_allowed": False,
            "source_specific_notes": [
                "NASA's current media guidance explicitly includes media used in 3D-model rendition for educational or informational purposes.",
                "The source page credits NASA/JPL-Caltech and does not display a separate third-party copyright notice.",
                "Use must remain factual and non-commercial; do not use NASA/JPL logos or imply NASA/JPL/Caltech endorsement.",
                "The project owner must confirm this intended use before the TripoSR runner manifest is marked approved.",
            ],
            "source_page_credit": SOURCE_CREDIT,
            "human_signoff_required": True,
        },
        "preprocessing": {
            "background_removed": False,
            "status": "not_started",
            "review_required": (
                "Crop/segment only the mock-up; exclude the visible hand/person and workbench background."
            ),
        },
        "inference_eligible": False,
        "limitations": [
            "generic Europa Clipper mock-up, not the target satellite's confirmed star-tracker model",
            "single perspective image provides no engineering scale, mounting interface, aperture dimensions or boresight",
            "raw source intake is not a TripoSR input manifest and does not authorize inference by itself",
        ],
    }

    temporary_path: Path | None = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="wb", dir=destination, prefix=".p9m4i-source-", delete=False
        ) as stream:
            temporary_path = Path(stream.name)
            stream.write(data)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary_path, image_path)
        temporary_path = None
        receipt_path.write_text(
            json.dumps(receipt, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
    except Exception:
        if temporary_path is not None:
            temporary_path.unlink(missing_ok=True)
        image_path.unlink(missing_ok=True)
        receipt_path.unlink(missing_ok=True)
        raise
    return receipt


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output-dir", type=Path, required=True)
    args = parser.parse_args()
    try:
        receipt = fetch_nasa_star_tracker_source(args.output_dir)
    except Exception as exc:
        parser.exit(2, f"P9-M4-I source intake rejected: {exc}\n")
    print(json.dumps(receipt, ensure_ascii=False, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
