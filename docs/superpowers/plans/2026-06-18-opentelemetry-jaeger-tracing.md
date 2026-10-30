# OpenTelemetry + Jaeger Tracing Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add env-gated OpenTelemetry tracing (performance spans + error capture) to the WJS Django app running inside Janeway, exporting via OTLP/HTTP to a local Jaeger instance.

**Architecture:** All tracing logic lives in `wjs/jcom_profile/tracing.py` (`configure_tracing()`), which is a no-op unless `OTEL_TRACES_ENABLED` is truthy. `janeway/src/core/wsgi.py` calls it before `get_wsgi_application()` (~2-line upstream diff). `wjs/defaults/settings.py` exposes the env-driven knobs. A root `docker-compose.yml` runs one Jaeger all-in-one; all journals report under a single `OTEL_SERVICE_NAME` (default `wjs`).

**Tech Stack:** Python 3.11, Django 4.2, OpenTelemetry SDK + OTLP/HTTP exporter + instrumentation (django, psycopg2, requests, redis, logging), Jaeger all-in-one (Docker).

## Global Constraints

- **Code style:** black/isort/flake8, line length **119**, double quotes (flake8-quotes). Run `pre-commit run --all-files` before each commit. Do NOT use ruff. (See `.claude/rules/linting.md`.)
- **Branch:** all work on `feature/issue-2096-opentelemetry-jaeger-tracing` (already created). Never commit to `wjs-develop`. (See `.claude/rules/branching.md`.)
- **Tests run from `janeway/src`**, pointing at this repo by relative path; `pytest.ini` auto-resolves. (See `.claude/rules/tests.md`.) The repo must be `pip install -e .[test]` into Janeway's venv.
- **Tracing must never break the app:** all setup is wrapped so import/runtime/exporter errors are logged and swallowed; the request path is never affected.
- **Single shared service name** for all journals (not per-journal).
- `janeway/` is upstream — keep its diff minimal (logic stays in this repo).

---

## File Structure

| File | Responsibility |
|------|----------------|
| `setup.cfg` (modify) | Add OTel runtime dependencies to `install_requires`. |
| `wjs/jcom_profile/tracing.py` (create) | All tracing logic: defaults, env reading, `configure_tracing()`, guarded instrumentation. |
| `wjs/jcom_profile/tests/test_tracing.py` (create) | Unit tests (SDK mocked; no live collector needed). |
| `wjs/defaults/settings.py` (modify) | Env-driven config block exposing the OTel knobs as Django settings. |
| `janeway/src/core/wsgi.py` (modify) | Call `configure_tracing()` before `get_wsgi_application()`. |
| `docker-compose.yml` (create, repo root) | Single Jaeger all-in-one for local trace inspection. |

---

## Task 1: Add OpenTelemetry dependencies

**Files:**
- Modify: `setup.cfg` (the `install_requires` list under `[options]`)

**Interfaces:**
- Produces: the `opentelemetry.*` packages importable in the Janeway venv (consumed by all later tasks).

- [ ] **Step 1: Add the dependencies**

In `setup.cfg`, inside `[options] install_requires`, add these lines (place them after the existing `gunicorn` line, before the `# Constraints:` comment):

```
    opentelemetry-sdk
    opentelemetry-exporter-otlp-proto-http
    opentelemetry-instrumentation-django
    opentelemetry-instrumentation-psycopg2
    opentelemetry-instrumentation-requests
    opentelemetry-instrumentation-redis
    opentelemetry-instrumentation-logging
```

Note: do NOT pin versions. OpenTelemetry uses coupled api/sdk (1.x) and instrumentation (0.x) versioning; the instrumentation packages declare the compatible api/sdk ranges, so let pip resolve a matched set. (`opentelemetry-api` arrives transitively via `-sdk`.)

- [ ] **Step 2: Install and verify the imports resolve**

Run (from the Janeway venv, in this repo root):

```bash
pip install -e .[test]
python -c "import opentelemetry.sdk.trace, opentelemetry.exporter.otlp.proto.http.trace_exporter, opentelemetry.instrumentation.django, opentelemetry.instrumentation.psycopg2, opentelemetry.instrumentation.requests, opentelemetry.instrumentation.redis, opentelemetry.instrumentation.logging; print('ok')"
```

