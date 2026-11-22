"""Compare archived GMAT TT and UTC to native ET with a geocentric ERFA conversion."""
import argparse
import csv
from decimal import Decimal as D
import hashlib
import json
from pathlib import Path
import erfa


def checked(root, name, digest):
    data = (root / name).read_bytes()
    if hashlib.sha256(data).hexdigest() != digest:
        raise ValueError(name)
    return data.decode().splitlines()


def et(tt):
    # Split JD at J2000, retain decimal-day precision in the linear term.
    days = tt + D(2430000) - D(2451545)
    correction = erfa.dtdb(2451545., float(days), 0., 0., 0., 0.)
    return days * 86400 + D(str(correction))


def audit(root):
    frame = json.loads((root / "gmat_frame_time_execution.json").read_text())
    diag = json.loads((root / "gmat_full_grid_ET_diagnostic.json").read_text())
    raw = csv.reader(checked(root, "gmat_frame_time_raw.txt", frame["frame_time_report_sha256"]))
    native = csv.DictReader(checked(root, "basilisk_same_process_spice_ET_91.csv",
                                    diag["native_Basilisk_91_ET_messages_sha256"]))
    archive = csv.DictReader(checked(root, "gmat_full_grid_a1_tt_to_ET_91.csv",
                                     diag["full_91_point_comparison_sha256"]))
    results = []
    for i, (g, b, a) in enumerate(zip(raw, native, archive, strict=True)):
        elapsed, utc, tai, tt, a1 = map(D, g[:5])
        assert i == int(a["sample_index"]) and abs(D(b["time_s"]) - i*60) < D("1e-8")
        assert abs(elapsed - i*60) < D("0.001")
        # Independently convert reported UTC to TT using ERFA leap-second tables.
        t1, t2 = erfa.utctai(2430000., float(utc))
        t1, t2 = erfa.taitt(t1, t2)
        tt_from_utc = D(str(t1)) - D(2430000) + D(str(t2))
        entry = dict(index=i)
        for name, delta in {
            "erfa_tt_minus_native": et(tt) - D(b["et_s"]),
            "erfa_tt_minus_archived_naif_tt": et(tt) - D(a["gmat_TT_unitim_ET_s"]),
            "erfa_utc_minus_report_tt": (tt_from_utc-tt)*86400,
            "erfa_a1_minus_erfa_tt": et(a1 + (D("32.184")-D("0.0343817"))/86400) - et(tt),
        }.items():
            entry[name+"_us"] = float(delta * 1000000)
        results.append(entry)
    assert len(results) == 91
    return dict(pyerfa=erfa.__version__, maxima_abs_us={
        key:max(abs(row[key]) for row in results) for key in results[0] if key!="index"}, rows=results)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("artifact", type=Path)
    parser.add_argument("output", type=Path)
    args = parser.parse_args()
    result = audit(args.artifact)
    args.output.write_text(json.dumps(result, indent=2), encoding="utf-8")
    print(result["maxima_abs_us"])
