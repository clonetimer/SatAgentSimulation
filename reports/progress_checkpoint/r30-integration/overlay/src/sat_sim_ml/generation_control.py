"""Bounded, CPU-only admission and cooperative generation control.

Cancelling an HTTP waiter never releases a still-running worker's reservation.
No operation here loads a model or interrupts a CUDA/native computation.
"""
from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
import math
import threading
import time
from uuid import uuid4


class ModelBusy(RuntimeError):
    pass


class GenerationAborted(RuntimeError):
    def __init__(self, reason: str, request_id: str):
        super().__init__(reason)
        self.reason, self.request_id = reason, request_id


class GenerationControl:
    def __init__(self, deadline_s: float, *, clock=time.monotonic):
        self.request_id = 'chatcmpl-' + uuid4().hex
        self.clock = clock
        self.received_at = clock()
        self.deadline_at = self.received_at + deadline_s
        self.last_progress_at = self.received_at
        self.started_at = self.finished_at = None
        self.phase = 'queued'
        self.prompt_tokens = self.generated_tokens = None
        self.first_token_at = None
        self.cancel_event = threading.Event()
        self.cancel_reason = None
        self.cancel_requested_at = None
        self.terminal_reason = None
        self.model_result = None
        self.future = None
        self._lock = threading.RLock()

    def request_cancel(self, reason='client_cancelled'):
        with self._lock:
            if self.finished_at is not None:
                return False
            if not self.cancel_event.is_set():
                self.cancel_reason = reason
                self.cancel_requested_at = self.clock()
                self.cancel_event.set()
            return True

    def stop_requested(self):
        with self._lock:
            if self.clock() >= self.deadline_at:
                self.request_cancel('deadline')
            return self.cancel_event.is_set()

    def checkpoint(self, phase=None):
        if self.stop_requested():
            raise GenerationAborted(self.cancel_reason or 'cancelled', self.request_id)
        if phase is not None:
            with self._lock:
                if phase != self.phase:
                    self.phase, self.last_progress_at = phase, self.clock()

    def observe_tokens(self, count):
        with self._lock:
            if count != self.generated_tokens:
                self.generated_tokens = int(count)
                if count > 0:
                    if self.first_token_at is None:
                        self.first_token_at = self.clock()
                    self.phase = 'decoding'
                self.last_progress_at = self.clock()

    def start(self):
        with self._lock:
            self.started_at = self.clock()
        self.checkpoint('starting')

    def finish(self, reason):
        with self._lock:
            self.finished_at = self.clock()
            self.terminal_reason = reason

    def snapshot(self):
        with self._lock:
            now = self.finished_at if self.finished_at is not None else self.clock()
            return {
                'request_id': self.request_id,
                'phase': 'cancelling' if self.cancel_event.is_set() and self.finished_at is None else self.phase,
                'underlying_phase': self.phase,
                'elapsed_total_s': max(0., now - self.received_at),
                'queue_wait_s': max(0., (self.started_at if self.started_at is not None else now) - self.received_at),
                'prompt_tokens': self.prompt_tokens, 'generated_tokens': self.generated_tokens,
                'first_token_latency_s': None if self.first_token_at is None else self.first_token_at - self.received_at,
                'last_progress_age_s': max(0., now - self.last_progress_at),
                'deadline_remaining_s': max(0., self.deadline_at - now),
                'cancel_pending': self.cancel_event.is_set() and self.finished_at is None,
                'cancel_reason': self.cancel_reason, 'terminal_reason': self.terminal_reason,
                'worker_started': self.started_at is not None,
                'worker_finished': self.finished_at is not None,
            }


class GenerationAdmission:
    def __init__(self, *, request_deadline_s=270., max_waiting_requests=0, clock=time.monotonic):
        if not math.isfinite(request_deadline_s) or request_deadline_s <= 0:
            raise ValueError('REQUEST_DEADLINE_MUST_BE_FINITE_POSITIVE')
        if type(max_waiting_requests) is not int or not 0 <= max_waiting_requests <= 64:
            raise ValueError('MAX_WAITING_REQUESTS_MUST_BE_INTEGER_0_TO_64')
        self.request_deadline_s = float(request_deadline_s)
        self.max_waiting_requests = max_waiting_requests
        self.clock = clock
        self._lock = threading.Lock()
        self._executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix='sat-model-generation')
        self._jobs = {}
        self._closed = False
        self._last_terminal = None
        self._counts = dict(accepted=0, rejected_busy=0, completed=0, failed=0, cancelled=0, timed_out=0)
        self._idle = threading.Event()
        self._idle.set()

    def submit(self, function):
        with self._lock:
            if self._closed or len(self._jobs) >= 1 + self.max_waiting_requests:
                self._counts['rejected_busy'] += 1
                raise ModelBusy('MODEL_BUSY')
            control = GenerationControl(self.request_deadline_s, clock=self.clock)
            self._jobs[control.request_id] = control
            self._idle.clear()
            self._counts['accepted'] += 1
            try:
                control.future = self._executor.submit(self._run, control, function)
            except BaseException:
                self._jobs.pop(control.request_id)
                if not self._jobs:
                    self._idle.set()
                raise
            return control

    def _run(self, control, function):
        outcome = 'failed'
        try:
            control.start()
            result = function(control)
            control.checkpoint()
            outcome = 'completed'
            return result
        except GenerationAborted as exc:
            outcome = 'timed_out' if exc.reason == 'deadline' else 'cancelled'
            raise
        finally:
            control.finish(outcome)
            # Only this actual worker-exit path refunds capacity. Cancelled
            # queued work retains a bounded slot until it is dequeued/skipped.
            with self._lock:
                self._jobs.pop(control.request_id, None)
                self._counts[outcome] += 1
                self._last_terminal = control.snapshot()
                if not self._jobs:
                    self._idle.set()

    def cancel(self, request_id, reason='client_cancelled'):
        with self._lock:
            control = self._jobs.get(request_id)
        return control is not None and control.request_cancel(reason)

    def snapshot(self):
        with self._lock:
            jobs = [c.snapshot() for c in self._jobs.values()]
            return {
                'busy': bool(jobs), 'available': not self._closed and len(jobs) < 1 + self.max_waiting_requests,
                'max_active': 1, 'max_waiting_requests': self.max_waiting_requests,
                'active_count': sum(j['worker_started'] for j in jobs),
                'queued_count': sum(not j['worker_started'] for j in jobs),
                'requests': jobs, 'counts': dict(self._counts), 'last_terminal': self._last_terminal,
                'request_deadline_s': self.request_deadline_s,
                'deadline_semantics': 'cooperative; current native/CUDA pass may overrun',
                'closed': self._closed,
            }

    def wait_for_idle(self, timeout=None):
        return self._idle.wait(timeout)

    def close(self):
        with self._lock:
            self._closed = True
            jobs = list(self._jobs.values())
        for control in jobs:
            control.request_cancel('service_shutdown')
        # No future.cancel(): a queued work item must still execute the cheap
        # abort checkpoint and refund its slot. This never kills running work.
        self._executor.shutdown(wait=False, cancel_futures=False)