Expected: prints `ok` with no ImportError.

- [ ] **Step 3: Commit**

```bash
git add setup.cfg
git commit -m "build(issue-2096): add OpenTelemetry tracing dependencies"
```

---

## Task 2: `tracing.py` — defaults, env helpers, and disabled/idempotent paths

**Files:**
- Create: `wjs/jcom_profile/tracing.py`
- Test: `wjs/jcom_profile/tests/test_tracing.py`

**Interfaces:**
- Produces:
  - `DEFAULT_SERVICE_NAME: str = "wjs"`, `DEFAULT_OTLP_ENDPOINT: str = "http://localhost:4318"`, `DEFAULT_SAMPLER_RATIO: str = "1.0"`
  - `_env_truthy(value: Optional[str]) -> bool`
  - `tracing_enabled() -> bool`
  - `configure_tracing() -> None` (no-op when disabled or already configured; full behavior added in Task 3)
  - module global `_CONFIGURED: bool`
  - module function `_instrument() -> None` (stub here; filled in Task 3)

- [ ] **Step 1: Write the failing tests**

Create `wjs/jcom_profile/tests/test_tracing.py`:

```python
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


def test_tracing_enabled_reads_env(monkeypatch):
    monkeypatch.setenv("OTEL_TRACES_ENABLED", "true")
    assert tracing.tracing_enabled() is True
    monkeypatch.setenv("OTEL_TRACES_ENABLED", "false")
    assert tracing.tracing_enabled() is False
    monkeypatch.delenv("OTEL_TRACES_ENABLED", raising=False)
    assert tracing.tracing_enabled() is False


def test_configure_tracing_noop_when_disabled(monkeypatch, mocker):
    monkeypatch.delenv("OTEL_TRACES_ENABLED", raising=False)
    monkeypatch.setattr(tracing, "_CONFIGURED", False)
    instrument = mocker.patch.object(tracing, "_instrument")
    set_provider = mocker.patch("opentelemetry.trace.set_tracer_provider")

    tracing.configure_tracing()

    instrument.assert_not_called()
    set_provider.assert_not_called()
    assert tracing._CONFIGURED is False


def test_configure_tracing_idempotent(monkeypatch, mocker):
    monkeypatch.setenv("OTEL_TRACES_ENABLED", "true")
    monkeypatch.setattr(tracing, "_CONFIGURED", True)
    instrument = mocker.patch.object(tracing, "_instrument")
    set_provider = mocker.patch("opentelemetry.trace.set_tracer_provider")

    tracing.configure_tracing()

    instrument.assert_not_called()
    set_provider.assert_not_called()
```

- [ ] **Step 2: Run tests to verify they fail**

Run (from `janeway/src`):

```bash
pytest ../../wjs-profile-project/wjs/jcom_profile/tests/test_tracing.py -v
```

Expected: FAIL — `ModuleNotFoundError: No module named 'wjs.jcom_profile.tracing'` (or AttributeError for the helpers).

- [ ] **Step 3: Write the minimal implementation**

Create `wjs/jcom_profile/tracing.py`:

```python
"""OpenTelemetry tracing setup for the WJS app inside Janeway.

All logic is gated behind ``OTEL_TRACES_ENABLED`` and is a no-op when disabled.
Setup never raises into the request path: failures are logged and swallowed.
"""

import logging
import os
from typing import Optional

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


def tracing_enabled() -> bool:
    """Return True when tracing is enabled via the environment."""
    return _env_truthy(os.environ.get("OTEL_TRACES_ENABLED"))


def _instrument() -> None:
    """Apply auto-instrumentation. Filled in by Task 3."""


def configure_tracing() -> None:
    """Initialise OpenTelemetry tracing if enabled. No-op otherwise. Idempotent."""
    global _CONFIGURED
    if _CONFIGURED or not tracing_enabled():
        return
    # Full behavior added in Task 3.
```

- [ ] **Step 4: Run tests to verify they pass**

Run (from `janeway/src`):

```bash
pytest ../../wjs-profile-project/wjs/jcom_profile/tests/test_tracing.py -v
```

