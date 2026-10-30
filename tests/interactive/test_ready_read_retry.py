"""Readiness I/O protocol checks; these stubs are not Basilisk physics evidence."""
import json
import time
from pathlib import Path

import pytest

from sat_sim.interactive import native_process_supervisor as module
from test_native_supervised_process_barrier import _launch


def wait_for_published_file(supervisor):
    deadline = time.monotonic() + 8.
    while not supervisor.ready_path.is_file():
        assert supervisor.returncode is None
        if time.monotonic() >= deadline:
            raise TimeoutError('Protocol test child did not publish its ready file')
        time.sleep(.02)


def test_transient_ready_permission_errors_preserve_owned_child_handshake(tmp_path, monkeypatch):
    supervisor = _launch(tmp_path)
    try:
        wait_for_published_file(supervisor)
        original = Path.read_text
        denied = []

        def read(path, *args, **kwargs):
            if path == supervisor.ready_path and len(denied) < 3:
                denied.append(True)
                raise PermissionError('Injected transient Windows sharing denial')
            return original(path, *args, **kwargs)

        with monkeypatch.context() as patch:
            patch.setattr(Path, 'read_text', read)
            ready = supervisor.observe_ready(timeout_s=2.)
        assert len(denied) == 3 and ready['pid'] == supervisor.pid
        assert supervisor.returncode is None
        barrier = supervisor.terminate_and_attest(force=True)
        assert barrier['direct_child_wait_observed'] is True
        assert barrier['native_resume_supported'] is False
    finally:
        supervisor.cleanup_without_attestation()


def test_permanent_ready_permission_denial_honors_original_deadline(tmp_path, monkeypatch):
    supervisor = _launch(tmp_path)
    try:
        wait_for_published_file(supervisor)
        original = Path.read_text
        reads, clock_calls = [], []

        def read(path, *args, **kwargs):
            if path == supervisor.ready_path:
                reads.append(True)
                raise PermissionError('Injected persistent denial')
            return original(path, *args, **kwargs)

        def clock():
            clock_calls.append(True)
            assert len(clock_calls) <= 10, 'Read retry reset the original deadline'
            return 100. if len(clock_calls) == 1 else 100.01 if len(clock_calls) == 2 else 100.1

        with monkeypatch.context() as patch:
            patch.setattr(Path, 'read_text', read)
            patch.setattr(module.time, 'monotonic', clock)
            with pytest.raises(TimeoutError, match='SUPERVISOR_CHILD_READY_TIMEOUT'):
                supervisor.observe_ready(timeout_s=.05)
        assert reads and not supervisor.barrier_path.exists()
        with pytest.raises(ValueError, match='NO_READY_HANDSHAKE'):
            supervisor.terminate_and_attest(force=True)
    finally:
        supervisor.cleanup_without_attestation()


def test_permission_retry_never_accepts_a_wrong_ready_nonce(tmp_path, monkeypatch):
    supervisor = _launch(tmp_path)
    try:
        wait_for_published_file(supervisor)
        original = Path.read_text
        attempts = []

        def read(path, *args, **kwargs):
            if path == supervisor.ready_path:
                attempts.append(True)
                if len(attempts) == 1:
                    raise PermissionError('Injected transient sharing denial')
                return json.dumps({'schema_version': module.READY_SCHEMA, 'run_nonce': '0' * 64,
                                   'pid': supervisor.pid, 'profile': 'stub-worker',
                                   'checkpoint_ns': 0, 'checkpoint_sha256': None})
            return original(path, *args, **kwargs)

        with monkeypatch.context() as patch:
            patch.setattr(Path, 'read_text', read)
            with pytest.raises(ValueError, match='CHILD_IDENTITY_INVALID'):
                supervisor.observe_ready(timeout_s=2.)
        assert len(attempts) == 2 and not supervisor.barrier_path.exists()
        with pytest.raises(ValueError, match='NO_READY_HANDSHAKE'):
            supervisor.terminate_and_attest(force=True)
    finally:
        supervisor.cleanup_without_attestation()
