"""Windows native lifetime guard: real kernel Job Object kill-on-close proof.

Runs only on Windows CI; no pure-Python spacecraft physics is used.
"""
from __future__ import annotations

import ctypes
import os
import subprocess
import sys
import time

import pytest

# Windows guard is stdlib-only. Load its *real source file* in the lean
# Windows OS job test without eagerly importing unrelated satellite-science
# dependencies from sat_sim.__init__ (which require Basilisk, PyYAML, etc.).
import importlib.util
from pathlib import Path

_SOURCE = Path(__file__).resolve().parents[2] / "src" / "sat_sim" / "interactive" / "native_windows_job.py"
_spec = importlib.util.spec_from_file_location("windows_native_job_guard", _SOURCE)
assert _spec is not None and _spec.loader is not None
_module = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(_module)
WindowsKillOnCloseJob = _module.WindowsKillOnCloseJob


@pytest.mark.skipif(sys.platform != "win32", reason="Windows Job Object test")
def test_windows_job_close_kills_live_child():
    process = subprocess.Popen(
        [sys.executable, "-c", "import time; time.sleep(30)"],
        stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
    )
    job = None
    try:
        job = WindowsKillOnCloseJob.attach(process.pid)
        assert job.attached
        assert process.poll() is None
        job.close()
        assert not job.attached
        assert process.wait(timeout=10.) != 0
    finally:
        if job is not None:
            job.close()
        if process.poll() is None:
            process.kill()
        process.wait(timeout=5.)


@pytest.mark.skipif(sys.platform != "win32", reason="Windows Job Object test")
def test_windows_unmanaged_child_refused():
    with pytest.raises(ValueError, match="BAD_CHILD"):
        WindowsKillOnCloseJob.attach(-1)


def test_job_not_available_on_linux():
    if sys.platform == "win32":
        pytest.skip("tested by native Windows tests")
    with pytest.raises(RuntimeError, match="UNSUPPORTED_PLATFORM"):
        WindowsKillOnCloseJob.attach(1234)