Expected: PASS (5 test cases incl. parametrized).

- [ ] **Step 5: Lint and commit**

```bash
pre-commit run --files wjs/jcom_profile/tracing.py wjs/jcom_profile/tests/test_tracing.py
git add wjs/jcom_profile/tracing.py wjs/jcom_profile/tests/test_tracing.py
git commit -m "feat(issue-2096): add tracing module skeleton (disabled/idempotent paths)"
```

---

## Task 3: `tracing.py` — provider, exporter, sampler, and instrumentation

**Files:**
- Modify: `wjs/jcom_profile/tracing.py`
- Test: `wjs/jcom_profile/tests/test_tracing.py`

**Interfaces:**
- Consumes: `configure_tracing`, `_instrument`, `_CONFIGURED`, defaults from Task 2.
- Produces: a configured global `TracerProvider` (when enabled) and applied instrumentors. Adds helper `_safe_instrument(name: str, fn: Callable[[], None]) -> None`.

- [ ] **Step 1: Write the failing tests**

Append to `wjs/jcom_profile/tests/test_tracing.py`:

```python
def test_configure_tracing_enabled_sets_provider(monkeypatch, mocker):
    monkeypatch.setenv("OTEL_TRACES_ENABLED", "true")
    monkeypatch.setenv("OTEL_SERVICE_NAME", "wjs")
    monkeypatch.setenv("OTEL_EXPORTER_OTLP_ENDPOINT", "http://jaeger:4318")
    monkeypatch.setenv("OTEL_TRACES_SAMPLER_RATIO", "1.0")
    monkeypatch.setattr(tracing, "_CONFIGURED", False)

    set_provider = mocker.patch("opentelemetry.trace.set_tracer_provider")
    exporter = mocker.patch(
        "opentelemetry.exporter.otlp.proto.http.trace_exporter.OTLPSpanExporter"
    )
    instrument = mocker.patch.object(tracing, "_instrument")

    tracing.configure_tracing()

    set_provider.assert_called_once()
    instrument.assert_called_once()
    assert tracing._CONFIGURED is True
    # endpoint is normalised to the /v1/traces path
    _, kwargs = exporter.call_args
    assert kwargs["endpoint"] == "http://jaeger:4318/v1/traces"


def test_configure_tracing_swallows_errors(monkeypatch, mocker):
    monkeypatch.setenv("OTEL_TRACES_ENABLED", "true")
    monkeypatch.setattr(tracing, "_CONFIGURED", False)
    mocker.patch(
        "opentelemetry.sdk.trace.TracerProvider", side_effect=RuntimeError("boom")
    )

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
```

- [ ] **Step 2: Run tests to verify they fail**

Run (from `janeway/src`):

```bash
pytest ../../wjs-profile-project/wjs/jcom_profile/tests/test_tracing.py -k "enabled_sets_provider or swallows or safe_instrument" -v
```

Expected: FAIL — `set_tracer_provider` not called / `_safe_instrument` AttributeError.

- [ ] **Step 3: Write the implementation**

Replace the `_instrument` stub and the body of `configure_tracing` in `wjs/jcom_profile/tracing.py` with:

