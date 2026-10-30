"""Unit tests for OpenTelemetry tracing configuration."""

import pytest

from wjs.jcom_profile import tracing


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        ("true", True),
        ("True", True),
        ("1", True),
        ("yes", True),
        ("on", True),
        ("false", False),
        ("0", False),
        ("", False),
        (None, False),
    ],
)
def test_env_truthy(value, expected):
    assert tracing._env_truthy(value) is expected


# ---------------------------------------------------------------------------
# Predicate tests for the two new flags
# ---------------------------------------------------------------------------


def test_performance_tracing_enabled_reads_env(monkeypatch):
    monkeypatch.setenv("OTEL_PERFORMANCE_TRACING_ENABLED", "true")
    assert tracing.performance_tracing_enabled() is True
    monkeypatch.setenv("OTEL_PERFORMANCE_TRACING_ENABLED", "false")
    assert tracing.performance_tracing_enabled() is False
    monkeypatch.delenv("OTEL_PERFORMANCE_TRACING_ENABLED", raising=False)
    assert tracing.performance_tracing_enabled() is False


def test_error_tracing_enabled_reads_env(monkeypatch):
    monkeypatch.setenv("OTEL_ERROR_TRACING_ENABLED", "true")
    assert tracing.error_tracing_enabled() is True
    monkeypatch.setenv("OTEL_ERROR_TRACING_ENABLED", "false")
    assert tracing.error_tracing_enabled() is False
    monkeypatch.delenv("OTEL_ERROR_TRACING_ENABLED", raising=False)
    assert tracing.error_tracing_enabled() is False


def test_tracing_enabled_is_or_of_both(monkeypatch):
    """tracing_enabled() is True if either performance or error tracing is on."""
    monkeypatch.delenv("OTEL_PERFORMANCE_TRACING_ENABLED", raising=False)
    monkeypatch.delenv("OTEL_ERROR_TRACING_ENABLED", raising=False)
    assert tracing.tracing_enabled() is False

    monkeypatch.setenv("OTEL_PERFORMANCE_TRACING_ENABLED", "true")
    monkeypatch.delenv("OTEL_ERROR_TRACING_ENABLED", raising=False)
    assert tracing.tracing_enabled() is True

    monkeypatch.delenv("OTEL_PERFORMANCE_TRACING_ENABLED", raising=False)
    monkeypatch.setenv("OTEL_ERROR_TRACING_ENABLED", "true")
    assert tracing.tracing_enabled() is True

    monkeypatch.setenv("OTEL_PERFORMANCE_TRACING_ENABLED", "true")
    monkeypatch.setenv("OTEL_ERROR_TRACING_ENABLED", "true")
    assert tracing.tracing_enabled() is True


# ---------------------------------------------------------------------------
# Instrumentor gating — core feature
# ---------------------------------------------------------------------------


def test_instrument_error_only_installs_django_and_logging(mocker):
    """error-only mode: Django + logging instrumented; psycopg/requests/redis NOT."""
    django_mock = mocker.patch("opentelemetry.instrumentation.django.DjangoInstrumentor")
    psycopg_mock = mocker.patch("opentelemetry.instrumentation.psycopg.PsycopgInstrumentor")
    requests_mock = mocker.patch("opentelemetry.instrumentation.requests.RequestsInstrumentor")
    redis_mock = mocker.patch("opentelemetry.instrumentation.redis.RedisInstrumentor")
    logging_mock = mocker.patch("opentelemetry.instrumentation.logging.LoggingInstrumentor")

    tracing._instrument(performance_enabled=False, error_enabled=True)

    django_mock.return_value.instrument.assert_called_once()
    logging_mock.return_value.instrument.assert_called_once()
    psycopg_mock.return_value.instrument.assert_not_called()
    requests_mock.return_value.instrument.assert_not_called()
    redis_mock.return_value.instrument.assert_not_called()


def test_instrument_performance_only_installs_all_five(mocker):
    """performance-only mode: all five instrumentors installed."""
    django_mock = mocker.patch("opentelemetry.instrumentation.django.DjangoInstrumentor")
    psycopg_mock = mocker.patch("opentelemetry.instrumentation.psycopg.PsycopgInstrumentor")
    requests_mock = mocker.patch("opentelemetry.instrumentation.requests.RequestsInstrumentor")
    redis_mock = mocker.patch("opentelemetry.instrumentation.redis.RedisInstrumentor")
    logging_mock = mocker.patch("opentelemetry.instrumentation.logging.LoggingInstrumentor")

    tracing._instrument(performance_enabled=True, error_enabled=False)

    django_mock.return_value.instrument.assert_called_once()
    psycopg_mock.return_value.instrument.assert_called_once()
    requests_mock.return_value.instrument.assert_called_once()
    redis_mock.return_value.instrument.assert_called_once()
    logging_mock.return_value.instrument.assert_called_once()


