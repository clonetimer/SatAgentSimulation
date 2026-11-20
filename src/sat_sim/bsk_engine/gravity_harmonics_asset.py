"""Exact-version GGM03S coefficient verification for Basilisk 2.11.0.

No synthetic J3/J4 terms: all data originate in the unmodified Basilisk
release asset. The Git blob identity pins all bytes; SHA-256 is emitted for
runtime audit. The loader never performs network access.
"""
from __future__ import annotations

import csv
from hashlib import sha1, sha256
import io
import math
from pathlib import Path

SOURCE_REVISION = "6c1511f5385dccaa73c9ae24522a74adc24fe623"
SOURCE_SIZE_BYTES = 1_350_695
SOURCE_BLOB_SHA1 = "6022af24f8aaeba461eb6a3c22330265578a8710"
SOURCE_URL = (
    "https://raw.githubusercontent.com/AVSLab/basilisk/"
    + SOURCE_REVISION + "/supportData/LocalGravData/GGM03S.txt"
)
GRAVITY_CHOICES = {
    "legacy_j2_only": 2,
    "ggm03s_degree2": 2,
    "ggm03s_degree3": 3,
    "ggm03s_degree4": 4,
}


def inspect_ggm03s(path: Path) -> dict[str, object]:
    """Fail closed on missing, replaced, sparse or unnormalised full GGM03S."""
    path = Path(path).expanduser().resolve()
    if not path.is_file():
        raise FileNotFoundError("NATIVE_GGM03S_FULL_ASSET_MISSING")
    if path.stat().st_size != SOURCE_SIZE_BYTES:
        raise ValueError("NATIVE_GGM03S_SIZE_MISMATCH")
    data = path.read_bytes()
    blob_sha = sha1(b"blob " + str(len(data)).encode("ascii") + b"\0" + data).hexdigest()
    if blob_sha != SOURCE_BLOB_SHA1:
        raise ValueError("NATIVE_GGM03S_PINNED_BLOB_SHA1_MISMATCH")
    digest = sha256(data).hexdigest()
    try:
        reader = csv.reader(io.StringIO(data.decode("ascii")))
        header = next(reader)
        radius_m = float(header[0])
        gravitational_mu = float(header[1])
        declared_max_degree, declared_max_order = int(header[3]), int(header[4])
        normalized = int(header[5])
        reference_lon, reference_lat = float(header[6]), float(header[7])
        if not (
            math.isclose(radius_m, 6378136.3, rel_tol=0, abs_tol=0.01)
            and math.isclose(gravitational_mu, 3.986004415e14, rel_tol=1e-9)
            and declared_max_degree >= 4 and declared_max_order >= 4
            and normalized == 1 and reference_lon == 0 and reference_lat == 0
        ):
            raise ValueError("invalid GGM03S header")
        coefficients: dict[tuple[int, int], tuple[float, float]] = {}
        last_degree = -1
        for entry in reader:
            if not entry:
                continue
            n, m = int(entry[0]), int(entry[1])
            if n > 4:
                break
            if n < last_degree or not (0 <= m <= n) or (n, m) in coefficients:
                raise ValueError("invalid GGM03S degree/order sequence")
            c, s = float(entry[2]), float(entry[3])
            if not (math.isfinite(c) and math.isfinite(s)):
                raise ValueError("nonfinite GGM03S coefficient")
            coefficients[n, m] = (c, s)
            last_degree = n
        if not all((n, m) in coefficients for n in (2, 3, 4)
                   for m in range(n + 1)):
            raise ValueError("GGM03S degree-2/3/4 terms missing")
        c30, c40 = coefficients[3, 0][0], coefficients[4, 0][0]
        if abs(c30) < 1.0e-10 or abs(c40) < 1.0e-10:
            raise ValueError("GGM03S J3/J4 zonal coefficients absent")
    except (UnicodeDecodeError, ValueError, IndexError, StopIteration) as exc:
        raise ValueError("NATIVE_GGM03S_FORMAT_OR_COEFFICIENTS_INVALID") from exc
    return {
        "name": "GGM03S.txt",
        "path": str(path),
        "source_revision": SOURCE_REVISION,
        "source_url": SOURCE_URL,
        "source_git_blob_sha1": SOURCE_BLOB_SHA1,
        "sha256": digest,
        "size_bytes": len(data),
        "normalized": True,
        "reference_radius_m": radius_m,
        "reference_mu_m3_s2": gravitational_mu,
        "declared_max_degree": declared_max_degree,
        "declared_max_order": declared_max_order,
        "cbar_30": c30,
        "cbar_40": c40,
        "degree3_degree4_full_order_terms_present": True,
    }
