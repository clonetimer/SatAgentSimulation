"""Controlled CPU fixtures verify scheduling, never actual model performance."""
from concurrent.futures import ThreadPoolExecutor
import asyncio
import io
import json
import threading
from urllib.error import HTTPError

from fastapi.testclient import TestClient
import httpx
import pytest

from sat_sim.llm_backends import OpenAIChatCompletionsBackend
from sat_sim_ml.generation_control import GenerationAborted, GenerationAdmission, GenerationControl, ModelBusy
from sat_sim_ml.serving import create_model_app


class Clock:
    def __init__(self):
        self.now = 0.
        self.lock = threading.Lock()

    def __call__(self):
        with self.lock:
            return self.now

    def advance(self, seconds):
        with self.lock:
            self.now += seconds


class BlockingEngine:
    model_name = 'protocol-not-a-real-model'
    provenance = {'engine':'protocol_fixture', 'adapter_loaded':False}

    def __init__(self):
        self.started = threading.Event()
        self.release = threading.Event()
        self.control = None
        self.calls = 0

    def generate(self, messages, *, max_tokens, response_schema=None, control=None):
        self.calls += 1
        self.control = control
        control.checkpoint('prefill_or_first_token')
        self.started.set()
        assert self.release.wait(5), 'test fixture must always be released'
        control.checkpoint()
        control.observe_tokens(1)
        return {'text':'{"ok":true}', 'prompt_tokens':4, 'completion_tokens':1,
                'elapsed_s':.1, 'finish_reason':'stop'}


BODY = {'model':BlockingEngine.model_name, 'messages':[{'role':'user','content':'test'}]}


def test_default_admission_rejects_second_job_without_queueing():
    manager = GenerationAdmission()
    started, release = threading.Event(), threading.Event()
    def work(control):
        started.set()
        assert release.wait(5)
        return 'done'
    try:
        first = manager.submit(work)
        assert started.wait(2)
        with pytest.raises(ModelBusy):
            manager.submit(work)
        assert manager.snapshot()['active_count'] == 1
        assert manager.snapshot()['queued_count'] == 0
        release.set()
        assert first.future.result(2) == 'done'
        assert manager.wait_for_idle(2)
        assert manager.snapshot()['counts']['completed'] == 1
    finally:
        release.set()
        manager.close()


def test_cancelled_queued_job_keeps_bounded_slot_until_dequeued_and_never_calls_engine():
    manager = GenerationAdmission(max_waiting_requests=1)
    started, release, unwanted = threading.Event(), threading.Event(), threading.Event()
    def first_work(control):
        started.set()
        assert release.wait(5)
        return 'first'
    try:
        first = manager.submit(first_work)
        assert started.wait(2)
        queued = manager.submit(lambda control: unwanted.set())
        assert manager.cancel(queued.request_id)
        with pytest.raises(ModelBusy):
            manager.submit(lambda control: None)
        assert manager.snapshot()['queued_count'] == 1
        release.set()
        assert first.future.result(2) == 'first'
        with pytest.raises(GenerationAborted):
            queued.future.result(2)
        assert not unwanted.is_set()
        assert manager.wait_for_idle(2)
        assert manager.snapshot()['counts']['cancelled'] == 1
    finally:
        release.set()
        manager.close()


def test_queued_deadline_is_absolute_and_expired_work_never_runs():
    clock = Clock()
    manager = GenerationAdmission(request_deadline_s=10., max_waiting_requests=1, clock=clock)
    started, release, unwanted = threading.Event(), threading.Event(), threading.Event()
    def work(control):
        started.set()
        assert release.wait(5)
    try:
        first = manager.submit(work)
        assert started.wait(2)
        queued = manager.submit(lambda control: unwanted.set())
        clock.advance(11)
        release.set()
        for ticket in (first, queued):
            with pytest.raises(GenerationAborted, match='deadline'):
                ticket.future.result(2)
        assert not unwanted.is_set()
        assert manager.wait_for_idle(2)
        assert manager.snapshot()['counts']['timed_out'] == 2
    finally:
        release.set()
        manager.close()


