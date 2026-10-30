import pytest
from django_q import worker
from opentelemetry import trace
from opentelemetry.propagate import inject
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import SimpleSpanProcessor
from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter
from opentelemetry.trace import SpanKind, StatusCode

from wjs.jcom_profile import tracing, tracing_djangoq


def sample_task_function():
    """Stand in for the function of a django-q task."""


@pytest.fixture
def tracing_on(monkeypatch):
    monkeypatch.setenv("OTEL_PERFORMANCE_TRACING_ENABLED", "true")
    monkeypatch.delenv("OTEL_ERROR_TRACING_ENABLED", raising=False)


@pytest.fixture
def tracing_off(monkeypatch):
    monkeypatch.delenv("OTEL_PERFORMANCE_TRACING_ENABLED", raising=False)
    monkeypatch.delenv("OTEL_ERROR_TRACING_ENABLED", raising=False)


@pytest.fixture
def exporter(mocker):
    """Collect, in memory, the spans of the tracer used for tasks.

    The global tracer provider can be set only once per process, so the tasks'
    tracer comes from a local provider instead.
    """
    span_exporter = InMemorySpanExporter()
    provider = TracerProvider()
    provider.add_span_processor(SimpleSpanProcessor(span_exporter))
    mocker.patch.object(tracing_djangoq, "_tracer", return_value=provider.get_tracer("test"))
    return span_exporter, provider


@pytest.fixture
def current_task(monkeypatch):
    task = {"id": "abc123", "name": "some-task", "func": sample_task_function, "group": "some-group"}
    monkeypatch.setattr(tracing_djangoq, "_current_task", task)
    return task


# ---------------------------------------------------------------------------
# Receiver of pre_enqueue (inject_trace_context)
# ---------------------------------------------------------------------------


def test_inject_trace_context_noop_when_disabled(tracing_off, exporter):
    _, provider = exporter
    task = {"name": "t"}
    with provider.get_tracer("test").start_as_current_span("request"):
        tracing_djangoq.inject_trace_context(sender="django_q", task=task)
    assert tracing_djangoq._CONTEXT_CARRIER_KEY not in task, "Nothing must be added to the task with tracing off"


def test_inject_trace_context_stores_current_span(tracing_on, exporter):
    _, provider = exporter
    task = {"name": "t"}
    with provider.get_tracer("test").start_as_current_span("request") as span:
        tracing_djangoq.inject_trace_context(sender="django_q", task=task)
    carrier = task[tracing_djangoq._CONTEXT_CARRIER_KEY]
    trace_id = format(span.get_span_context().trace_id, "032x")
    assert trace_id in carrier["traceparent"], "The task must carry the trace of the enqueuing span"


def test_inject_trace_context_without_current_span(tracing_on):
    task = {"name": "t"}
    tracing_djangoq.inject_trace_context(sender="django_q", task=task)
    assert tracing_djangoq._CONTEXT_CARRIER_KEY not in task, "No carrier is needed when there is no trace"


def test_inject_trace_context_swallows_errors(tracing_on, mocker):
    mocker.patch("opentelemetry.propagate.inject", side_effect=RuntimeError("boom"))
    task = {"name": "t"}
    # Must not raise
    tracing_djangoq.inject_trace_context(sender="django_q", task=task)
    assert tracing_djangoq._CONTEXT_CARRIER_KEY not in task, "A failure must leave the task untouched"


# ---------------------------------------------------------------------------
# Receiver of post_spawn (setup_worker_tracing)
# ---------------------------------------------------------------------------


def test_setup_worker_tracing_noop_when_disabled(tracing_off, mocker, monkeypatch):
    monkeypatch.setattr(worker, "TimeoutHandler", worker.TimeoutHandler)
    configure = mocker.patch.object(tracing, "configure_tracing")
    original = worker.TimeoutHandler

    tracing_djangoq.setup_worker_tracing(sender="django_q", proc_name="Process-1")

    configure.assert_not_called()
    assert worker.TimeoutHandler is original, "The worker must not be touched with tracing off"


def test_setup_worker_tracing_configures_and_wraps_tasks(tracing_on, mocker, monkeypatch):
    monkeypatch.setattr(worker, "TimeoutHandler", worker.TimeoutHandler)
    configure = mocker.patch.object(tracing, "configure_tracing")

    tracing_djangoq.setup_worker_tracing(sender="django_q", proc_name="Process-1")

    configure.assert_called_once()
    assert worker.TimeoutHandler is tracing_djangoq.TracedTimeoutHandler, "Tasks must run inside a span"


