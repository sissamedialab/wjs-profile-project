# Design: Error & performance tracing with OpenTelemetry + Jaeger

- **Issue:** #2096
- **Date:** 2026-06-18
- **Branch:** `feature/issue-2096-opentelemetry-jaeger-tracing`

## Goal

Add distributed tracing (performance spans + error/exception capture) to the WJS
Django application that runs inside Janeway, exporting via OpenTelemetry (OTLP) to a
local Jaeger instance for inspection.

## Decisions (confirmed)

- **Enablement:** off by default, enabled via two independent environment variables
  (`OTEL_PERFORMANCE_TRACING_ENABLED` and `OTEL_ERROR_TRACING_ENABLED`). CI, local dev,
  and existing deployments are unaffected unless they opt in.
- **Transport:** OTLP/HTTP on port `4318` (`opentelemetry-exporter-otlp-proto-http`);
  no `grpcio` dependency.
- **Instrumentation scope:** full stack — Django, DB (dbapi/psycopg2), outbound
  `requests`, redis, and logging correlation.
- **Single instance:** one Jaeger all-in-one container; **all journals report under a
  single shared `OTEL_SERVICE_NAME`** (default `wjs`) so every request and error lands
  in one place. Not per-journal.

## Architecture

### Components

1. **`wjs/jcom_profile/tracing.py`** (new) — owns all tracing logic in *our* repo.
   Exposes a single entry point:

   ```python
   def configure_tracing() -> None:
       """Initialise OpenTelemetry tracing if enabled via env. No-op otherwise. Idempotent."""
   ```

   Responsibilities:
   - Return immediately unless at least one of `OTEL_PERFORMANCE_TRACING_ENABLED` or
     `OTEL_ERROR_TRACING_ENABLED` is truthy.
   - Guard against double-initialisation (gunicorn preload, autoreload, repeated imports).
   - Build a `TracerProvider` with a `Resource` carrying `service.name = OTEL_SERVICE_NAME`.
   - Attach a `BatchSpanProcessor` with an `OTLPSpanExporter` (HTTP) pointed at
     `OTEL_EXPORTER_OTLP_ENDPOINT`.
   - Apply a parent-based ratio sampler from `OTEL_TRACES_SAMPLER_RATIO`.
   - Instrument: `DjangoInstrumentor`, DB instrumentor, `RequestsInstrumentor`,
     `RedisInstrumentor`, `LoggingInstrumentor`.

2. **`wjs/channels/asgi.py`** (our repo — the ASGI/channels entrypoint) — calls
   `configure_tracing()` after `load_janeway_settings()` and *before* `get_asgi_application()`.
   Keeping the call in our own ASGI module means **no upstream Janeway change is needed**; it is
   a safe no-op when disabled. (The plain-WSGI entrypoint is out of scope; django-q2 workers are
   covered separately, see *django-q2 tasks* below.)

3. **`wjs/defaults/settings.py`** — a small, documented config block exposing the OTel
   knobs as module-level constants read from the environment (consistent with the existing
   `os.environ.get` pattern used for Redis URLs), so the configuration is visible/overridable
   at the Django-settings layer and available to non-wsgi entrypoints later.

   > **Ordering note:** `asgi.py` calls `configure_tracing()` *before* Django loads the
   > settings module, so `configure_tracing()` must **not** depend on this block having run.
   > `configure_tracing()` reads `os.environ` directly with its own fallback defaults; the
   > settings block mirrors the same env contract. The default values are defined once (as
   > constants in `tracing.py`) and referenced by `settings.py` to avoid drift. A deployment's
   > own env vars always take precedence in both paths.

4. **`docker-compose-jaeger.yml`** (new, in `wjs-profile-project` root) — a single
   `jaegertracing/jaeger:2.19.0` (Jaeger v2) service, exposing UI `16686` and OTLP/HTTP
   `4318` (and `4317` for completeness). Jaeger v2 is OTLP-native, so the OTLP receivers are
   enabled by default (no `COLLECTOR_OTLP_ENABLED` needed). Local/dev tooling only; run with
   `docker compose -f docker-compose-jaeger.yml up -d`.

5. **`setup.cfg`** — new dependencies under `install_requires` (or a dedicated extra; see
   Open question).

### Data flow

```
Django request
  → DjangoInstrumentor creates a server span (records exceptions as error span events)
  → nested spans from DB / requests / redis instrumentors
  → BatchSpanProcessor batches spans
  → OTLPSpanExporter (HTTP) → Jaeger all-in-one :4318
  → Jaeger UI :16686 (filter by service "wjs", error tag for failures)
```

### Configuration contract (env vars)

| Variable | Default | Purpose |
|----------|---------|---------|
| `OTEL_PERFORMANCE_TRACING_ENABLED` | `false` | Enable performance tracing (Django + DB + requests + redis + logging). |
| `OTEL_ERROR_TRACING_ENABLED` | `false` | Enable error tracing (Django + logging only; no performance-detail spans). |
| `OTEL_SERVICE_NAME` | `wjs` | Single shared service name for all journals. |
| `OTEL_EXPORTER_OTLP_ENDPOINT` | `http://localhost:4318` | OTLP/HTTP collector endpoint (Jaeger). |
| `OTEL_TRACES_SAMPLER_RATIO` | `1.0` | Parent-based ratio sampler (1.0 = trace everything; lower in prod). |
| `OTEL_ERROR_TRACING_CAPTURE_LOCALS` | `true` | Captures scrubbed, length-capped (200 chars) local variables into a separate `exception.stacktrace_with_locals` span attribute on errors. **Warning:** despite scrubbing, non-secret PII (e.g. user names in local vars) may still be recorded; review before enabling in production. |

