"""Exercise the real engine method wiring with CPU objects, never model weights."""
from contextlib import nullcontext
import importlib.util
from pathlib import Path
import sys
import threading
from types import SimpleNamespace

import pytest

from sat_sim_ml.generation_control import GenerationAborted, GenerationControl, ModelBusy
from sat_sim_ml.serving import LocalModelEngine


class Clock:
    now = 0.

    def __call__(self):
        return self.now

    def advance(self, seconds):
        self.now += seconds


class Tokens:
    def __init__(self, values, *, row=False):
        self.values = list(values)
        self.shape = (len(values),) if row else (1, len(values))
        self.device = 'cpu'
        self.to_calls = []

    def to(self, device):
        self.to_calls.append(device)
        self.device = device
        return self

    def __len__(self):
        return len(self.values)

    def __getitem__(self, item):
        if isinstance(item, tuple):
            assert item[0] == 0
            return Tokens(self.values[item[1]], row=True)
        return self.values[item]


class FakeTorch:
    bool = 'fake-bool-dtype'

    @staticmethod
    def inference_mode():
        return nullcontext()

    @staticmethod
    def ones_like(value):
        return 'fake-attention-mask'

    @staticmethod
    def full(shape, value, *, device, dtype):
        assert dtype == FakeTorch.bool
        return [value] * shape[0]


@pytest.fixture
def engine(monkeypatch):
    # Installing a small fake module verifies lazy Transformers use without
    # importing torch or allocating CUDA memory.
    monkeypatch.setitem(sys.modules, 'transformers', SimpleNamespace(
        StoppingCriteria=object, StoppingCriteriaList=list))
    result = LocalModelEngine.__new__(LocalModelEngine)
    result.torch = FakeTorch()
    result._lock = threading.Lock()
    result.max_context = 32
    result.tokenizer = SimpleNamespace(eos_token_id=2, encoded=Tokens([10, 11]), decoded=[])
    result.tokenizer.apply_chat_template = lambda *args, **kwargs: result.tokenizer.encoded

    def decode(value, **kwargs):
        result.tokenizer.decoded.append(value)
        return '{"ok":true}' if len(value) else ''

    result.tokenizer.decode = decode
    result.model = SimpleNamespace(calls=[])

    def generate(encoded, **kwargs):
        result.model.calls.append(kwargs)
        return Tokens([10, 11, 12, 2])

    result.model.generate = generate
    return result


def assert_lock_released(engine):
    assert engine._lock.acquire(blocking=False)
    engine._lock.release()


def test_context_budget_is_checked_before_cuda_transfer(engine):
    engine.max_context = 3
    with pytest.raises(ValueError, match='MODEL_CONTEXT_BUDGET_EXCEEDED'):
        engine.generate([], max_tokens=2)
    assert engine.tokenizer.encoded.to_calls == []
    assert engine.model.calls == []
    assert_lock_released(engine)


def test_direct_engine_contention_is_rejected_without_waiting(engine):
    engine._lock.acquire()
    try:
        with pytest.raises(ModelBusy):
            engine.generate([], max_tokens=2)
        assert engine.model.calls == []
    finally:
        engine._lock.release()


def test_transformers_stop_hook_observes_tokens_and_stops_on_absolute_deadline(engine):
    clock = Clock()
    control = GenerationControl(10., clock=clock)
    control.start()
    stop_observations = []

    def generate(encoded, **kwargs):
        clock.advance(11)
        stop_observations.append(kwargs['stopping_criteria'][0](Tokens([10, 11, 12]), None))
        return Tokens([10, 11, 12])

    engine.model.generate = generate
    with pytest.raises(GenerationAborted, match='deadline'):
        engine.generate([], max_tokens=2, control=control)
    assert stop_observations == [[True]]
    assert control.snapshot()['generated_tokens'] == 1
    assert control.snapshot()['prompt_tokens'] == 2
    assert engine.tokenizer.decoded == []  # Partial answer cannot become success.
    assert_lock_released(engine)