```python
from typing import Callable  # add to the existing imports at top


def _safe_instrument(name: str, fn: Callable[[], None]) -> None:
    """Run one instrumentor, logging and swallowing any failure."""
    try:
        fn()
    except Exception:  # noqa: B902 - one bad instrumentor must not block the others
        logger.warning("Could not instrument %s for tracing", name, exc_info=True)


def _instrument() -> None:
    """Apply auto-instrumentation for the supported libraries (each guarded)."""

    def django():
        from opentelemetry.instrumentation.django import DjangoInstrumentor

        DjangoInstrumentor().instrument()

    def psycopg2():
        from opentelemetry.instrumentation.psycopg2 import Psycopg2Instrumentor

        Psycopg2Instrumentor().instrument(skip_dep_check=True)

    def requests():
        from opentelemetry.instrumentation.requests import RequestsInstrumentor

        RequestsInstrumentor().instrument()

    def redis():
        from opentelemetry.instrumentation.redis import RedisInstrumentor

        RedisInstrumentor().instrument()

    def logging_():
        from opentelemetry.instrumentation.logging import LoggingInstrumentor

        LoggingInstrumentor().instrument(set_logging_format=False)

    _safe_instrument("django", django)
    _safe_instrument("psycopg2", psycopg2)
    _safe_instrument("requests", requests)
    _safe_instrument("redis", redis)
    _safe_instrument("logging", logging_)


def configure_tracing() -> None:
    """Initialise OpenTelemetry tracing if enabled. No-op otherwise. Idempotent."""
    global _CONFIGURED
    if _CONFIGURED or not tracing_enabled():
        return
    try:
        from opentelemetry import trace
        from opentelemetry.exporter.otlp.proto.http.trace_exporter import OTLPSpanExporter
        from opentelemetry.sdk.resources import Resource
        from opentelemetry.sdk.trace import TracerProvider
        from opentelemetry.sdk.trace.export import BatchSpanProcessor
        from opentelemetry.sdk.trace.sampling import ParentBased, TraceIdRatioBased

        service_name = os.environ.get("OTEL_SERVICE_NAME", DEFAULT_SERVICE_NAME)
        endpoint = os.environ.get("OTEL_EXPORTER_OTLP_ENDPOINT", DEFAULT_OTLP_ENDPOINT)
        ratio = float(os.environ.get("OTEL_TRACES_SAMPLER_RATIO", DEFAULT_SAMPLER_RATIO))

        resource = Resource.create({"service.name": service_name})
        provider = TracerProvider(
            resource=resource,
            sampler=ParentBased(TraceIdRatioBased(ratio)),
        )
        exporter = OTLPSpanExporter(endpoint=f"{endpoint.rstrip('/')}/v1/traces")
        provider.add_span_processor(BatchSpanProcessor(exporter))
        trace.set_tracer_provider(provider)

        _instrument()
        _CONFIGURED = True
        logger.info(
            "OpenTelemetry tracing enabled (service=%s, endpoint=%s)", service_name, endpoint
        )
    except Exception:  # noqa: B902 - tracing must never break the app
        logger.exception("Failed to configure OpenTelemetry tracing; continuing without it")
```

- [ ] **Step 4: Run the full test file to verify it passes**

Run (from `janeway/src`):

```bash
pytest ../../wjs-profile-project/wjs/jcom_profile/tests/test_tracing.py -v
```

Expected: PASS (all cases).

- [ ] **Step 5: Lint and commit**

```bash
pre-commit run --files wjs/jcom_profile/tracing.py wjs/jcom_profile/tests/test_tracing.py
git add wjs/jcom_profile/tracing.py wjs/jcom_profile/tests/test_tracing.py
git commit -m "feat(issue-2096): configure tracer provider, OTLP exporter, instrumentation"
```

---

## Task 4: Settings config block

**Files:**
- Modify: `wjs/defaults/settings.py`
- Test: `wjs/jcom_profile/tests/test_tracing.py`

**Interfaces:**
- Consumes: defaults from `wjs.jcom_profile.tracing`.
- Produces: Django settings `OTEL_TRACES_ENABLED`, `OTEL_SERVICE_NAME`, `OTEL_EXPORTER_OTLP_ENDPOINT`, `OTEL_TRACES_SAMPLER_RATIO`.

- [ ] **Step 1: Write the failing test**

Append to `wjs/jcom_profile/tests/test_tracing.py`:

```python
def test_settings_expose_otel_defaults():
    from django.conf import settings

    assert settings.OTEL_SERVICE_NAME == tracing.DEFAULT_SERVICE_NAME
    assert settings.OTEL_EXPORTER_OTLP_ENDPOINT == tracing.DEFAULT_OTLP_ENDPOINT
    assert settings.OTEL_TRACES_SAMPLER_RATIO == tracing.DEFAULT_SAMPLER_RATIO
    # Disabled by default unless the environment opts in.
    assert tracing._env_truthy(settings.OTEL_TRACES_ENABLED) is False
```

- [ ] **Step 2: Run test to verify it fails**

Run (from `janeway/src`):

```bash
pytest ../../wjs-profile-project/wjs/jcom_profile/tests/test_tracing.py::test_settings_expose_otel_defaults -v
```