> **Instrumentor split:** Django and logging instrumentors are installed when _either_ flag
> is truthy (Django surfaces exceptions as error spans; logging adds trace-id correlation
> useful in both modes). psycopg2, requests, and redis are performance-detail spans
> installed only when `OTEL_PERFORMANCE_TRACING_ENABLED` is truthy.

## Error handling

- **Tracing must never break the app.** `configure_tracing()` wraps setup so that an
  import error, missing dependency, or unreachable collector is logged and swallowed —
  the request path is never affected. The OTLP exporter already fails soft (spans are
  dropped on export error, app continues).
- Disabled path is a pure early return; zero overhead and zero new failure modes.

### Local variable capture

When `OTEL_ERROR_TRACING_CAPTURE_LOCALS=true` (default) and `OTEL_ERROR_TRACING_ENABLED=true`,
the `OTelErrorLocalsMiddleware` fires on view exceptions and calls
`_format_traceback_with_locals(exc)`. This uses `traceback.TracebackException.from_exception(...,
capture_locals=True)` to collect each frame's local variables as repr-strings, then:

- **Scrubs** any variable whose name contains a known secret/PII pattern (password, passwd,
  pwd, secret, token, authorization, api_key, apikey, access_key, private_key, session,
  cookie, csrf) — the value is replaced with `"<scrubbed>"`.
- **Truncates** any value longer than 200 characters, appending `"...<truncated>"`.

The result is stored as the span attribute `exception.stacktrace_with_locals` (separate from
the standard `exception.stacktrace`). If no span is active/recording at exception time
(e.g. tracing was not configured, the span already ended, or OTel is unavailable), the
middleware is a safe no-op — it catches all exceptions internally and logs a warning without
re-raising.

**Manual verification:** to confirm the attribute appears in Jaeger, run the app with both
`OTEL_ERROR_TRACING_ENABLED=true` and `OTEL_ERROR_TRACING_CAPTURE_LOCALS=true`, trigger a
500 error, and look for `exception.stacktrace_with_locals` in the span's tags in the Jaeger
UI. This end-to-end step is not covered by automated tests.

## Testing

- **Unit:** `configure_tracing()` is a no-op when both `OTEL_PERFORMANCE_TRACING_ENABLED`
  and `OTEL_ERROR_TRACING_ENABLED` are unset/false (no provider set, instrumentors not
  called); idempotent when called twice; sets the expected service name / endpoint when
  enabled. Per-mode instrumentor gating is asserted directly on `_instrument(perf, err)`.
  Patch the SDK/instrumentors so tests don't need a live collector.
- **Manual smoke test:** `docker compose up -d`, run Janeway with
  `OTEL_PERFORMANCE_TRACING_ENABLED=true OTEL_ERROR_TRACING_ENABLED=true`, hit a page,
  confirm the trace appears in the Jaeger UI under service `wjs`; trigger a 500 and
  confirm the span is flagged as an error.
- Tests follow the repo convention: run from `janeway/src` (see `.claude/rules/tests.md`).

## django-q2 tasks

Tasks run in the qcluster's worker processes (`manage.py qcluster`), which never load
`wjs/channels/asgi.py`. `wjs/jcom_profile/tracing_djangoq.py`, connected from
`JCOMProfileConfig.ready()`, hooks into django-q2's signals:

- `pre_enqueue` (in the enqueuing process, e.g. a web request) — injects the current trace
  context (W3C `traceparent`) into the task dict under `otel_context`, so the task's span joins
  the request's trace;
- `post_spawn` (in each new worker process) — calls `configure_tracing()` there, so each worker
  has its own provider and exporter thread (no fork-safety issue: the qcluster parent never
  configures tracing), and replaces `django_q.worker.TimeoutHandler` with
  `TracedTimeoutHandler`;
- `pre_execute` (in the worker) — remembers the task about to run.

django-q2 sends no signal in the worker when a task ends (`post_execute` is sent by the monitor
process), so the span (`task <func>`, kind CONSUMER, with `django_q.task.*` attributes, error
status on failure or timeout) is opened and closed by `TracedTimeoutHandler`, the context manager
that surrounds exactly the call to the task's function. The spans are flushed after each task:
multiprocessing ends its children with `os._exit()`, which skips the provider's atexit shutdown.

The qcluster unit (`setup-docs/ansible/systemd/qcluster.service.j2`) sets the same `OTEL_*`
variables as the gunicorn one, with service name `wjs<suffix>-qcluster`. Scheduled tasks and
tasks enqueued outside a traced request start their own trace.

## Scope boundaries (out of scope)

- **`BatchSpanProcessor` fork-safety** — `BatchSpanProcessor` is not fork-safe; a future
  gunicorn `--preload` deployment will need to re-initialise the tracer provider in a
  post-fork/worker-init hook, otherwise forked workers inherit a dead exporter thread and
  silently drop spans.
- **Plain-WSGI entrypoint** — tracing is initialised from the ASGI/channels entrypoint
  (`wjs/channels/asgi.py`), which is how the app is served. The same `configure_tracing()` call
  can be added to the WSGI entrypoint later; noted for future work.
- **Production Jaeger deployment** — the `docker-compose-jaeger.yml` is local/dev tooling.
- **Metrics and logs pipelines** — traces only (OTel metrics/logs not configured).
- **ruff** — not applicable; `wjs-*` repos use black/isort/flake8 (see
  `.claude/rules/linting.md`).

## Dependency placement (decided)

OTel packages go in the main `install_requires` in `setup.cfg`. Rationale: imports are
gated and cheap when disabled, and a single install path avoids "tracing silently
unavailable because the extra wasn't installed" in production. (Easily switched to a
`tracing` extra later if base-install size becomes a concern.)