def test_instrument_both_installs_all_five(mocker):
    """Both modes enabled: all five instrumentors installed."""
    django_mock = mocker.patch("opentelemetry.instrumentation.django.DjangoInstrumentor")
    psycopg_mock = mocker.patch("opentelemetry.instrumentation.psycopg.PsycopgInstrumentor")
    requests_mock = mocker.patch("opentelemetry.instrumentation.requests.RequestsInstrumentor")
    redis_mock = mocker.patch("opentelemetry.instrumentation.redis.RedisInstrumentor")
    logging_mock = mocker.patch("opentelemetry.instrumentation.logging.LoggingInstrumentor")

    tracing._instrument(performance_enabled=True, error_enabled=True)

    django_mock.return_value.instrument.assert_called_once()
    psycopg_mock.return_value.instrument.assert_called_once()
    requests_mock.return_value.instrument.assert_called_once()
    redis_mock.return_value.instrument.assert_called_once()
    logging_mock.return_value.instrument.assert_called_once()


# ---------------------------------------------------------------------------
# configure_tracing high-level tests
# ---------------------------------------------------------------------------


def test_configure_tracing_noop_when_disabled(monkeypatch, mocker):
    monkeypatch.delenv("OTEL_PERFORMANCE_TRACING_ENABLED", raising=False)
    monkeypatch.delenv("OTEL_ERROR_TRACING_ENABLED", raising=False)
    monkeypatch.setattr(tracing, "_CONFIGURED", False)
    instrument = mocker.patch.object(tracing, "_instrument")
    set_provider = mocker.patch("opentelemetry.trace.set_tracer_provider")

    tracing.configure_tracing()

    instrument.assert_not_called()
    set_provider.assert_not_called()
    assert tracing._CONFIGURED is False


def test_configure_tracing_idempotent(monkeypatch, mocker):
    monkeypatch.setenv("OTEL_PERFORMANCE_TRACING_ENABLED", "true")
    monkeypatch.setattr(tracing, "_CONFIGURED", True)
    instrument = mocker.patch.object(tracing, "_instrument")
    set_provider = mocker.patch("opentelemetry.trace.set_tracer_provider")

    tracing.configure_tracing()

    instrument.assert_not_called()
    set_provider.assert_not_called()


def test_configure_tracing_enabled_sets_provider(monkeypatch, mocker):
    monkeypatch.setenv("OTEL_PERFORMANCE_TRACING_ENABLED", "true")
    monkeypatch.setenv("OTEL_SERVICE_NAME", "wjs")
    monkeypatch.setenv("OTEL_EXPORTER_OTLP_ENDPOINT", "http://jaeger:4318")
    monkeypatch.setenv("OTEL_TRACES_SAMPLER_RATIO", "1.0")
    monkeypatch.setattr(tracing, "_CONFIGURED", False)

    set_provider = mocker.patch("opentelemetry.trace.set_tracer_provider")
    exporter = mocker.patch("opentelemetry.exporter.otlp.proto.http.trace_exporter.OTLPSpanExporter")
    instrument = mocker.patch.object(tracing, "_instrument")

    tracing.configure_tracing()

    set_provider.assert_called_once()
    instrument.assert_called_once()
    assert tracing._CONFIGURED is True
    # endpoint is normalised to the /v1/traces path
    _, kwargs = exporter.call_args
    assert kwargs["endpoint"] == "http://jaeger:4318/v1/traces"


def test_configure_tracing_swallows_errors(monkeypatch, mocker):
    monkeypatch.setenv("OTEL_PERFORMANCE_TRACING_ENABLED", "true")
    monkeypatch.setattr(tracing, "_CONFIGURED", False)
    mocker.patch("opentelemetry.sdk.trace.TracerProvider", side_effect=RuntimeError("boom"))

    # Must not raise.
    tracing.configure_tracing()
    assert tracing._CONFIGURED is False


