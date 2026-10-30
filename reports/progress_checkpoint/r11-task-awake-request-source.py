"""Task-scoped Windows system-awake request for the already-owned24h run.

No power-policy change, display request, process restart, TC or simulation write.
Windows releases this thread's request on exit; normal shutdown releases it
explicitly when the original native or launcher identity is no longer alive.
"""
from pathlib import Path
from datetime import datetime, timezone
import ctypes
import hashlib
import json
import os
import time
import psutil

ROOT = Path(__file__).resolve().parents[2]
BASE = ROOT / 'reports/local_requirements_completion'
OUT = BASE / 'hardware-bounded-r11-task-awake-request'
NATIVE_PID, NATIVE_CREATED = 80520, 1793337824.415924
LAUNCHER_PID, LAUNCHER_CREATED = 82756, 1793337780.3809385
ES_CONTINUOUS, ES_SYSTEM_REQUIRED = 0x80000000, 0x00000001

def alive(pid, created):
    try:
        proc = psutil.Process(pid)
        return proc.is_running() and proc.create_time() == created
    except psutil.NoSuchProcess:
        return False

def main():
    assert os.name == 'nt'
    assert alive(NATIVE_PID, NATIVE_CREATED) and alive(LAUNCHER_PID, LAUNCHER_CREATED)
    OUT.mkdir(exist_ok=False)
    proc = psutil.Process()
    progress = json.loads((BASE / 'hardware-bounded-r11-endurance/endurance-24h/progress.json').read_text(encoding='utf-8'))
    assert progress['status'] == 'RUNNING'
    remaining = max(0., progress['requested_wall_seconds'] - progress['wall_observed_by_runner_s'])
    deadline = time.monotonic() + remaining + 1800.
    state = {'status':'STARTING', 'started_at_utc':datetime.now(timezone.utc).isoformat(),
        'pid':proc.pid,'created':proc.create_time(),'command':proc.cmdline(),
        'native_pid':NATIVE_PID,'native_created':NATIVE_CREATED,
        'launcher_pid':LAUNCHER_PID,'launcher_created':LAUNCHER_CREATED,
        'driver_sha256':hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
        'requested_flags_hex':hex(ES_CONTINUOUS | ES_SYSTEM_REQUIRED),
        'request_active':False,'persistent_power_policy_changed':False,'display_request':False,
        'scope':'Only this helper thread while the same owned native and launcher live. Does not prevent explicit user sleep/power-off, qualify duration, or touch simulation inputs.'}
    def save():
        path = OUT / 'state.json.pending'
        path.write_text(json.dumps(state,indent=2)+'\n',encoding='utf-8')
        path.replace(OUT / 'state.json')
    api = ctypes.WinDLL('kernel32',use_last_error=True).SetThreadExecutionState
    api.argtypes = [ctypes.c_uint32]
    api.restype = ctypes.c_uint32
    save()
    acquired = False
    try:
        previous = api(ES_CONTINUOUS | ES_SYSTEM_REQUIRED)
        state['previous_thread_flags_hex'] = hex(previous)
        if previous == 0:
            raise OSError(ctypes.get_last_error(),'WINDOWS_SYSTEM_AWAKE_REQUEST_FAILED')
        acquired = True
        confirmed = api(ES_CONTINUOUS | ES_SYSTEM_REQUIRED)
        state['confirmed_previous_flags_hex'] = hex(confirmed)
        assert confirmed & (ES_CONTINUOUS | ES_SYSTEM_REQUIRED) == ES_CONTINUOUS | ES_SYSTEM_REQUIRED
        state.update(status='HOLDING_SYSTEM_AWAKE_FOR_SAME_OWNED_NATIVE',request_active=True)
        save()
        while alive(NATIVE_PID,NATIVE_CREATED) and alive(LAUNCHER_PID,LAUNCHER_CREATED):
            if time.monotonic() >= deadline:
                state['release_reason'] = 'TASK_RUN_DURATION_PLUS30MIN_DEADLINE'
                break
            state['last_verified_same_targets_alive_at_utc'] = datetime.now(timezone.utc).isoformat()
            save()
            time.sleep(15.)
        state.setdefault('release_reason','OWNED_NATIVE_OR_LAUNCHER_IDENTITY_NO_LONGER_ALIVE')
        state['status'] = 'RELEASED'
    except Exception as exc:
        state.update(status='FAILED_REQUIRES_REVIEW',error={'type':type(exc).__name__,'message':str(exc)})
    finally:
        if acquired:
            result = api(ES_CONTINUOUS)
            state['explicit_release_return_flags_hex'] = hex(result)
        state.update(request_active=False,finished_at_utc=datetime.now(timezone.utc).isoformat())
        save()
    return int(state['status'] == 'FAILED_REQUIRES_REVIEW')

if __name__ == '__main__':
    raise SystemExit(main())
