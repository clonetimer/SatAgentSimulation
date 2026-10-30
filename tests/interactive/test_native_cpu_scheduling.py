"""Scheduling must respect existing CPU permissions and disclose unavailable policy."""
import os
from types import SimpleNamespace

import pytest

import sat_sim.interactive.native_cpu_scheduling as target


def _cpu(index, efficiency, *, group=0, flags=0):
    return {'group':group, 'logical_processor':index,
            'efficiency_class':efficiency, 'flags':flags}


def test_selection_never_widens_permission_or_uses_reserved_processors():
    sets = [_cpu(0,0), _cpu(1,1), _cpu(2,1), _cpu(3,2),
            _cpu(4,3,flags=1), _cpu(5,3,flags=2), _cpu(6,3,flags=8)]
    assert target._performance_affinity(sets,[0,1,2,4,5,6]) == [1,2]
    assert target._performance_affinity(sets,[0]) == []
    assert target._performance_affinity([_cpu(0,0),_cpu(1,1,group=1)],[0,1]) == []
    assert target._performance_affinity([_cpu(0,0),_cpu(1,0)],[0,1]) == []


def _fake_windows(monkeypatch, *, fail=False, mismatch=False):
    monkeypatch.setattr(target, 'sys', SimpleNamespace(platform='win32'))
    monkeypatch.setattr(target, '_windows_cpu_sets', lambda:[_cpu(0,0),_cpu(1,1),_cpu(2,1)])
    class Process:
        affinity = [0,1]
        changes = []
        def cpu_affinity(self, selected=None):
            if selected is None:
                return list(self.affinity)
            self.changes.append(list(selected))
            if fail and selected == [1]:
                raise OSError('denied by test policy')
            self.affinity = list(selected) if not mismatch else [0,1]
    process = Process()
    # The interface intentionally has no PID argument; production touches self only.
    monkeypatch.setattr(target.psutil, 'Process', lambda:process)
    return process


def test_self_policy_reports_verified_effect_and_preserves_input_mask(monkeypatch):
    monkeypatch.delenv('SAT_SIM_NATIVE_CPU_POLICY',raising=False)
    process = _fake_windows(monkeypatch)
    result = target.configure_native_worker_scheduling()
    assert process.changes == [[1]]
    assert result['pid'] == os.getpid()
    assert result['applied'] is True and result['mode'] == 'PERFORMANCE_CORES'
    assert result['allowed_affinity'] == [0,1] and result['selected_affinity'] == [1]


@pytest.mark.parametrize('kind',['failure','mismatch'])
def test_unavailable_preference_restores_and_reports_system_policy(monkeypatch,kind):
    monkeypatch.delenv('SAT_SIM_NATIVE_CPU_POLICY',raising=False)
    process = _fake_windows(monkeypatch,fail=kind=='failure',mismatch=kind=='mismatch')
    result = target.configure_native_worker_scheduling()
    assert process.affinity == [0,1]
    assert process.changes == [[1],[0,1]]
    assert result['applied'] is False and result['mode'] == 'SYSTEM_DEFAULT'
    assert result['reason'] == 'SCHEDULING_PREFERENCE_UNAVAILABLE'
    assert result['error']


def test_explicit_system_policy_does_not_touch_affinity(monkeypatch):
    monkeypatch.setenv('SAT_SIM_NATIVE_CPU_POLICY','system')
    monkeypatch.setattr(target,'_windows_cpu_sets',lambda:pytest.fail('policy must not inspect/change CPU affinity'))
    assert target.configure_native_worker_scheduling()['reason'] == 'EXPLICIT_SYSTEM_POLICY'


def test_invalid_deployment_policy_is_rejected(monkeypatch):
    monkeypatch.setenv('SAT_SIM_NATIVE_CPU_POLICY','unknown')
    with pytest.raises(ValueError,match='NATIVE_CPU_POLICY_INVALID'):
        target.configure_native_worker_scheduling()


def test_unavailable_windows_api_is_disclosed_without_touching_affinity(monkeypatch):
    monkeypatch.delenv('SAT_SIM_NATIVE_CPU_POLICY',raising=False)
    monkeypatch.setattr(target,'sys',SimpleNamespace(platform='win32'))
    def unavailable():
        raise AttributeError('GetSystemCpuSetInformation unavailable')
    monkeypatch.setattr(target,'_windows_cpu_sets',unavailable)
    monkeypatch.setattr(target.psutil,'Process',lambda:pytest.fail('unsupported API must not touch affinity'))
    result = target.configure_native_worker_scheduling()
    assert result['mode'] == 'SYSTEM_DEFAULT' and result['applied'] is False
    assert 'AttributeError' in result['error']
