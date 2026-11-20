"""Pin and convert IAGA-VMOD IGRF-14 2025 main field / 2025-30 SV.

The official Basilisk 2.11.0 MagneticFieldWMM module accepts the NOAA COF
numeric layout. We preserve the distinct IGRF-14 data identity and provide
deterministic conversion; do not label the computational engine as an official
IGRF-specific Basilisk module or infer flight-calibrated magnetometry.
"""
from __future__ import annotations

from hashlib import sha1, sha256
import math
from pathlib import Path

SOURCE_COMMIT = "2899280ffbec7cae12e19fbcaa04ed9609092729"
SOURCE_BLOB_SHA1 = "13e7122692044f292afcdf9cac03c017837fa064"
SOURCE_BYTES = 42411
SOURCE_URL = (
    "https://raw.githubusercontent.com/IAGA-VMOD/IGRF14eval/"
    + SOURCE_COMMIT + "/data/release/igrf14coeffs.txt"
)
SOURCE_NAME = "igrf14coeffs.txt"
COF_NAME = "IGRF14-2025.COF"
MODEL_ID = "IGRF14_2025_2030"
BASE_EPOCH = 2025.0
VALID_UNTIL_YEAR = 2030
MAIN_DEGREE = 13
SV_DEGREE = 8


def _blob_sha1(data: bytes) -> str:
    return sha1(b"blob " + str(len(data)).encode("ascii") + b"\0" + data).hexdigest()


def parse_igrf14(source: bytes) -> tuple[dict[tuple[int, int], tuple[float, float, float, float]], dict]:
    """Require the exact released coefficient table, not a COF-like guess."""
    if len(source) != SOURCE_BYTES:
        raise ValueError("IGRF14_SOURCE_SIZE_MISMATCH")
    if _blob_sha1(source) != SOURCE_BLOB_SHA1:
        raise ValueError("IGRF14_SOURCE_GIT_BLOB_MISMATCH")
    try:
        lines = source.decode("ascii").splitlines()
        if len(lines) != 199:
            raise ValueError("source line count")
        if "14th Generation" not in lines[0] or "Schmidt semi-normalised" not in lines[0]:
            raise ValueError("source model identity")
        headings = lines[3].split()
        if headings[-2:] != ["2025.0", "2025-30"]:
            raise ValueError("source epoch columns")
        parsed: dict[tuple[int, int], dict[str, tuple[float, float]]] = {}
        entry_count = 0
        for line in lines[4:]:
            tokens = line.split()
            if len(tokens) != 30 or tokens[0] not in ("g", "h"):
                raise ValueError("source coefficient columns")
            family, degree, order = tokens[:3]
            n, m = int(degree), int(order)
            if not 1 <= n <= MAIN_DEGREE or not 0 <= m <= n or (m == 0 and family == "h"):
                raise ValueError("source degree or order")
            value, annual_sv = float(tokens[-2]), float(tokens[-1])
            if not math.isfinite(value) or not math.isfinite(annual_sv):
                raise ValueError("nonfinite IGRF coefficient")
            if n > SV_DEGREE and annual_sv != 0.0:
                raise ValueError("unexpected higher-degree secular variation")
            key = (n, m)
            item = parsed.setdefault(key, {})
            if family in item:
                raise ValueError("duplicate coefficient")
            item[family] = (value, annual_sv)
            entry_count += 1
        expected_count = sum(2 * n + 1 for n in range(1, MAIN_DEGREE + 1))
        if entry_count != expected_count or len(parsed) != sum(n + 1 for n in range(1, MAIN_DEGREE + 1)):
            raise ValueError("missing complete degree/order table")
        for (n, m), fields in parsed.items():
            if "g" not in fields or (m > 0 and "h" not in fields):
                raise ValueError("missing g/h coefficient pair")
        g10, g11, h11 = parsed[1, 0]["g"][0], parsed[1, 1]["g"][0], parsed[1, 1]["h"][0]
        # Anchor to IAGA's 2025 release, independently visible in the NOAA
        # original text; future revisions must not silently reuse IGRF-14.
        if (g10, g11, h11, parsed[1, 0]["g"][1]) != (-29350.0, -1410.3, 4545.5, 12.6):
            raise ValueError("IGRF14_2025_ANCHOR_MISMATCH")
        records = {
            key: (item["g"][0], item.get("h", (0.0, 0.0))[0],
                  item["g"][1], item.get("h", (0.0, 0.0))[1])
            for key, item in parsed.items()
        }
        return records, {
            "model_id": MODEL_ID,
            "source_commit": SOURCE_COMMIT,
            "source_git_blob_sha1": SOURCE_BLOB_SHA1,
            "source_sha256": sha256(source).hexdigest(),
            "source_bytes": len(source),
            "coefficient_rows": entry_count,
            "main_degree": MAIN_DEGREE,
            "secular_variation_degree": SV_DEGREE,
            "base_epoch_year": BASE_EPOCH,
            "validity_end_exclusive_year": VALID_UNTIL_YEAR,
            "normalization": "Schmidt_semi_normalized",
            "reference_radius_m": 6371200.0,
            "g10_2025_nt": g10,
            "g10_sv_nt_per_year": parsed[1, 0]["g"][1],
            "source_url": SOURCE_URL,
        }
    except (UnicodeError, ValueError, IndexError, KeyError) as exc:
        if isinstance(exc, ValueError) and str(exc).startswith("IGRF14_"):
            raise
        raise ValueError("IGRF14_SOURCE_FORMAT_INVALID") from exc


def render_igrf14_cof(source: bytes) -> tuple[bytes, dict]:
    """Generate the exact 13-degree COF used by Basilisk's MAG routines."""
    records, evidence = parse_igrf14(source)
    rows = [" 2025.0 IGRF14-2025 11/18/2024"]
    for n in range(1, MAIN_DEGREE + 1):
        for m in range(n + 1):
            g, h, gsv, hsv = records[n, m]
            rows.append(f"{n:3d} {m:3d} {g:13.6f} {h:13.6f} {gsv:11.6f} {hsv:11.6f}")
    rows.append("999999999999999999999999999999999999999999999999")
    output = ("\n".join(rows) + "\n").encode("ascii")
    evidence["cof_sha256"] = sha256(output).hexdigest()
    evidence["cof_bytes"] = len(output)
    evidence["cof_degree_and_order"] = MAIN_DEGREE
    evidence["cof_engine"] = "Basilisk.simulation.magneticFieldWMM.MagneticFieldWMM"
    evidence["cof_engine_scope"] = "NOAA_MAG_library_using_IGRF14_not_WMM2025_coefficients"
    return output, evidence


def verify_igrf14_assets(folder: Path) -> dict:
    folder = Path(folder).expanduser().resolve()
    source = folder / SOURCE_NAME
    cof = folder / COF_NAME
    if not source.is_file() or not cof.is_file():
        raise FileNotFoundError("IGRF14_PINNED_SOURCE_OR_COF_MISSING")
    required_cof, evidence = render_igrf14_cof(source.read_bytes())
    if cof.read_bytes() != required_cof:
        raise ValueError("IGRF14_DERIVED_COF_CONTENT_MISMATCH")
    evidence["cof_path"] = str(cof)
    evidence["source_path"] = str(source)
    return evidence
