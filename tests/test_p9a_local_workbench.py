"""Local launcher opt-in, not a global/API policy relaxation."""
import importlib.util
import os
import pytest
from sat_sim.interactive import interactive_enabled

FLAG = "SAT_SIM_INTERACTIVE_ENABLED"

def launcher():
    assert importlib.util.find_spec("sat_sim.local_workbench"), "Local workbench launcher is missing"
    from sat_sim import local_workbench
    return local_workbench

@pytest.mark.parametrize("host", ["127.0.0.1", "127.0.0.2", "::1", "localhost"])
def test_only_loopback_defaults_to_interactive(host):
    env = {}
    launcher().configure_local_environment(host, env)
    assert env[FLAG] == "1"

@pytest.mark.parametrize("value", ["0", "false", "off", "", "1", "true"])
def test_explicit_settings_are_preserved(value):
    env = {FLAG: value, "SAT_SIM_SUPERVISED_NATIVE_RUNTIME": "1"}
    launcher().configure_local_environment("127.0.0.1", env)
    assert env == {FLAG: value, "SAT_SIM_SUPERVISED_NATIVE_RUNTIME": "1"}

@pytest.mark.parametrize("host", ["0.0.0.0", "::", "192.168.1.20", "example.org", "localhost.example.org"])
def test_network_hosts_are_not_auto_enabled(host):
    env = {}
    launcher().configure_local_environment(host, env)
    assert FLAG not in env

def test_library_default_stays_disabled(monkeypatch):
    monkeypatch.delenv(FLAG, raising=False)
    assert not interactive_enabled()

def test_local_launcher_serves_actual_interactive_routes(tmp_path, monkeypatch):
    import uvicorn
    from fastapi.testclient import TestClient
    monkeypatch.delenv(FLAG, raising=False)
    monkeypatch.delenv("SAT_SIM_SUPERVISED_NATIVE_RUNTIME", raising=False)
    captured = {}
    def capture(app, **kwargs):
        with TestClient(app) as client:
            captured["health"] = client.get("/health").json()
            response = client.get("/interactive/capabilities")
            assert response.status_code == 200
            captured["policy"] = response.json()
    monkeypatch.setattr(uvicorn, "run", capture)
    result = launcher().main(["--host", "127.0.0.1", "--runs-root", str(tmp_path / "runs"), "--artifacts-root", str(tmp_path / "artifacts")])
    assert result == 0
    assert captured["health"]["features"]["interactive"] is True
    supported = {(x["capability_id"], x["continuous"]) for x in captured["policy"]["creation"] if x["supported"]}
    assert ("whole_spacecraft.unified_native.v1", False) in supported
    assert ("whole_spacecraft.unified_native.v1", True) in supported
    assert ("subsystem.adcs_unified_native.v1", False) in supported
    assert ("subsystem.adcs_unified_native.v1", True) not in supported
