"""Check archived geometric Sun/Moon directions against ERFA without a DE421 query."""
import argparse
import csv
import hashlib
import itertools
import json
from pathlib import Path

import erfa
import numpy as np


def verified_lines(root, filename, sha):
    data = (root / filename).read_bytes()
    if hashlib.sha256(data).hexdigest() != sha:
        raise ValueError(f"Input digest mismatch: {filename}")
    return data.decode().splitlines()


def angle_arcsec(a, b):
    return float(np.degrees(np.arctan2(np.linalg.norm(np.cross(a, b)), a @ b)) * 3600)


def audit(root):
    receipt_hash = "890f91601f510c7b4f9cf20f54250bfa3c590d448d73c1902be4951fc9e171d2"
    receipt = json.loads("\n".join(verified_lines(root, "sun_anchor_execution.json", receipt_hash)))
    frames = json.loads((root / "gmat_frame_time_execution.json").read_bytes())
    clock = list(csv.reader(verified_lines(root, "gmat_frame_time_raw.txt", frames["frame_time_report_sha256"])))
    samples, vectors, references = [], [], []
    for body in ("sun", "moon"):
        normalized = csv.DictReader(verified_lines(root, f"{body}_anchor_three_engines.csv",
                                                  receipt[f"{body}_anchor_comparison_csv_sha256"]))
        raw = csv.reader(verified_lines(root, f"gmat_{body}_anchor_raw.txt", receipt[f"gmat_{body}_report_sha256"]))
        for index, row, original in zip((0, 45, 90), normalized, raw, strict=True):
            elapsed, _, _, tt, a1 = map(float, clock[index][:5])
            assert abs(elapsed - index * 60) < 0.001 and float(row["time_s"]) == index * 60
            assert abs(float(row["gmat_a1_mjd"]) - a1) < 1e-8
            jd1 = 2430000.
            if body == "sun":
                offset = erfa.dtdb(jd1, tt, 0., 0., 0., 0.)
                tdb = erfa.tttdb(jd1, tt, offset)
                heliocentric, _ = erfa.epv00(*tdb)
                reference = -heliocentric["p"] * erfa.DAU / 1000
            else:
                reference = erfa.moon98(jd1, tt)["p"] * erfa.DAU / 1000
            data = {}
            for engine in ("gmat_icrf", "basilisk_spice_n", "naif_j2000"):
                v = np.array([float(row[f"{engine}_{body}_{c}_km"]) for c in "xyz"])
                if engine == "gmat_icrf":
                    np.testing.assert_array_equal(v, list(map(float, original[:3])))
                if engine == "basilisk_spice_n":
                    vectors.append(v)
                    references.append(reference)
                data[engine] = dict(angle_arcsec=angle_arcsec(v, reference),
                                    separation_km=float(np.linalg.norm(v-reference)))
            samples.append(dict(body=body, elapsed_s=index*60, erfa_geometric_km=reference.tolist(), comparisons=data))

    # Diagnose gross axis permutations and sign mistakes, including reflections.
    alternatives = []
    for order in itertools.permutations(range(3)):
        for signs in itertools.product((1, -1), repeat=3):
            if order == (0, 1, 2) and signs == (1, 1, 1):
                continue
            errors = [angle_arcsec(v[list(order)] * signs, ref) for v, ref in zip(vectors, references, strict=True)]
            alternatives.append(dict(order=order, signs=signs, max_error_deg=max(errors)/3600))
    return dict(archive_receipt_sha256=receipt_hash, frame_report_sha256=frames["frame_time_report_sha256"],
                erfa=erfa.__version__, numpy=np.__version__, samples=samples,
                signed_permutation_controls=len(alternatives),
                closest_nonidentity_by_max_error=min(alternatives, key=lambda a:a["max_error_deg"]))


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("artifact", type=Path)
    parser.add_argument("output", type=Path)
    args = parser.parse_args()
    result = audit(args.artifact)
    args.output.write_text(json.dumps(result, indent=2) + "\n", encoding="utf-8")
    for row in result["samples"]:
        print(row["body"], row["elapsed_s"], row["comparisons"]["basilisk_spice_n"])
    print(result["closest_nonidentity_by_max_error"])
