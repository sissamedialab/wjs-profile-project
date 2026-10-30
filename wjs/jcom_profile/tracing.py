"""OpenTelemetry tracing setup for the WJS app inside Janeway.

All logic is gated behind ``OTEL_PERFORMANCE_TRACING_ENABLED`` and/or
``OTEL_ERROR_TRACING_ENABLED`` and is a no-op when both are disabled.
Setup never raises into the request path: failures are logged and swallowed.
"""

import logging
import os
from typing import Callable, Optional

logger = logging.getLogger(__name__)

# Default configuration. Single source of truth; wjs/defaults/settings.py
# references these so the two paths cannot drift.
DEFAULT_SERVICE_NAME = "wjs"
DEFAULT_OTLP_ENDPOINT = "http://localhost:4318"
DEFAULT_SAMPLER_RATIO = "1.0"

_TRUTHY = {"1", "true", "yes", "on"}

# Module-level guard against double initialisation (gunicorn preload, autoreload,
# repeated imports). Set True once tracing is configured.
_CONFIGURED = False


def _env_truthy(value: Optional[str]) -> bool:
    """Return True when ``value`` is a truthy string flag."""
    return (value or "").strip().lower() in _TRUTHY


def performance_tracing_enabled() -> bool:
    """Return True when performance tracing is enabled via the environment."""
    return _env_truthy(os.environ.get("OTEL_PERFORMANCE_TRACING_ENABLED"))


def error_tracing_enabled() -> bool:
    """Return True when error tracing is enabled via the environment."""
    return _env_truthy(os.environ.get("OTEL_ERROR_TRACING_ENABLED"))


def tracing_enabled() -> bool:
    """Return True if either performance or error tracing is enabled."""
    return performance_tracing_enabled() or error_tracing_enabled()


def _sampler_ratio() -> float:
    """Read the sampler ratio from env, clamped to [0.0, 1.0]; fall back to the default on a bad value."""
    raw = os.environ.get("OTEL_TRACES_SAMPLER_RATIO", DEFAULT_SAMPLER_RATIO)
    try:
        value = float(raw)
    except (TypeError, ValueError):
        logger.warning("Invalid OTEL_TRACES_SAMPLER_RATIO %r; falling back to %s", raw, DEFAULT_SAMPLER_RATIO)
        value = float(DEFAULT_SAMPLER_RATIO)
    return max(0.0, min(1.0, value))


def _safe_instrument(name: str, fn: Callable[[], None]) -> None:
    """Run one instrumentor, logging and swallowing any failure."""
    try:
        fn()
    except Exception:  # noqa: B902 - one bad instrumentor must not block the others
        logger.warning("Could not instrument %s for tracing", name, exc_info=True)


def _instrument(performance_enabled: bool, error_enabled: bool) -> None:
    """Apply auto-instrumentation for the supported libraries (each guarded).

    Django and logging instrumentors are installed when either mode is enabled
    (Django surfaces exceptions as error spans; logging adds trace-id correlation
    useful in both modes). psycop, requests, and redis are performance-detail
    spans and are installed only when performance tracing is enabled.
    """

    def django():
        from opentelemetry.instrumentation.django import DjangoInstrumentor

        DjangoInstrumentor().instrument()

    def psycopg():
        from opentelemetry.instrumentation.psycopg import PsycopgInstrumentor

        PsycopgInstrumentor().instrument(skip_dep_check=True)

    def requests():
        from opentelemetry.instrumentation.requests import RequestsInstrumentor

        RequestsInstrumentor().instrument()

    def redis():
        from opentelemetry.instrumentation.redis import RedisInstrumentor

        RedisInstrumentor().instrument()

    def logging_():
        from opentelemetry.instrumentation.logging import LoggingInstrumentor

        LoggingInstrumentor().instrument(set_logging_format=False)

    if performance_enabled or error_enabled:
        _safe_instrument("django", django)
        _safe_instrument("logging", logging_)

    if performance_enabled:
        _safe_instrument("psycopg", psycopg)
        _safe_instrument("requests", requests)
        _safe_instrument("redis", redis)


_SENSITIVE_LOCAL_PATTERNS = (
    "password",
    "passwd",
    "pwd",
    "secret",
    "token",
    "authorization",
    "api_key",
    "apikey",
    "access_key",
    "private_key",
    "session",
    "cookie",
    "csrf",
)
_MAX_LOCAL_REPR = 200


def capture_locals_enabled() -> bool:
    """True when error stack traces should include (scrubbed) local variables. Defaults to True."""
    return _env_truthy(os.environ.get("OTEL_ERROR_TRACING_CAPTURE_LOCALS", "true"))


