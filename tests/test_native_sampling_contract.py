"""Requested native sampling must match real recorder timestamps."""
import pytest
from fastapi.testclient import TestClient

from sat_sim.api import create_app
from sat_sim.form_schema import capability_form_schema
from sat_sim.unified_agent import normalize_form_task_spec
from sat_sim.task_models import canonicalize_task_spec
from sat_sim.task_compiler import compile_task_spec
from sat_sim.task_validator import validate_task_spec
from sat_sim.unified_execution import execute_compiled_task
from sat_sim.bsk_engine.unified_native import UnifiedRuntimeConfig


def _native_task(step_s):
    spec = canonicalize_task_spec(normalize_form_task_spec(
        capability_form_schema('subsystem.adcs_unified_native.v1')['default_form']))
    spec['simulation'].update(duration_s=3., step_s=step_s, sample_s=.5)
    return spec


def test_unaligned_native_sampling_is_rejected_before_execution():
    validation = validate_task_spec(_native_task(.2))
    assert not validation.ok
    assert any('NATIVE_SAMPLING_MISALIGNED' in issue.message for issue in validation.errors)
    with pytest.raises(ValueError, match='NATIVE_SAMPLING_MISALIGNED'):
        UnifiedRuntimeConfig('subsystem.adcs_unified_native.v1', 3., .2, .5, True, {}, ())


def test_aligned_native_finite_run_records_requested_half_seconds():
    spec = _native_task(.1)
    result = execute_compiled_task(compile_task_spec(spec), task_spec=spec, write_dataset=False)
    assert [row['time_s'] for row in result.trace_rows] == pytest.approx([0., .5, 1., 1.5, 2., 2.5, 3.])


def test_continuous_default_half_second_period_uses_real_aligned_native_samples(tmp_path, monkeypatch):
    monkeypatch.setenv('SAT_SIM_INTERACTIVE_ENABLED', '1')
    monkeypatch.delenv('SAT_SIM_SUPERVISED_NATIVE_RUNTIME', raising=False)
    app = create_app(runs_root=tmp_path/'runs', artifacts_root=tmp_path/'artifacts',
                     embedded_worker=False, auth_mode='disabled')
    with TestClient(app) as client:
        response = client.post('/interactive/continuous/sessions', json={'session_id':'aligned-native'})
        assert response.status_code == 201, response.text
        session = app.state.interactive_manager.get('aligned-native')
        assert session.spec.task_spec['simulation']['solver']['step_s'] == .1
        assert client.post('/interactive/sessions/aligned-native/control', json={'action':'start'}).status_code == 200
        assert client.post('/interactive/sessions/aligned-native/control', json={'action':'pause'}).status_code == 200
        assert client.post('/interactive/sessions/aligned-native/control', json={'action':'step', 'quanta':6}).status_code == 200
        response = client.get('/interactive/sessions/aligned-native/telemetry/whole.continuous')
        assert response.status_code == 200
        times = [row['sim_time_s'] for row in response.json()['frames']]
        assert len(times) >= 6
        assert all(abs(value/.5 - round(value/.5)) < 1e-9 for value in times)
        assert [b-a for a,b in zip(times,times[1:])] == pytest.approx([.5]*(len(times)-1))
        assert client.post('/interactive/sessions/aligned-native/control', json={'action':'stop'}).status_code == 200
        assert session.wait(timeout_s=15.)
        from sat_sim.interactive.continuous_source_audit import audit_telemetry_sampling
        assert audit_telemetry_sampling(session._workspace.path, .5)['status'] == 'PASS'


def test_cadence_audit_rejects_mismatch_in_rotated_protocol_fixture(tmp_path):
    # Log-auditor fixture, not satellite physics or duration qualification.
    from sat_sim.interactive.models import InteractiveSessionSpec, TelemetryFrame
    from sat_sim.interactive.workspace import SessionWorkspace
    from sat_sim.interactive.continuous_source_audit import audit_telemetry_sampling
    spec = InteractiveSessionSpec(session_id='cadence-log-fixture',
        capability_id='whole_spacecraft.unified_native.v1', task_spec={}, quantum_s=.5, continuous=True)
    workspace = SessionWorkspace(tmp_path, spec)
    workspace._telemetry_segment_max_bytes = 512
    for index, time_s in enumerate([0., .6, 1.2, 1.5, 2., 2.5, 3.]):
        workspace.append_telemetry(TelemetryFrame(session_id=spec.session_id,
            stream='whole.continuous', sequence=index, sim_time_s=time_s, values={'fixture':True}))
    result = audit_telemetry_sampling(workspace.path, .5)
    assert result['frames_checked'] == 7
    assert result['status'] == 'NOT_SATISFIED'
    assert result['off_grid_frame_count'] == 2
    assert result['unexpected_interval_count'] == 3