Expected: FAIL — `AttributeError: 'Settings' object has no attribute 'OTEL_SERVICE_NAME'`.

- [ ] **Step 3: Add the settings block**

Append to the end of `wjs/defaults/settings.py`:

```python
# OpenTelemetry tracing
# ---------------------
# Disabled by default; enable per-environment with OTEL_TRACES_ENABLED=true.
# All journals report under a single service name. Setup lives in
# wjs/jcom_profile/tracing.py (called from core/wsgi.py); Jaeger runs via the
# repo-root docker-compose.yml. Defaults are imported from tracing.py so the
# settings layer and the wsgi-time setup cannot drift.
from wjs.jcom_profile.tracing import (  # noqa: E402
    DEFAULT_OTLP_ENDPOINT,
    DEFAULT_SAMPLER_RATIO,
    DEFAULT_SERVICE_NAME,
)

OTEL_TRACES_ENABLED = os.environ.get("OTEL_TRACES_ENABLED", "false")
OTEL_SERVICE_NAME = os.environ.get("OTEL_SERVICE_NAME", DEFAULT_SERVICE_NAME)
OTEL_EXPORTER_OTLP_ENDPOINT = os.environ.get("OTEL_EXPORTER_OTLP_ENDPOINT", DEFAULT_OTLP_ENDPOINT)
OTEL_TRACES_SAMPLER_RATIO = os.environ.get("OTEL_TRACES_SAMPLER_RATIO", DEFAULT_SAMPLER_RATIO)
```

(`tracing.py` imports only stdlib at module level, so importing it during settings load is safe — no Django models, no circular import.)

- [ ] **Step 4: Run test to verify it passes**

Run (from `janeway/src`):

```bash
pytest ../../wjs-profile-project/wjs/jcom_profile/tests/test_tracing.py::test_settings_expose_otel_defaults -v
```

Expected: PASS.

- [ ] **Step 5: Lint and commit**

```bash
pre-commit run --files wjs/defaults/settings.py wjs/jcom_profile/tests/test_tracing.py
git add wjs/defaults/settings.py wjs/jcom_profile/tests/test_tracing.py
git commit -m "feat(issue-2096): expose OpenTelemetry settings (env-driven, disabled by default)"
```

---

## Task 5: Wire `configure_tracing()` into Janeway's wsgi entrypoint

**Files:**
- Modify: `janeway/src/core/wsgi.py` (upstream repo — minimal diff)

**Interfaces:**
- Consumes: `wjs.jcom_profile.tracing.configure_tracing`.
- Produces: tracing initialised at web-server boot when enabled.

> This change is in the upstream `janeway/` repo. Commit it on a matching branch there (`feature/issue-2096-opentelemetry-jaeger-tracing`). It is not exercised by this repo's pytest suite; verify by import and by the Task 6 smoke test.

- [ ] **Step 1: Create the branch in the janeway repo**

```bash
cd /home/yakky/Projects/projects/sissa-1.8/janeway
git switch -c feature/issue-2096-opentelemetry-jaeger-tracing
```

- [ ] **Step 2: Edit `janeway/src/core/wsgi.py`**

Add the import alongside the existing imports and the call between `load_janeway_settings()` and `get_wsgi_application()`. Result:

```python
import logging
import os
import sys
import urllib.parse

from utils import load_janeway_settings

from django.core.wsgi import get_wsgi_application

from wjs.jcom_profile.tracing import configure_tracing

os.environ.setdefault("JANEWAY_SETTINGS_MODULE", "core.settings")

load_janeway_settings()

# Initialise OpenTelemetry tracing before the app is built (no-op unless enabled).
configure_tracing()

application = get_wsgi_application()
```

- [ ] **Step 3: Verify the module imports cleanly (tracing disabled = no-op)**

Run (from `janeway/src`, with `OTEL_TRACES_ENABLED` unset):

```bash
python -c "import core.wsgi; print('wsgi import ok')"
```

Expected: prints `wsgi import ok`, no error, no tracing side effects.

- [ ] **Step 4: Commit (in the janeway repo)**

```bash
git add src/core/wsgi.py
git commit -m "feat(issue-2096): initialise WJS OpenTelemetry tracing in wsgi"
```

