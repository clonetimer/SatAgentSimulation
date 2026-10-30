"""Runtime preflight and package metadata agree on the supported Python floor."""
from pathlib import Path
import tomllib

from packaging.specifiers import SpecifierSet
import pytest

from sat_sim import release_closure


@pytest.mark.parametrize(('version', 'expected'), [
    ((3, 10, 20), release_closure.CheckStatus.FAIL),
    ((3, 11, 0), release_closure.CheckStatus.PASS),
    ((3, 13, 99), release_closure.CheckStatus.PASS),
    ((3, 14, 0), release_closure.CheckStatus.FAIL),
])
def test_environment_doctor_rejects_unsupported_python_before_other_checks(monkeypatch, version, expected):
    observed = []
    original = release_closure._check

    class StopAfterPythonCheck(Exception):
        pass

    def capture_first_check(*args, **kwargs):
        result = original(*args, **kwargs)
        observed.append(result)
        raise StopAfterPythonCheck

    # Stop after the real first environment-doctor check, avoiding unrelated
    # imports, filesystem probes and optional native smoke execution.
    with monkeypatch.context() as patch:
        patch.setattr(release_closure.sys, 'version_info', version)
        patch.setattr(release_closure, '_check', capture_first_check)
        with pytest.raises(StopAfterPythonCheck):
            release_closure.run_environment_doctor(smoke=False)
    assert len(observed) == 1
    assert observed[0].check_id == 'python.version'
    assert observed[0].status == expected
    assert 'Python 3.11 through 3.13' in observed[0].summary


def test_package_metadata_python_floor_matches_runtime_supported_range():
    root = Path(__file__).resolve().parents[1]
    project = tomllib.loads((root/'pyproject.toml').read_text(encoding='utf-8'))['project']
    supported = SpecifierSet(project['requires-python'])
    assert not supported.contains('3.10.20')
    assert supported.contains('3.11.0')
    assert supported.contains('3.12.12')
    assert supported.contains('3.13.9')
    assert not supported.contains('3.14.0')