def _is_sensitive_local(name: str) -> bool:
    """True if a local variable name looks like it could hold a secret/PII."""
    lowered = name.lower()
    return any(pattern in lowered for pattern in _SENSITIVE_LOCAL_PATTERNS)


def _safe_local_repr(value: object) -> str:
    """repr() a local for display, never raising; truncate to _MAX_LOCAL_REPR."""
    try:
        rendered = repr(value)
    except Exception:  # noqa: B902 - a local's __repr__ must never break tracing
        return f"<unrepresentable: {type(value).__name__}>"
    if len(rendered) > _MAX_LOCAL_REPR:
        rendered = rendered[:_MAX_LOCAL_REPR] + "...<truncated>"
    return rendered


def _format_traceback_with_locals(exc: BaseException) -> str:
    """Format a traceback string including each frame's locals.

    Sensitive names are scrubbed, values are length-capped, and a local whose
    ``repr()`` raises is rendered as a placeholder so one bad object never
    breaks the whole trace. Locals are captured for the primary exception's
    frames only (chained-exception locals are intentionally not dumped).
    """
    import traceback

    parts = list(traceback.format_exception(type(exc), exc, exc.__traceback__))
    parts.append("\nLocal variables by frame (most recent call last):\n")
    for frame, lineno in traceback.walk_tb(exc.__traceback__):
        code = frame.f_code
        parts.append(f'  File "{code.co_filename}", line {lineno}, in {code.co_name}\n')
        for name, value in frame.f_locals.items():
            if _is_sensitive_local(name):
                rendered = "<scrubbed>"
            else:
                rendered = _safe_local_repr(value)
            parts.append(f"    {name} = {rendered}\n")
    return "".join(parts)


class OTelErrorLocalsMiddleware:
    """Django middleware: on a view exception, attach a scrubbed locals-bearing stack trace
    to the active OpenTelemetry span as the attribute ``exception.stacktrace_with_locals``.

    Wired into MIDDLEWARE only when error tracing AND OTEL_ERROR_TRACING_CAPTURE_LOCALS are on.
    If no span is active/recording (e.g. tracing off, or the span already ended), it is a safe no-op.
    """

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        return self.get_response(request)

    def process_exception(self, request, exception):
        try:
            from opentelemetry import trace

            span = trace.get_current_span()
            if span.is_recording():
                span.set_attribute(
                    "exception.stacktrace_with_locals",
                    _format_traceback_with_locals(exception),
                )
        except Exception:  # noqa: B902 - must never interfere with request error handling
            logger.warning("Failed to attach locals to error span", exc_info=True)
        return None


def configure_tracing() -> None:
    """Initialise OpenTelemetry tracing if enabled. No-op otherwise. Idempotent."""
    global _CONFIGURED
    if _CONFIGURED or not tracing_enabled():
        return
    try:
        import importlib.metadata

        from opentelemetry import trace
        from opentelemetry.exporter.otlp.proto.http.trace_exporter import (
            OTLPSpanExporter,
        )
        from opentelemetry.sdk.resources import SERVICE_NAME, SERVICE_VERSION, Resource
        from opentelemetry.sdk.trace import TracerProvider
        from opentelemetry.sdk.trace.export import BatchSpanProcessor
        from opentelemetry.sdk.trace.sampling import ParentBased, TraceIdRatioBased

        wjs_version = importlib.metadata.version("wjs.jcom_profile")

        service_name = os.environ.get("OTEL_SERVICE_NAME", DEFAULT_SERVICE_NAME)
        endpoint = os.environ.get("OTEL_EXPORTER_OTLP_ENDPOINT", DEFAULT_OTLP_ENDPOINT)
        traces_url = f"{endpoint.rstrip('/')}/v1/traces"

        resource = Resource.create(
            attributes={
                SERVICE_NAME: service_name,
                SERVICE_VERSION: wjs_version,
            },
        )
        provider = TracerProvider(
            resource=resource,
            sampler=ParentBased(TraceIdRatioBased(_sampler_ratio())),
        )
        exporter = OTLPSpanExporter(endpoint=traces_url)
        provider.add_span_processor(BatchSpanProcessor(exporter))
        trace.set_tracer_provider(provider)

        perf = performance_tracing_enabled()
        err = error_tracing_enabled()
        _instrument(perf, err)
        _CONFIGURED = True
        logger.info(
            "OpenTelemetry tracing enabled (service=%s, endpoint=%s, performance=%s, error=%s)",
            service_name,
            traces_url,
            perf,
            err,
        )
    except Exception:  # noqa: B902 - tracing must never break the app
        logger.exception("Failed to configure OpenTelemetry tracing; continuing without it")
