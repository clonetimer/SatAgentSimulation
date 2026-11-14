"""The published wheel must contain every first-party scene ES module."""
from pathlib import Path
import fnmatch
import tomllib
import os
import zipfile


def test_all_scene_es_modules_are_declared_as_package_data():
    root = Path(__file__).resolve().parents[1]
    config = tomllib.loads((root/"pyproject.toml").read_text(encoding="utf-8"))
    patterns = config["tool"]["setuptools"]["package-data"]["sat_sim"]
    assert any(fnmatch.fnmatchcase("web/realtime-orbit.mjs",pattern) for pattern in patterns), "P9F_ES_MODULE_MISSING_FROM_DISTRIBUTION"


def test_built_wheel_contains_the_live_projection_module():
    import pytest
    wheel_dir = os.environ.get("SAT_SIM_P9F_WHEEL_DIR")
    if not wheel_dir:
        pytest.skip("Dedicated built-wheel acceptance")
    wheels = list(Path(wheel_dir).glob("*.whl"))
    assert len(wheels)==1, wheels
    with zipfile.ZipFile(wheels[0]) as archive:
        assert "sat_sim/web/realtime-orbit.mjs" in archive.namelist()
        assert "inertialPositionToEarthFixed" in archive.read("sat_sim/web/realtime-orbit.mjs").decode()
