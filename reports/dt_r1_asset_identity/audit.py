"""Check the external data identities in the DT four-effect product regression."""

import argparse
import hashlib
import json
from pathlib import Path


def read(path):
    return json.loads(path.read_bytes())


def sha256(path):
    with path.open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def require(condition, message):
    if not condition:
        raise ValueError(message)


def audit(case, assets, fixture):
    bundle = case / "bundles/dt1_product_four_effects"
    seal = read(bundle / "SEALED.json")
    manifest = read(bundle / "bundle_manifest.json")
    require(seal["bundle_manifest_sha256"] == sha256(bundle / "bundle_manifest.json"), "seal")
    for name, item in manifest["files"].items():
        path = (bundle / name).resolve()
        require(path.is_relative_to(bundle.resolve()), name)
        require(path.stat().st_size == item["size_bytes"] and sha256(path) == item["sha256"], name)
    summary_name = "results/summary.json"
    deps_name = "runtime/dependency_versions.json"
    require({summary_name, deps_name}.issubset(manifest["files"]), "sealed summary and dependencies")
    require((manifest["status"], manifest["validation_result"]) == ("SUCCEEDED", "PASS"), "run")
    result = read(bundle / summary_name)
    exported = read(case / "script_outputs/summary.json")
    env = result["orbital_environment"]
    require(exported["orbital_environment"] == env, "exported and sealed environment differ")
    models = (env["gravity_model"], env["magnetic_field_model_id"], env["atmosphere_model"])
    require(models == ("ggm03s_degree4", "IGRF14_2025_2030", "NRLMSISE-00"), "model selection")
    require(env["drag_enabled"] and result["solar_radiation_pressure"]["status"] == "PASS", "drag/SRP")
    entries = []

    def check(expected_path, recorded_path, recorded_digest, role):
        require(expected_path.resolve() == Path(recorded_path).resolve(), f"path: {role}")
        actual = sha256(expected_path)
        require(actual == recorded_digest, f"digest: {role}")
        entries.append(dict(role=role, file=expected_path.name, bytes=expected_path.stat().st_size,
                            sha256=actual))

    metadata = env["assets"]
    kernels = {item["name"]: item for item in metadata["spice"]}
    expected = ("de430.bsp", "naif0012.tls", "pck00010.tpc", "gm_de431.tpc")
    require(len(metadata["spice"]) == len(expected) and kernels.keys() == set(expected), "kernel set")
    for name in expected:
        check(assets / "spice" / name, kernels[name]["path"], kernels[name]["sha256"], "SPICE " + name)
    gravity = metadata["gravity_full"]
    check(assets / "gravity/GGM03S.txt", gravity["path"], gravity["sha256"], "gravity coefficients")
    igrf = metadata["igrf14"]
    check(assets / "igrf/igrf14coeffs.txt", igrf["source_path"], igrf["source_sha256"], "IGRF source")
    check(assets / "igrf/IGRF14-2025.COF", igrf["cof_path"], igrf["cof_sha256"], "IGRF runtime COF")
    weather = env["space_weather"]
    check(case / "SW-Last5Years.csv", weather["path"], weather["sha256"], "space weather fixture")
    require(sha256(fixture) == weather["sha256"], "source fixture")
    provenance = case / "space_weather_manifest.json"
    require(sha256(provenance) == weather["provenance_manifest_sha256"], "weather provenance digest")
    weather_manifest = read(provenance)
    require(weather_manifest["asset"]["sha256"] == weather["sha256"], "weather provenance asset")
    require(weather_manifest["source_url"] == weather["source_url"]
            == "test-fixture://celestrak-compatible-sample", "weather provenance source")

    return dict(assets=entries, weather_manifest_sha256=sha256(provenance),
                weather_source=weather["source_url"], sealed_members_checked=len(manifest["files"]),
                bundle_manifest_sha256=seal["bundle_manifest_sha256"],
                execution_plan_sha256=manifest["execution_plan_sha256"],
                dependencies=read(bundle / deps_name)["dependencies"],
                native_result=manifest["validation_result"],
                exported_and_sealed_environment_equal=True)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    for name in ("case", "asset_root", "weather_fixture"):
        parser.add_argument(name, type=Path)
    args = parser.parse_args()
    print(json.dumps(audit(args.case, args.asset_root, args.weather_fixture), indent=2))