def test_cancellation_is_observed_after_a_long_prefix_callback(engine, monkeypatch):
    clock = Clock()
    control = GenerationControl(10., clock=clock)
    control.start()

    def prefix_factory(*args):
        def prefix(batch_id, sent):
            clock.advance(11)
            return [12]
        return prefix

    monkeypatch.setattr('sat_sim_ml.guided_decoding.prefix_constraint', prefix_factory)

    def generate(encoded, **kwargs):
        kwargs['prefix_allowed_tokens_fn'](0, Tokens([10, 11], row=True))
        raise AssertionError('expired callback cannot continue generation')

    engine.model.generate = generate
    with pytest.raises(GenerationAborted, match='deadline'):
        engine.generate([], max_tokens=2, response_schema={'type':'object'}, control=control)
    assert engine.tokenizer.decoded == []
    assert_lock_released(engine)


def test_success_keeps_completion_and_decoding_metadata(engine):
    control = GenerationControl(10.)
    control.start()

    def generate(encoded, **kwargs):
        assert kwargs['attention_mask'] == 'fake-attention-mask'
        assert kwargs['stopping_criteria'][0](Tokens([10, 11, 12, 2]), None) == [False]
        return Tokens([10, 11, 12, 2])

    engine.model.generate = generate
    result = engine.generate([], max_tokens=2, control=control)
    assert result['text'] == '{"ok":true}'
    assert result['prompt_tokens'] == 2 and result['completion_tokens'] == 2
    assert result['finish_reason'] == 'stop'
    assert result['structured_generation']['answer_substitution'] is False
    assert control.snapshot()['generated_tokens'] == 2
    assert engine.tokenizer.encoded.to_calls == ['cuda']  # Fake object only.
    assert_lock_released(engine)


def load_cli():
    path = Path(__file__).resolve().parents[1] / 'scripts' / 'serve_modelscope_model.py'
    spec = importlib.util.spec_from_file_location('isolated_modelscope_cli', path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.mark.parametrize('arguments', [
    ['--request-deadline-s', '0'], ['--request-deadline-s', 'nan'],
    ['--max-waiting-requests', '65'], ['--disconnect-poll-interval-s', 'inf'],
])
def test_cli_invalid_policy_is_rejected_before_engine_construction(monkeypatch, arguments):
    module = load_cli()
    called = []
    monkeypatch.setattr(module, 'LocalModelEngine', lambda *args, **kwargs: called.append(True))
    monkeypatch.setattr(sys, 'argv', ['serve_modelscope_model.py', *arguments])
    with pytest.raises(SystemExit) as exc:
        module.main()
    assert exc.value.code == 2 and called == []


def test_cli_help_explains_cooperative_limit_without_loading_engine(monkeypatch, capsys):
    module = load_cli()
    monkeypatch.setattr(module, 'LocalModelEngine', lambda *args, **kwargs: pytest.fail('no weights for --help'))
    monkeypatch.setattr(sys, 'argv', ['serve_modelscope_model.py', '--help'])
    with pytest.raises(SystemExit) as exc:
        module.main()
    assert exc.value.code == 0
    output = capsys.readouterr().out
    assert '--request-deadline-s' in output and 'cannot interrupt' in output


def test_cli_policy_reaches_one_loopback_app_without_starting_a_server(monkeypatch):
    module = load_cli()
    constructions, served = [], []
    engine = SimpleNamespace(model_name='protocol-only', provenance={'engine':'protocol_fixture'},
        generate=lambda *args, **kwargs: None)
    def construct(*args, **kwargs):
        constructions.append((args, kwargs))
        return engine
    def run(app, **kwargs):
        served.append((app, kwargs))
    monkeypatch.setattr(module, 'LocalModelEngine', construct)
    monkeypatch.setitem(sys.modules, 'uvicorn', SimpleNamespace(run=run))
    monkeypatch.setattr(sys, 'argv', ['serve_modelscope_model.py',
        '--request-deadline-s', '2.5', '--max-waiting-requests', '1',
        '--disconnect-poll-interval-s', '.02'])
    module.main()
    assert len(constructions) == 1 and len(served) == 1
    app, options = served[0]
    try:
        assert options == {'host':'127.0.0.1', 'port':8031, 'access_log':False}
        assert app.state.model_admission.request_deadline_s == 2.5
        assert app.state.model_admission.max_waiting_requests == 1
    finally:
        app.state.model_admission.close()