def test_safe_instrument_swallows_errors():
    calls = []

    def boom():
        raise RuntimeError("nope")

    # Must not raise even though the callable fails.
    tracing._safe_instrument("thing", boom)
    tracing._safe_instrument("ok", lambda: calls.append(1))
    assert calls == [1]


def test_sampler_ratio_valid(monkeypatch):
    monkeypatch.setenv("OTEL_TRACES_SAMPLER_RATIO", "0.25")
    assert tracing._sampler_ratio() == 0.25


def test_sampler_ratio_clamps_above_one(monkeypatch):
    monkeypatch.setenv("OTEL_TRACES_SAMPLER_RATIO", "1.5")
    assert tracing._sampler_ratio() == 1.0


def test_sampler_ratio_clamps_below_zero(monkeypatch):
    monkeypatch.setenv("OTEL_TRACES_SAMPLER_RATIO", "-0.1")
    assert tracing._sampler_ratio() == 0.0


def test_sampler_ratio_falls_back_on_bad_value(monkeypatch):
    monkeypatch.setenv("OTEL_TRACES_SAMPLER_RATIO", "abc")
    assert tracing._sampler_ratio() == float(tracing.DEFAULT_SAMPLER_RATIO)


def test_settings_expose_otel_defaults():
    from django.conf import settings

    assert settings.OTEL_SERVICE_NAME == tracing.DEFAULT_SERVICE_NAME
    assert settings.OTEL_EXPORTER_OTLP_ENDPOINT == tracing.DEFAULT_OTLP_ENDPOINT
    assert settings.OTEL_TRACES_SAMPLER_RATIO == tracing.DEFAULT_SAMPLER_RATIO
    # Disabled by default unless the environment opts in.
    assert tracing._env_truthy(settings.OTEL_PERFORMANCE_TRACING_ENABLED) is False
    assert tracing._env_truthy(settings.OTEL_ERROR_TRACING_ENABLED) is False
    # Old single-flag must be gone
    assert not hasattr(settings, "OTEL_TRACES_ENABLED")


# ---------------------------------------------------------------------------
# capture_locals_enabled predicate
# ---------------------------------------------------------------------------


def test_capture_locals_enabled_default_true(monkeypatch):
    """Defaults to True when env var is unset."""
    monkeypatch.delenv("OTEL_ERROR_TRACING_CAPTURE_LOCALS", raising=False)
    assert tracing.capture_locals_enabled() is True


def test_capture_locals_enabled_true(monkeypatch):
    monkeypatch.setenv("OTEL_ERROR_TRACING_CAPTURE_LOCALS", "true")
    assert tracing.capture_locals_enabled() is True


def test_capture_locals_enabled_false(monkeypatch):
    monkeypatch.setenv("OTEL_ERROR_TRACING_CAPTURE_LOCALS", "false")
    assert tracing.capture_locals_enabled() is False


# ---------------------------------------------------------------------------
# _is_sensitive_local name detection
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "name",
    [
        "password",
        "user_password",
        "PASSWORD",
        "passwd",
        "pwd",
        "secret",
        "my_secret_key",
        "token",
        "access_token",
        "authorization",
        "api_key",
        "apikey",
        "access_key",
        "private_key",
        "session",
        "session_id",
        "cookie",
        "csrf",
        "csrf_token",
    ],
)
def test_is_sensitive_local_true(name):
    assert tracing._is_sensitive_local(name) is True


@pytest.mark.parametrize(
    "name",
    [
        "article_id",
        "count",
        "journal_code",
        "user_id",
        "title",
        "authors",
        "submission",
    ],
)
def test_is_sensitive_local_false(name):
    assert tracing._is_sensitive_local(name) is False


# ---------------------------------------------------------------------------
# _format_traceback_with_locals
# ---------------------------------------------------------------------------


def test_format_traceback_with_locals_contains_traceback_and_benign():
    """Benign local variable name+value appears in the formatted output."""

    def _helper():
        article_id = 42  # noqa: F841
        raise ValueError("test error for tracing")

    try:
        _helper()
    except ValueError as exc:
        result = tracing._format_traceback_with_locals(exc)

    assert "ValueError" in result
    assert "test error for tracing" in result
    assert "article_id" in result
    assert "42" in result


def test_format_traceback_with_locals_scrubs_sensitive():
    """A local named 'password' is scrubbed to '<scrubbed>'."""

    def _helper():
        password = "supersecret"  # noqa: F841
        raise RuntimeError("oops")

    try:
        _helper()
    except RuntimeError as exc:
        result = tracing._format_traceback_with_locals(exc)

    assert "password" in result
    assert "<scrubbed>" in result
    assert "supersecret" not in result