---

## Task 6: Jaeger docker-compose + manual smoke test

**Files:**
- Create: `docker-compose.yml` (repo root of `wjs-profile-project`)

**Interfaces:**
- Produces: a local Jaeger all-in-one reachable at UI `http://localhost:16686` and OTLP/HTTP `http://localhost:4318`.

- [ ] **Step 1: Create `docker-compose.yml`**

```yaml
# Local Jaeger for inspecting WJS traces.
#
# Usage:
#   docker compose up -d
#   # run Janeway with tracing on:
#   OTEL_TRACES_ENABLED=true OTEL_EXPORTER_OTLP_ENDPOINT=http://localhost:4318 \
#     python manage.py runserver
#   # open the UI and filter by service "wjs":
#   xdg-open http://localhost:16686
#
# Single instance: all journals report under one service name (OTEL_SERVICE_NAME, default "wjs").
services:
  jaeger:
    image: jaegertracing/all-in-one:1.62.0
    environment:
      - COLLECTOR_OTLP_ENABLED=true
    ports:
      - '16686:16686'   # Jaeger UI
      - '4318:4318'     # OTLP/HTTP (used by the app)
      - '4317:4317'     # OTLP/gRPC (available, unused by default)
```

- [ ] **Step 2: Start Jaeger and confirm the UI and OTLP port are up**

```bash
docker compose up -d
curl -sf -o /dev/null http://localhost:16686 && echo "UI up"
curl -sf -o /dev/null http://localhost:4318 || echo "4318 reachable (404/405 on GET is fine)"
```

Expected: `UI up` printed; port 4318 accepts connections (a GET returning 404/405 is expected — it only serves POST `/v1/traces`).

- [ ] **Step 3: Smoke-test a real trace end to end**

From `janeway/src`, with Jaeger running:

```bash
OTEL_TRACES_ENABLED=true OTEL_EXPORTER_OTLP_ENDPOINT=http://localhost:4318 \
  python manage.py runserver 0.0.0.0:8000
```

In another shell: `curl -s -o /dev/null http://localhost:8000/` (load any page), then open `http://localhost:16686`, select service **wjs**, and confirm a trace with HTTP + DB spans appears. Trigger a server error (request a URL that 500s) and confirm the span is flagged with `error=true` / records the exception — this is the "error tracing".

- [ ] **Step 4: Commit**

```bash
git add docker-compose.yml
git commit -m "feat(issue-2096): add Jaeger all-in-one docker-compose for local tracing"
```

---

## Task 7: Full suite regression + docs

**Files:**
- (No new files; optional doc note.)

- [ ] **Step 1: Run the relevant test module reversed/parallel (catch ordering/global-state issues)**

The tracing tests touch process-global env and the OTel provider; confirm they're isolated.

Run (from `janeway/src`):

```bash
pytest ../../wjs-profile-project/wjs/jcom_profile/tests/test_tracing.py -v -n2 --reverse
```

Expected: PASS.

- [ ] **Step 2: Run a broader slice to confirm no import-time regression from the settings change**

Run (from `janeway/src`):

```bash
pytest ../../wjs-profile-project/wjs/jcom_profile/tests/ -q -m "not fix_labels"
```

Expected: PASS (no collection/import errors from the new `settings.py` block).

- [ ] **Step 3: Final lint**

```bash
cd /home/yakky/Projects/projects/sissa-1.8/wjs-profile-project
pre-commit run --all-files
```

Expected: all hooks pass (incl. the new `Flake8-pyproject`-backed flake8).

---

## Self-Review Notes (author check — for reference, not a task)

- **Spec coverage:** settings config → Task 4; wsgi integration → Task 5; docker-compose single Jaeger instance → Task 6; off-by-default env gating → Tasks 2 & 4; OTLP/HTTP → Tasks 1 & 3; full-stack instrumentation → Task 3; single service name → Tasks 3, 4, 6; fail-soft → Task 3 tests; deps in `install_requires` → Task 1; out-of-scope (workers/ASGI, prod deploy) → unchanged by design.
- **Known limitation (documented in spec):** DB spans rely on `Psycopg2Instrumentor` (Postgres, used in CI and Janeway). A MariaDB deployment would need a different DB instrumentor; the `_safe_instrument` guard makes the psycopg2 step skip cleanly rather than error there.
- **Type/name consistency:** `configure_tracing`, `_instrument`, `_safe_instrument`, `_CONFIGURED`, `_env_truthy`, `tracing_enabled`, and the three `DEFAULT_*` constants are used identically across tasks and tests.