# ---------------------------------------------------------------------------
# Receiver of pre_execute (remember_task)
# ---------------------------------------------------------------------------


def test_remember_task(monkeypatch):
    monkeypatch.setattr(tracing_djangoq, "_current_task", None)
    task = {"name": "t"}
    tracing_djangoq.remember_task(sender="django_q", func=sample_task_function, task=task)
    assert tracing_djangoq._current_task is task, "The task about to run must be remembered"


# ---------------------------------------------------------------------------
# TracedTimeoutHandler
# ---------------------------------------------------------------------------


def test_traced_timeout_handler_wraps_task_in_span(exporter, current_task):
    span_exporter, _ = exporter

    with tracing_djangoq.TracedTimeoutHandler(-1):
        inside = trace.get_current_span()

    (span,) = span_exporter.get_finished_spans()
    func_repr = f"{__name__}.sample_task_function"
    assert span.name == f"task {func_repr}", "The span must be named after the task's function"
    assert span.kind == SpanKind.CONSUMER, "Task spans are consumer spans"
    assert span.attributes == {
        "django_q.task.id": "abc123",
        "django_q.task.name": "some-task",
        "django_q.task.func": func_repr,
        "django_q.task.group": "some-group",
    }, "The span must describe the task"
    assert inside.get_span_context() == span.get_span_context(), "The span must be current while the task runs"
    assert not trace.get_current_span().get_span_context().is_valid, "The span must not outlive the task"


def test_traced_timeout_handler_joins_enqueuer_trace(exporter, current_task):
    span_exporter, provider = exporter
    with provider.get_tracer("test").start_as_current_span("request") as request_span:
        carrier = {}
        inject(carrier)
    current_task[tracing_djangoq._CONTEXT_CARRIER_KEY] = carrier
    span_exporter.clear()

    with tracing_djangoq.TracedTimeoutHandler(-1):
        pass

    (span,) = span_exporter.get_finished_spans()
    assert span.context.trace_id == request_span.get_span_context().trace_id, "The task must join the request's trace"
    assert span.parent.span_id == request_span.get_span_context().span_id, "The request's span must be the parent"


def test_traced_timeout_handler_records_failure(exporter, current_task):
    span_exporter, _ = exporter

    with pytest.raises(ValueError, match="task failed"):
        with tracing_djangoq.TracedTimeoutHandler(-1):
            raise ValueError("task failed")

    (span,) = span_exporter.get_finished_spans()
    assert span.status.status_code == StatusCode.ERROR, "A failed task must give an error span"
    assert [event.name for event in span.events] == ["exception"], "The exception must be recorded"


def test_traced_timeout_handler_keeps_timeout(exporter, current_task, mocker):
    alarm = mocker.patch("django_q.timeout.signal.alarm")

    with tracing_djangoq.TracedTimeoutHandler(30):
        pass

    assert alarm.call_args_list == [mocker.call(30), mocker.call(0)], "The timeout must still be set and reset"


def test_traced_timeout_handler_runs_task_when_span_fails(current_task, mocker):
    mocker.patch.object(tracing_djangoq, "_tracer", side_effect=RuntimeError("boom"))
    ran = []

    with tracing_djangoq.TracedTimeoutHandler(-1):
        ran.append(True)

    assert ran == [True], "A tracing failure must not prevent the task from running"


def test_traced_timeout_handler_flushes(exporter, current_task, mocker):
    flush = mocker.patch.object(tracing_djangoq, "_flush")

    with tracing_djangoq.TracedTimeoutHandler(-1):
        pass

    flush.assert_called_once()


def test_connect_signals_is_idempotent():
    from django_q.signals import post_spawn, pre_enqueue, pre_execute

    tracing_djangoq.connect_signals()
    tracing_djangoq.connect_signals()

    for signal, dispatch_uid in (
        (pre_enqueue, "wjs_tracing_inject_trace_context"),
        (post_spawn, "wjs_tracing_setup_worker_tracing"),
        (pre_execute, "wjs_tracing_remember_task"),
    ):
        # Each entry of Signal.receivers starts with the lookup key (dispatch_uid, sender id)
        connected = [entry for entry in signal.receivers if entry[0][0] == dispatch_uid]
        assert len(connected) == 1, f"{dispatch_uid} must be connected once"