def test_health_and_models_remain_responsive_while_busy_and_api_stays_compatible():
    engine = BlockingEngine()
    with TestClient(create_model_app(engine)) as client, ThreadPoolExecutor(max_workers=3) as pool:
        pending = pool.submit(client.post, '/v1/chat/completions', json=BODY)
        try:
            assert engine.started.wait(2)
            busy = pool.submit(client.post, '/v1/chat/completions', json=BODY).result(2)
            assert busy.status_code == 429
            assert busy.headers['retry-after'] == '1'
            health = pool.submit(client.get, '/health').result(2).json()
            assert health['ok'] is True and health['busy'] is True and health['available'] is False
            assert health['requests'][0]['underlying_phase'] == 'prefill_or_first_token'
            assert pool.submit(client.get, '/v1/models').result(2).status_code == 200
            engine.release.set()
            response = pending.result(2)
            assert response.status_code == 200
            result = response.json()
            assert result['choices'][0]['message']['content'] == '{"ok":true}'
            assert result['usage'] == {'prompt_tokens':4, 'completion_tokens':1}
            assert result['sat_sim']['template_fallback_used'] is False
            assert response.headers['x-request-id'] == result['id']
            assert client.app.state.model_admission.wait_for_idle(2)
        finally:
            engine.release.set()


def test_http_deadline_does_not_release_a_still_running_worker():
    clock, engine = Clock(), BlockingEngine()
    app = create_model_app(engine, request_deadline_s=10., disconnect_poll_interval_s=.005, clock=clock)
    with TestClient(app) as client, ThreadPoolExecutor(max_workers=2) as pool:
        pending = pool.submit(client.post, '/v1/chat/completions', json=BODY)
        try:
            assert engine.started.wait(2)
            clock.advance(11)
            timeout = pending.result(2)
            assert timeout.status_code == 504
            assert timeout.json()['detail']['reason_code'] == 'MODEL_REQUEST_DEADLINE_EXCEEDED'
            assert timeout.json()['detail']['worker_may_still_be_running'] is True
            health = client.get('/health').json()
            assert health['busy'] is True and health['requests'][0]['cancel_pending'] is True
            assert client.post('/v1/chat/completions', json=BODY).status_code == 429
            engine.release.set()
            assert app.state.model_admission.wait_for_idle(2)
            assert client.get('/health').json()['available'] is True
            assert app.state.model_admission.snapshot()['counts']['timed_out'] == 1
        finally:
            engine.release.set()


def test_manual_cancel_returns_before_worker_exit_without_hiding_busy():
    engine = BlockingEngine()
    app = create_model_app(engine, disconnect_poll_interval_s=.005)
    with TestClient(app) as client, ThreadPoolExecutor(max_workers=2) as pool:
        pending = pool.submit(client.post, '/v1/chat/completions', json=BODY)
        try:
            assert engine.started.wait(2)
            request_id = engine.control.request_id
            assert client.post(f'/v1/requests/{request_id}/cancel').status_code == 202
            assert pending.result(2).status_code == 409
            assert client.get('/health').json()['busy'] is True
            assert client.post('/v1/chat/completions', json=BODY).status_code == 429
            engine.release.set()
            assert app.state.model_admission.wait_for_idle(2)
            assert client.post(f'/v1/requests/{request_id}/cancel').status_code == 404
        finally:
            engine.release.set()


def test_disconnect_is_cooperative_and_does_not_cancel_worker_future(monkeypatch):
    from starlette.requests import Request
    disconnected = threading.Event()
    async def probe(self):
        return disconnected.is_set()
    monkeypatch.setattr(Request, 'is_disconnected', probe)
    engine = BlockingEngine()
    app = create_model_app(engine, disconnect_poll_interval_s=.005)
    with TestClient(app) as client, ThreadPoolExecutor(max_workers=2) as pool:
        pending = pool.submit(client.post, '/v1/chat/completions', json=BODY)
        try:
            assert engine.started.wait(2)
            disconnected.set()
            response = pending.result(2)
            assert response.status_code == 409
            assert response.json()['detail']['cancel_reason'] == 'client_disconnected'
            assert not engine.control.future.cancelled()
            assert client.get('/health').json()['busy'] is True
            engine.release.set()
            assert app.state.model_admission.wait_for_idle(2)
        finally:
            engine.release.set()


def test_worker_error_refunds_once_and_next_request_can_run():
    manager = GenerationAdmission()
    def fail(control):
        raise ValueError('expected fixture failure')
    try:
        with pytest.raises(ValueError):
            manager.submit(fail).future.result(2)
        assert manager.wait_for_idle(2)
        assert manager.submit(lambda control: 'next').future.result(2) == 'next'
        assert manager.wait_for_idle(2)
        assert manager.snapshot()['counts']['failed'] == 1
        assert manager.snapshot()['counts']['completed'] == 1
    finally:
        manager.close()


