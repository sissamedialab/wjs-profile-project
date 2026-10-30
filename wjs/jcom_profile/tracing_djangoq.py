"""OpenTelemetry tracing of django-q tasks.

The web process sets up tracing in wjs/channels/asgi.py, but the tasks run by
the qcluster are executed in separate worker processes (started by
``manage.py qcluster``), which need their own setup. This module hooks into
django-q's signals:

- ``pre_enqueue`` (sent in the process that enqueues the task, e.g. a web
  request): store the current trace context in the task, so that the task's
  span joins the trace of the request that enqueued it;
- ``post_spawn`` (sent in each new worker process): configure tracing for the
  process and start tracing the tasks it runs;
- ``pre_execute`` (sent in the worker just before running a task): remember
  the task, so that its span can be named after it.

django-q sends no signal in the worker when a task ends (``post_execute`` is
sent by the monitor, which is another process), so the task's span is opened
and closed by a subclass of django-q's ``TimeoutHandler``: in the worker, that
context manager surrounds exactly the call to the task's function.

Like the rest of the tracing setup, everything here is a no-op when tracing is
disabled, and never raises into the task execution.
"""

import logging
from typing import Optional

from django_q.signals import post_spawn, pre_enqueue, pre_execute
from django_q.timeout import TimeoutHandler
from django_q.utils import get_func_repr

from wjs.jcom_profile import tracing

logger = logging.getLogger(__name__)

# Key, in the django-q task dict, of the carrier of the trace context. django-q
# saves only the keys it knows about, so this one just travels with the task.
_CONTEXT_CARRIER_KEY = "otel_context"

# Upper bound for flushing the spans of a task once it has run.
_FLUSH_TIMEOUT_MILLIS = 5000

# The task the worker is about to run (set by the pre_execute signal).
_current_task: Optional[dict] = None


def inject_trace_context(sender, task: dict, **kwargs) -> None:
    """Store the current trace context in a task that is being enqueued."""
    if not tracing.tracing_enabled():
        return
    try:
        from opentelemetry.propagate import inject

        carrier = {}
        inject(carrier)
        if carrier:
            task[_CONTEXT_CARRIER_KEY] = carrier
    except Exception:  # noqa: B902 - tracing must never prevent enqueuing a task
        logger.warning("Could not store the trace context in task %s", task.get("name"), exc_info=True)


def setup_worker_tracing(sender, proc_name: str, **kwargs) -> None:
    """Configure tracing in a newly spawned worker and trace each task it runs."""
    if not tracing.tracing_enabled():
        return
    tracing.configure_tracing()
    # Imported here, in the worker: importing django_q.worker runs django.setup()
    # if the app registry is not ready yet.
    from django_q import worker

    # worker() looks TimeoutHandler up among its module globals at each task.
    worker.TimeoutHandler = TracedTimeoutHandler


def remember_task(sender, func, task: dict, **kwargs) -> None:
    """Remember the task the worker is about to run."""
    global _current_task
    _current_task = task


def _tracer():
    """Return the tracer for task spans."""
    from opentelemetry import trace

    return trace.get_tracer(__name__)


def _flush() -> None:
    """Export the spans that are still buffered.

    Worker processes end with os._exit() (that's how multiprocessing ends its
    children), which skips the atexit shutdown of the tracer provider: without
    flushing, the spans of the last tasks run by a recycled worker would be lost.
    """
    from opentelemetry import trace

    force_flush = getattr(trace.get_tracer_provider(), "force_flush", None)
    if force_flush is not None:
        force_flush(timeout_millis=_FLUSH_TIMEOUT_MILLIS)


class TracedTimeoutHandler(TimeoutHandler):
    """django-q's TimeoutHandler that also wraps the task's execution in a span.

    Installed in the worker processes by setup_worker_tracing().
    """

    _span = None
    _context_token = None

    def __enter__(self):
        try:
            self._start_span(_current_task or {})
        except Exception:  # noqa: B902 - tracing must never prevent running a task
            logger.warning("Could not start the span of a task", exc_info=True)
        return super().__enter__()

    def __exit__(self, exc_type, exc_value, traceback):
        result = super().__exit__(exc_type, exc_value, traceback)
        try:
            self._end_span(exc_value)
        except Exception:  # noqa: B902 - tracing must never alter the outcome of a task
            logger.warning("Could not end the span of a task", exc_info=True)
        return result

    def _start_span(self, task: dict) -> None:
        """Open the task's span, child of the enqueuer's span if any, and make it current."""
        from opentelemetry import context, trace
        from opentelemetry.propagate import extract

        func_repr = get_func_repr(task.get("func")) or "unknown"
        attributes = {
            "django_q.task.id": str(task.get("id", "")),
            "django_q.task.name": str(task.get("name", "")),
            "django_q.task.func": func_repr,
        }
        if task.get("group"):
            attributes["django_q.task.group"] = str(task["group"])
        self._span = _tracer().start_span(
            f"task {func_repr}",
            context=extract(task.get(_CONTEXT_CARRIER_KEY) or {}),
            kind=trace.SpanKind.CONSUMER,
            attributes=attributes,
        )
        self._context_token = context.attach(trace.set_span_in_context(self._span))

    def _end_span(self, exc_value: Optional[BaseException]) -> None:
        """Close the task's span, recording the exception that ended the task, if any."""
        if self._span is None:
            return
        from opentelemetry import context
        from opentelemetry.trace import Status, StatusCode

        if exc_value is not None:
            self._span.record_exception(exc_value)
            self._span.set_status(Status(StatusCode.ERROR, str(exc_value)))
        context.detach(self._context_token)
        self._span.end()
        self._span = self._context_token = None
        _flush()


def connect_signals() -> None:
    """Connect the receivers above to django-q's signals."""
    pre_enqueue.connect(inject_trace_context, dispatch_uid="wjs_tracing_inject_trace_context")
    post_spawn.connect(setup_worker_tracing, dispatch_uid="wjs_tracing_setup_worker_tracing")
    pre_execute.connect(remember_task, dispatch_uid="wjs_tracing_remember_task")