---

## Amendment: performance/error tracing split

**Date:** 2026-06-18 (post-initial implementation)

The single `OTEL_TRACES_ENABLED` flag has been replaced by two independent flags:

| Flag | Default | Instrumentors installed |
|------|---------|------------------------|
| `OTEL_PERFORMANCE_TRACING_ENABLED` | `false` | Django, psycopg2, requests, redis, logging |
| `OTEL_ERROR_TRACING_ENABLED` | `false` | Django, logging only |

`tracing_enabled()` is now the OR aggregate of both predicates and continues to serve as
the early-return guard in `configure_tracing()`.

`_instrument(performance_enabled, error_enabled)` accepts two booleans. Django and
logging are installed when either flag is truthy (Django surfaces exceptions as error
spans; logging adds trace-id correlation useful in both modes). psycopg2, requests, and
redis are performance-detail spans installed only when `performance_enabled` is True.

`settings.py` exposes `OTEL_PERFORMANCE_TRACING_ENABLED` and `OTEL_ERROR_TRACING_ENABLED`
(both defaulting to `"false"`); `OTEL_TRACES_ENABLED` is removed.

`docker-compose.yml` usage comment updated to `OTEL_PERFORMANCE_TRACING_ENABLED=true
OTEL_ERROR_TRACING_ENABLED=true`.

Tests (25 total): predicate tests for all three functions; per-mode instrumentor gating
assertions calling `_instrument` directly with mock patches on all five instrumentor
classes; higher-level `configure_tracing` tests updated to the new flags.

## Amendment: local variable capture in error spans

**Date:** 2026-06-18 (second amendment)

Added `OTEL_ERROR_TRACING_CAPTURE_LOCALS` (default `true`). When both this flag and
`OTEL_ERROR_TRACING_ENABLED` are truthy, `OTelErrorLocalsMiddleware` is appended to
`MIDDLEWARE` at settings-load time and fires on view exceptions to attach a scrubbed,
length-capped stack trace (with locals) as the span attribute
`exception.stacktrace_with_locals`.

New symbols in `tracing.py`: `_SENSITIVE_LOCAL_PATTERNS`, `_MAX_LOCAL_REPR`,
`capture_locals_enabled()`, `_is_sensitive_local()`, `_format_traceback_with_locals()`,
`OTelErrorLocalsMiddleware`.

Tests: 36 new tests (61 total). All 61 pass. Pre-commit clean.

End-to-end confirmation (attribute visible in Jaeger on a real 500) requires manual
verification — see the spec's "Local variable capture" section.

## Amendment: move init from janeway wsgi.py to wjs/channels/asgi.py

The `configure_tracing()` call was moved out of the upstream `janeway/src/core/wsgi.py`
(Task 5, now reverted) into our own ASGI/channels entrypoint `wjs/channels/asgi.py`, called
after `load_janeway_settings()` and before `get_asgi_application()`. The app is served via
ASGI (channels/uvicorn), and keeping the call in our repo means **no upstream Janeway change**.
Verified: the ASGI module imports as a no-op when disabled, and `configure_tracing()` runs
before `get_asgi_application()`.

## Amendment: rename compose file + upgrade to Jaeger v2

`docker-compose.yml` was renamed to `docker-compose-jaeger.yml` (run with
`docker compose -f docker-compose-jaeger.yml up -d`). The image was upgraded from the
maintenance-line `jaegertracing/all-in-one:1.62.0` to **Jaeger v2** `jaegertracing/jaeger:2.19.0`
(verified latest on Docker Hub). v2 is OTLP-native, so `COLLECTOR_OTLP_ENABLED` was dropped.
Verified end-to-end: v2 pulls/runs, UI on 16686, and a span emitted via our tracing over
OTLP/HTTP :4318 appears under service `wjs` in `/api/services`.