def test_service_close_cancels_cooperatively_and_retains_running_slot_until_exit():
    manager = GenerationAdmission(max_waiting_requests=1)
    started, release, unwanted = threading.Event(), threading.Event(), threading.Event()
    def work(control):
        started.set()
        assert release.wait(5)
        return 'cannot become success after shutdown'
    try:
        active = manager.submit(work)
        assert started.wait(2)
        queued = manager.submit(lambda control: unwanted.set())
        manager.close()
        snapshot = manager.snapshot()
        assert snapshot['closed'] is True and snapshot['busy'] is True
        assert snapshot['available'] is False and snapshot['queued_count'] == 1
        with pytest.raises(ModelBusy):
            manager.submit(lambda control: None)
        assert not active.future.cancelled() and not queued.future.cancelled()
        release.set()
        for ticket in (active, queued):
            with pytest.raises(GenerationAborted, match='service_shutdown'):
                ticket.future.result(2)
        assert manager.wait_for_idle(2) and not unwanted.is_set()
        assert manager.snapshot()['counts']['cancelled'] == 2
    finally:
        release.set()
        manager.close()


def test_progress_uses_monotonic_time_and_keeps_first_token_timestamp_at_zero():
    clock = Clock()
    control = GenerationControl(10, clock=clock)
    control.start()
    control.observe_tokens(1)
    clock.advance(2)
    control.observe_tokens(2)
    snapshot = control.snapshot()
    assert snapshot['first_token_latency_s'] == 0
    assert snapshot['queue_wait_s'] == 0
    assert snapshot['generated_tokens'] == 2
    clock.advance(3)
    control.request_cancel()
    assert control.snapshot()['last_progress_age_s'] == 3


@pytest.mark.parametrize('options', [
    {'request_deadline_s':0}, {'request_deadline_s':float('inf')},
    {'request_deadline_s':float('nan')}, {'max_waiting_requests':-1},
    {'max_waiting_requests':True}, {'max_waiting_requests':65},
])
def test_invalid_admission_policy_rejected_before_any_work(options):
    with pytest.raises(ValueError):
        GenerationAdmission(**options)


@pytest.mark.parametrize('controlled_probe', [False, True])
def test_cancelling_the_http_task_does_not_cancel_the_worker_future(monkeypatch, controlled_probe):
    # Starlette's real polling implementation uses its own already-cancelled
    # CancelScope. Keep this test specifically about cancellation of our HTTP
    # waiter; the separate disconnect test controls a positive receive probe.
    from starlette.requests import Request
    async def connected(self):
        return False
    if controlled_probe:
        monkeypatch.setattr(Request, 'is_disconnected', connected)
    async def run():
        engine = BlockingEngine()
        app = create_model_app(engine, disconnect_poll_interval_s=.005)
        transport = httpx.ASGITransport(app=app)
        try:
            async with httpx.AsyncClient(transport=transport, base_url='http://fixture') as client:
                pending = asyncio.create_task(client.post('/v1/chat/completions', json=BODY))
                assert await asyncio.to_thread(engine.started.wait, 2)
                pending.cancel()
                with pytest.raises(asyncio.CancelledError):
                    await pending
                assert engine.control.cancel_reason == 'client_disconnected'
                assert not engine.control.future.cancelled()
                assert app.state.model_admission.snapshot()['busy'] is True
                assert (await client.post('/v1/chat/completions', json=BODY)).status_code == 429
        finally:
            engine.release.set()
            assert await asyncio.to_thread(app.state.model_admission.wait_for_idle, 2)
            app.state.model_admission.close()
    asyncio.run(run())


def test_extra_request_fields_stay_forbidden_and_never_reach_the_engine():
    engine = BlockingEngine()
    with TestClient(create_model_app(engine)) as client:
        response = client.post('/v1/chat/completions', json={**BODY, 'max_time':1})
        assert response.status_code == 422
        assert engine.calls == 0


@pytest.mark.parametrize('status,reason', [
    (429, 'MODEL_BUSY'), (504, 'MODEL_REQUEST_DEADLINE_EXCEEDED'),
    (409, 'MODEL_REQUEST_CANCELLED'),
])
def test_control_errors_never_retry_without_the_requested_schema(monkeypatch, status, reason):
    requests = []
    def reject(request, **kwargs):
        requests.append(json.loads(request.data))
        body = json.dumps({'detail':{'reason_code':reason}}).encode()
        raise HTTPError(request.full_url, status, reason, {}, io.BytesIO(body))
    monkeypatch.setattr('urllib.request.urlopen', reject)
    result = OpenAIChatCompletionsBackend(model='protocol-only', base_url='http://localhost:1',
        structured_output='json_schema').generate_text('test')
    assert not result.ok and len(requests) == 1
    assert requests[0]['response_format']['type'] == 'json_schema'