def test_format_traceback_with_locals_truncates_long_value():
    """A local whose repr exceeds _MAX_LOCAL_REPR chars is truncated."""

    def _helper():
        long_value = "x" * (tracing._MAX_LOCAL_REPR + 50)  # noqa: F841
        raise RuntimeError("long local")

    try:
        _helper()
    except RuntimeError as exc:
        result = tracing._format_traceback_with_locals(exc)

    assert "...<truncated>" in result
    # The full original value must not appear verbatim
    assert "x" * (tracing._MAX_LOCAL_REPR + 50) not in result


# ---------------------------------------------------------------------------
# OTelErrorLocalsMiddleware
# ---------------------------------------------------------------------------


def test_middleware_sets_attribute_on_recording_span(mocker):
    """process_exception sets 'exception.stacktrace_with_locals' on a recording span."""
    mock_span = mocker.MagicMock()
    mock_span.is_recording.return_value = True
    mocker.patch("opentelemetry.trace.get_current_span", return_value=mock_span)

    get_response = mocker.MagicMock()
    middleware = tracing.OTelErrorLocalsMiddleware(get_response)
    request = mocker.MagicMock()

    try:
        raise ValueError("middleware test")
    except ValueError as exc:
        exception = exc

    result = middleware.process_exception(request, exception)

    assert result is None
    mock_span.set_attribute.assert_called_once()
    call_args = mock_span.set_attribute.call_args
    assert call_args[0][0] == "exception.stacktrace_with_locals"
    assert isinstance(call_args[0][1], str)
    assert "ValueError" in call_args[0][1]


def test_middleware_no_attribute_when_not_recording(mocker):
    """process_exception does NOT call set_attribute when span is not recording."""
    mock_span = mocker.MagicMock()
    mock_span.is_recording.return_value = False
    mocker.patch("opentelemetry.trace.get_current_span", return_value=mock_span)

    get_response = mocker.MagicMock()
    middleware = tracing.OTelErrorLocalsMiddleware(get_response)
    request = mocker.MagicMock()

    try:
        raise RuntimeError("not recording")
    except RuntimeError as exc:
        exception = exc

    middleware.process_exception(request, exception)

    mock_span.set_attribute.assert_not_called()


def test_middleware_swallows_internal_errors(mocker):
    """process_exception returns None and does not raise if _format_traceback_with_locals fails."""
    mock_span = mocker.MagicMock()
    mock_span.is_recording.return_value = True
    mocker.patch("opentelemetry.trace.get_current_span", return_value=mock_span)
    mocker.patch.object(tracing, "_format_traceback_with_locals", side_effect=RuntimeError("formatter boom"))
    logger_spy = mocker.spy(tracing.logger, "warning")

    get_response = mocker.MagicMock()
    middleware = tracing.OTelErrorLocalsMiddleware(get_response)
    request = mocker.MagicMock()

    try:
        raise ValueError("original error")
    except ValueError as exc:
        exception = exc

    result = middleware.process_exception(request, exception)

    assert result is None  # Must not raise
    logger_spy.assert_called_once()


def test_middleware_call_passes_through(mocker):
    """__call__ returns get_response(request) unchanged."""
    sentinel = object()
    get_response = mocker.MagicMock(return_value=sentinel)
    middleware = tracing.OTelErrorLocalsMiddleware(get_response)
    request = mocker.MagicMock()

    response = middleware(request)

    get_response.assert_called_once_with(request)
    assert response is sentinel


def test_format_traceback_with_locals_survives_unreprable_local():
    class _BadRepr:
        def __repr__(self):
            return "%(x)s" % 5  # raises TypeError: format requires a mapping

    def _raises():
        bad = _BadRepr()  # noqa: F841
        benign = "benign-value-xyz"  # noqa: F841
        raise ValueError("boom")

    try:
        _raises()
    except ValueError as exc:
        out = tracing._format_traceback_with_locals(exc)  # must NOT raise

    assert "ValueError: boom" in out  # standard traceback still present
    assert "benign-value-xyz" in out  # other locals still rendered
    assert "bad" in out  # the bad local is still listed by name
    assert "unrepresentable" in out.lower()  # placeholder used instead of raising
    assert "format requires a mapping" not in out  # the inner repr error did not leak/propagate
