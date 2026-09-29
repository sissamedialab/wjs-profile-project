# OpenAPI Support via drf-spectacular Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Wire drf-spectacular into `wjs_review`'s existing DRF API so it exposes an accurate, access-gated OpenAPI schema, Swagger UI, and Redoc, converting the API's hand-built dict/error responses to real serializers wherever that's possible.

**Architecture:** `wjs_review/api/` is the only DRF surface in this project. Add `drf_spectacular` to `INSTALLED_APPS` and `REST_FRAMEWORK["DEFAULT_SCHEMA_CLASS"]`, add three gated views (schema JSON, Swagger UI, Redoc) reusing the API's existing access-control mixins, then work through the existing views one at a time: convert genuinely-JSON hand-built responses (galley list, collaborations envelope, the repeated error envelope) into real serializers so drf-spectacular can auto-infer them, and annotate the two binary-file responses (zip, galley download/upload) with `@extend_schema` since a serializer can't describe a byte stream.

**Tech Stack:** Django, Django REST Framework 3.18, drf-spectacular, pytest-django.

**Spec:** `docs/superpowers/specs/2026-09-21-openapi-drf-spectacular-design.md`

## Global Constraints

- Pin `drf-spectacular ~= 0.30` in `setup.cfg` (0.30.0 is the latest release as of 2026-09-21; verified via `pip index versions drf-spectacular`).
- Run tests via: `PYTHONPATH=/home/yakky/Projects/projects/sissa-1.8/janeway/src /home/yakky/.pyenv/versions/3.13.14/envs/janeway-5.2/bin/python -m pytest <args>` from the `wjs-profile-project` repo root (`/home/yakky/Projects/projects/sissa-1.8/wjs-profile-project`). This is the venv wjs-profile-project is installed into (editable, as `wjs.jcom_profile`) and the only way `pytest.ini`'s `DJANGO_SETTINGS_MODULE = wjs.defaults.tests` resolves `core`/`submission`/etc. from Janeway.
- Install the new dependency with: `/home/yakky/.pyenv/versions/3.13.14/envs/janeway-5.2/bin/pip install -e /home/yakky/Projects/projects/sissa-1.8/wjs-profile-project` (re-runs the editable install so `install_requires` picks up the new pin).
- **Verified full runtime path prefix is `/plugins/wjs-review-articles/api/v1/`, not `/api/v1/`** (confirmed via `reverse("collaborations")` → `/plugins/wjs-review-articles/api/v1/collaborations/`). Every schema-path assertion in this plan uses that full prefix — do not shorten it.
- Schema/docs endpoints (`schema`, `swagger-ui`, `redoc`) must use the same `LoggedRequestMixin, EOOrTypesetterAccessMixin` gating as the rest of the API (token auth + EO-or-typesetter permission), and must exclude themselves from the schema they serve via `@extend_schema_view(get=extend_schema(exclude=True))`.
- The `error` envelope (`{"error": {"code", "message", "details"?}}`) is represented once, as `ErrorDetailSerializer`/`ErrorSerializer` in `api/serializers.py`, and reused via `@extend_schema` everywhere it currently occurs as a hand-built dict.
- Any hand-built response body that gets rebuilt through a real serializer must stay byte-for-byte identical to what it produces today — verified with a regression test before/after the change, not just a schema-shape check.
- **Corrections from the design doc**, found while writing this plan:
  - `ArticleGalleyView.get`/`.post` never actually return HTTP 415 today — the `UNSUPPORTED_MEDIA_TYPE` error *code* is carried in a plain 400 body (DRF's default `ValidationError` handling always maps to 400). Document only the status codes the view actually returns: `{200, 400, 404, 409}` for GET, `{201, 400, 404, 409}` for POST. Do not add a 415 response.
  - `ArticleGalleyListView` has **no hand-rolled error envelope at all** — its only failure mode is the standard `get_object_or_404` 404 (DRF's default `{"detail": ...}` body). It needs zero `@extend_schema` annotations; converting it to `GenericAPIView` is enough for drf-spectacular to fully auto-infer its success response.
- `CollaborationListView`'s existing behavioral tests (`test_api_collaborations_*`) are gated behind `--run-collaborations-api` (`pytest.ini` doesn't set this by default) — always pass that flag when verifying Task 4, or those regression tests will silently be skipped.

---

### Task 1: Dependency, settings, and gated schema/docs endpoints

**Files:**
- Modify: `setup.cfg` (`install_requires`, after the `gunicorn` line, before the `# Constraints:` comment)
- Modify: `wjs/defaults/settings.py:11` (import line), and after line 46 (after the `wjs.user_search` try/except block, before `REDIS_CACHE_URL = ...`)
- Modify: `wjs/plugins/wjs_review/api/views.py` (imports + 3 new classes appended at the end of the file)
- Modify: `wjs/plugins/wjs_review/api/urls.py` (imports + 3 new paths)
- Test: `wjs/plugins/wjs_review/tests/test_api.py` (new tests appended at the end of the file)

**Interfaces:**
- Produces: URL names `"schema"`, `"swagger-ui"`, `"redoc"` (reversible with `reverse(...)`, no args); view classes `SchemaView`, `SwaggerUIView`, `RedocUIView` in `wjs/plugins/wjs_review/api/views.py`; `REST_FRAMEWORK["DEFAULT_SCHEMA_CLASS"]` and `SPECTACULAR_SETTINGS` in `wjs/defaults/settings.py`. All later tasks fetch the schema via `client.get(reverse("schema"), {"format": "json"}, HTTP_AUTHORIZATION=...)` and read `response.json()["paths"]` / `["components"]["schemas"]`.

- [ ] **Step 1: Write the failing tests**

Append to `wjs/plugins/wjs_review/tests/test_api.py`:

```python
@pytest.mark.django_db
def test_api_schema_is_protected(client: Client, eo_user: JCOMProfile, typesetter, normal_user):
    """Only EO members and typesetters with a valid token can fetch the OpenAPI schema."""
    url = reverse("schema")
    Token.objects.create(user=eo_user.janeway_account, key="EOTOKEN")
    Token.objects.create(user=typesetter.janeway_account, key="TYPESETTERTOKEN")
    Token.objects.create(user=normal_user.janeway_account, key="NORMALTOKEN")

    assert client.get(url).status_code == 401
    assert client.get(url, HTTP_AUTHORIZATION="Token WRONGTOKEN").status_code == 401
    assert client.get(url, HTTP_AUTHORIZATION="Token NORMALTOKEN").status_code == 403
    assert client.get(url, HTTP_AUTHORIZATION="Token EOTOKEN").status_code == 200
    assert client.get(url, HTTP_AUTHORIZATION="Token TYPESETTERTOKEN").status_code == 200


@pytest.mark.django_db
def test_api_schema_is_valid_openapi(client: Client, eo_user: JCOMProfile):
    """The schema entry point serves a parsable OpenAPI 3 document."""
    Token.objects.create(user=eo_user.janeway_account, key="EOTOKEN")

    response = client.get(reverse("schema"), {"format": "json"}, HTTP_AUTHORIZATION="Token EOTOKEN")

    assert response.status_code == 200
    schema = response.json()
    assert schema["openapi"].startswith("3.")
    assert "/plugins/wjs-review-articles/api/v1/collaborations/" in schema["paths"]


@pytest.mark.django_db
def test_api_docs_ui_is_protected(client: Client, eo_user: JCOMProfile, normal_user):
    """Swagger UI and Redoc are gated exactly like the API and the raw schema."""
    Token.objects.create(user=eo_user.janeway_account, key="EOTOKEN")
    Token.objects.create(user=normal_user.janeway_account, key="NORMALTOKEN")

    for view_name in ("swagger-ui", "redoc"):
        url = reverse(view_name)
        assert client.get(url).status_code == 401
        assert client.get(url, HTTP_AUTHORIZATION="Token NORMALTOKEN").status_code == 403
        assert client.get(url, HTTP_AUTHORIZATION="Token EOTOKEN").status_code == 200
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `PYTHONPATH=/home/yakky/Projects/projects/sissa-1.8/janeway/src /home/yakky/.pyenv/versions/3.13.14/envs/janeway-5.2/bin/python -m pytest wjs/plugins/wjs_review/tests/test_api.py -k "schema or docs_ui" -v`

Expected: FAIL with `django.urls.exceptions.NoReverseMatch: Reverse for 'schema' not found.` (or similar for `swagger-ui`/`redoc`).

- [ ] **Step 3: Add the dependency and install it**

In `setup.cfg`, in the `install_requires` list, add a line after `gunicorn` (and before the `# Constraints:` comment):

```
    gunicorn
    drf-spectacular ~= 0.30
    # Constraints:
```

Run: `/home/yakky/.pyenv/versions/3.13.14/envs/janeway-5.2/bin/pip install -e /home/yakky/Projects/projects/sissa-1.8/wjs-profile-project`

Expected: pip installs `drf-spectacular` (and its own dependency `inflection`/`uritemplate`/`PyYAML` etc.) with no errors.

- [ ] **Step 4: Wire settings**

In `wjs/defaults/settings.py`, replace line 11:

```python
from core.janeway_global_settings import STATIC_URL, TEMPLATES
```

with:

```python
from core.janeway_global_settings import REST_FRAMEWORK as _CORE_REST_FRAMEWORK
from core.janeway_global_settings import STATIC_URL, TEMPLATES
```

In the `INSTALLED_APPS` list (currently ending at line 28), add `"drf_spectacular"` after `"rest_framework.authtoken"`:

```python
INSTALLED_APPS = [
    "wjs.jcom_profile",
    "easy_select2",
    "rosetta",
    "django_fsm",
    "model_utils",
    "django_bootstrap5",
    "hijack.contrib.admin",
    "django_filters",
    "django_q",
    "wjs.themes",
    "wjs.advanced_admin",
    "rest_framework.authtoken",
    "drf_spectacular",
]
```

Then, right after the `wjs.user_search` `try`/`except ImportError: pass` block (i.e. right before the existing `REDIS_CACHE_URL = os.environ.get(...)` line), insert:

```python
REST_FRAMEWORK = {
    **_CORE_REST_FRAMEWORK,
    "DEFAULT_SCHEMA_CLASS": "drf_spectacular.openapi.AutoSchema",
}

SPECTACULAR_SETTINGS = {
    "TITLE": "WJS Review API",
    "DESCRIPTION": "Internal API for WJS Review production and collaboration data.",
    "VERSION": "1.0.0",
    "SERVE_INCLUDE_SCHEMA": False,
}

```

- [ ] **Step 5: Add the gated schema/docs views**

In `wjs/plugins/wjs_review/api/views.py`, add these imports (alongside the existing `rest_framework` imports near the top of the file):

```python
from drf_spectacular.utils import extend_schema, extend_schema_view
from drf_spectacular.views import (
    SpectacularAPIView,
    SpectacularRedocView,
    SpectacularSwaggerView,
)
```

Then append at the end of the file:

```python
@extend_schema_view(get=extend_schema(exclude=True))
class SchemaView(LoggedRequestMixin, EOOrTypesetterAccessMixin, SpectacularAPIView):
    """Serve the OpenAPI schema, gated like the rest of this API."""


@extend_schema_view(get=extend_schema(exclude=True))
class SwaggerUIView(LoggedRequestMixin, EOOrTypesetterAccessMixin, SpectacularSwaggerView):
    """Serve Swagger UI, gated like the rest of this API."""


@extend_schema_view(get=extend_schema(exclude=True))
class RedocUIView(LoggedRequestMixin, EOOrTypesetterAccessMixin, SpectacularRedocView):
    """Serve Redoc, gated like the rest of this API."""
```

- [ ] **Step 6: Wire the URLs**

In `wjs/plugins/wjs_review/api/urls.py`, change the import block to:

```python
from django.urls import path

from .views import (
    ArticleGalleyListView,
    ArticleGalleyView,
    ArticleZipDownloadView,
    CollaborationListView,
    JournalProductionListView,
    RedocUIView,
    SchemaView,
    SwaggerUIView,
)
```

and append these three paths at the end of `urlpatterns` (after the `typesetter-papers` entry, before the closing `]`):

```python
    path("schema/", SchemaView.as_view(), name="schema"),
    path("schema/swagger-ui/", SwaggerUIView.as_view(url_name="schema"), name="swagger-ui"),
    path("schema/redoc/", RedocUIView.as_view(url_name="schema"), name="redoc"),
```

- [ ] **Step 7: Run the tests to verify they pass**

Run: `PYTHONPATH=/home/yakky/Projects/projects/sissa-1.8/janeway/src /home/yakky/.pyenv/versions/3.13.14/envs/janeway-5.2/bin/python -m pytest wjs/plugins/wjs_review/tests/test_api.py -v`

Expected: PASS (all tests in the file, including the pre-existing ones — nothing about this task should break `test_api_articlegalleys_logged`).

- [ ] **Step 8: Commit**

```bash
git add setup.cfg wjs/defaults/settings.py wjs/plugins/wjs_review/api/views.py wjs/plugins/wjs_review/api/urls.py wjs/plugins/wjs_review/tests/test_api.py
git commit -m "feat(api): add drf-spectacular schema, Swagger UI, and Redoc"
```

---

### Task 2: Shared error serializer, annotate the binary-response views

**Files:**
- Modify: `wjs/plugins/wjs_review/api/serializers.py` (new serializers, added after the imports/`COLLABORATION_EXPORT_FIELDS` block, before `CollaborationSerializer`)
- Modify: `wjs/plugins/wjs_review/api/views.py` (`.serializers` import + `@extend_schema` on `ArticleZipDownloadView.get`, `ArticleGalleyView.get`, `ArticleGalleyView.post`)
- Test: `wjs/plugins/wjs_review/tests/test_api.py`

**Interfaces:**
- Consumes: `reverse("schema")` (Task 1).
- Produces: `ErrorDetailSerializer`, `ErrorSerializer` in `wjs/plugins/wjs_review/api/serializers.py` — reused by name (component `"Error"`) in every later task's error responses.

- [ ] **Step 1: Write the failing test**

Append to `wjs/plugins/wjs_review/tests/test_api.py`:

```python
@pytest.mark.django_db
def test_api_schema_documents_binary_responses(client: Client, eo_user: JCOMProfile):
    """The zip and galley download/upload entry points document their binary and error responses."""
    Token.objects.create(user=eo_user.janeway_account, key="EOTOKEN")

    response = client.get(reverse("schema"), {"format": "json"}, HTTP_AUTHORIZATION="Token EOTOKEN")
    schema = response.json()

    zip_get = schema["paths"]["/plugins/wjs-review-articles/api/v1/article/{pk}/zip/"]["get"]
    assert set(zip_get["responses"]) == {"200", "404"}

    galley_get = schema["paths"]["/plugins/wjs-review-articles/api/v1/article/{pk}/galley/{file_type}/"]["get"]
    assert set(galley_get["responses"]) == {"200", "400", "404", "409"}

    galley_post = schema["paths"]["/plugins/wjs-review-articles/api/v1/article/{pk}/galley/{file_type}/"]["post"]
    assert set(galley_post["responses"]) == {"201", "400", "404", "409"}

    error_component = schema["components"]["schemas"]["Error"]
    assert error_component["properties"]["error"]["$ref"].endswith("/ErrorDetail")
    detail_component = schema["components"]["schemas"]["ErrorDetail"]
    assert set(detail_component["properties"]) == {"code", "message", "details"}
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `PYTHONPATH=/home/yakky/Projects/projects/sissa-1.8/janeway/src /home/yakky/.pyenv/versions/3.13.14/envs/janeway-5.2/bin/python -m pytest wjs/plugins/wjs_review/tests/test_api.py::test_api_schema_documents_binary_responses -v`

Expected: FAIL — `zip_get["responses"]` is `{"200"}` only (no 404 documented), and `schema["components"]["schemas"]` has no `"Error"`/`"ErrorDetail"` key (`KeyError`).

- [ ] **Step 3: Add the error serializers**

In `wjs/plugins/wjs_review/api/serializers.py`, add after the imports and `COLLABORATION_EXPORT_FIELDS` block, before `class CollaborationSerializer`:

```python
class ErrorDetailSerializer(serializers.Serializer):
    """The `"error"` object of this API's error envelope."""

    code = serializers.CharField()
    message = serializers.CharField()
    details = serializers.JSONField(required=False)


class ErrorSerializer(serializers.Serializer):
    """This API's error envelope: `{"error": {"code", "message", "details"?}}`."""

    error = ErrorDetailSerializer()
```

- [ ] **Step 4: Annotate the binary-response views**

In `wjs/plugins/wjs_review/api/views.py`:

- Add `OpenApiTypes` and `OpenApiResponse` imports:

```python
from drf_spectacular.types import OpenApiTypes
from drf_spectacular.utils import OpenApiResponse, extend_schema, extend_schema_view
```

- Add `ErrorSerializer` to the existing `.serializers` import block:

```python
from .serializers import (
    CollaborationSerializer,
    ErrorSerializer,
    GalleyUploadSerializer,
    ProductionArticleSerializer,
)
```

- Decorate `ArticleZipDownloadView.get` (the method currently starting `def get(self, request, pk: int):` under `class ArticleZipDownloadView`):

```python
    @extend_schema(
        responses={
            200: OpenApiResponse(response=OpenApiTypes.BINARY, description="The requested zip file."),
            404: ErrorSerializer,
        },
    )
    def get(self, request, pk: int):
```

- Decorate `ArticleGalleyView.get`:

```python
    @extend_schema(
        responses={
            200: OpenApiResponse(response=OpenApiTypes.BINARY, description="The requested galley file."),
            400: ErrorSerializer,
            404: ErrorSerializer,
            409: ErrorSerializer,
        },
    )
    def get(self, request, pk: int, file_type: str, sequence: int = None):
```

- Decorate `ArticleGalleyView.post`:

```python
    @extend_schema(
        request=OpenApiTypes.BINARY,
        description=(
            "Upload or replace a galley file. The request body is the raw file content; "
            "its Content-Type must match the expected MIME type(s) for `file_type`."
        ),
        responses={
            201: None,
            400: ErrorSerializer,
            404: ErrorSerializer,
            409: ErrorSerializer,
        },
    )
    def post(self, request, pk: int, file_type: str, sequence: int = None):
```

- [ ] **Step 5: Run the test to verify it passes**

Run: `PYTHONPATH=/home/yakky/Projects/projects/sissa-1.8/janeway/src /home/yakky/.pyenv/versions/3.13.14/envs/janeway-5.2/bin/python -m pytest wjs/plugins/wjs_review/tests/test_api.py -v`

Expected: PASS (full file, including Task 1's tests and the pre-existing `test_api_articlegalleys_logged`).

- [ ] **Step 6: Commit**

```bash
git add wjs/plugins/wjs_review/api/serializers.py wjs/plugins/wjs_review/api/views.py wjs/plugins/wjs_review/tests/test_api.py
git commit -m "feat(api): document zip/galley binary responses and shared error envelope"
```

---

### Task 3: Convert `ArticleGalleyListView` to a real serializer

**Files:**
- Modify: `wjs/plugins/wjs_review/api/serializers.py` (new serializers, after `ErrorSerializer`, before `class CollaborationSerializer`)
- Modify: `wjs/plugins/wjs_review/api/views.py` (`ArticleGalleyListView` base class + body; imports)
- Test: `wjs/plugins/wjs_review/tests/test_api.py`

**Interfaces:**
- Consumes: `reverse("schema")` (Task 1).
- Produces: `GalleyItemSerializer`, `ArticleGalleyListSerializer` in `wjs/plugins/wjs_review/api/serializers.py`. `ArticleGalleyListView` is now a `GenericAPIView` with `serializer_class = ArticleGalleyListSerializer`.

- [ ] **Step 1: Write the regression test (characterizes current behavior)**

Append to `wjs/plugins/wjs_review/tests/test_api.py`. First add `File` and `Galley` to the existing imports at the top of the file — change:

```python
from submission.models import STAGE_PUBLISHED, Article
```

to:

```python
from core.models import File, Galley
from submission.models import STAGE_PUBLISHED, Article
```

Then append:

```python
@pytest.mark.django_db
def test_api_articlegalleys_response_shape(client: Client, article: Article, eo_user: JCOMProfile):
    """The article-galleys entry point serves the same JSON shape before and after the refactor."""
    article.date_published = timezone.now()
    article.stage = STAGE_PUBLISHED
    article.save()

    pdf_corefile = File.objects.create(
        mime_type="application/pdf",
        original_filename="of.pdf",
        uuid_filename="uf.pdf",
        is_galley=True,
    )
    Galley.objects.create(file=pdf_corefile, label="PDF", type="pdf", article=article, sequence=1)

    account = eo_user.janeway_account
    Token.objects.create(user=account, key="GOODTOKEN")

    url = reverse("article-galleys", args=(article.pk,))
    response = client.get(url, HTTP_AUTHORIZATION="Token GOODTOKEN")

    assert response.status_code == 200
    assert response.json() == {
        "article_id": article.pk,
        "items": [
            {
                "type": "pdf",
                "sequence": 1,
                "filename": "of.pdf",
                "contentType": "application/pdf",
                "download_url": f"/plugins/wjs-review-articles/api/v1/article/{article.pk}/galley/pdf/",
            }
        ],
    }


@pytest.mark.django_db
def test_api_schema_infers_article_galleys_response(client: Client, eo_user: JCOMProfile):
    """ArticleGalleyListView's response schema is inferred from its serializer, with no manual override."""
    Token.objects.create(user=eo_user.janeway_account, key="EOTOKEN")

    response = client.get(reverse("schema"), {"format": "json"}, HTTP_AUTHORIZATION="Token EOTOKEN")
    schema = response.json()

    component = schema["components"]["schemas"]["ArticleGalleyList"]
    assert set(component["properties"]) == {"article_id", "items"}
    item_ref = component["properties"]["items"]["items"]["$ref"]
    item_component = schema["components"]["schemas"][item_ref.rsplit("/", 1)[-1]]
    assert set(item_component["properties"]) == {"type", "sequence", "filename", "contentType", "download_url"}
```

- [ ] **Step 2: Run both tests**

Run: `PYTHONPATH=/home/yakky/Projects/projects/sissa-1.8/janeway/src /home/yakky/.pyenv/versions/3.13.14/envs/janeway-5.2/bin/python -m pytest wjs/plugins/wjs_review/tests/test_api.py::test_api_articlegalleys_response_shape wjs/plugins/wjs_review/tests/test_api.py::test_api_schema_infers_article_galleys_response -v`

Expected: `test_api_articlegalleys_response_shape` PASSES already (current dict-building code produces this exact shape — this step characterizes it). `test_api_schema_infers_article_galleys_response` FAILS with `KeyError: 'ArticleGalleyList'` (the serializer doesn't exist yet).

- [ ] **Step 3: Add the serializers**

In `wjs/plugins/wjs_review/api/serializers.py`, add after `ErrorSerializer`, before `class CollaborationSerializer`:

```python
class GalleyItemSerializer(serializers.Serializer):
    """One entry of `ArticleGalleyListSerializer.items`."""

    type = serializers.CharField()
    sequence = serializers.IntegerField()
    filename = serializers.CharField()
    contentType = serializers.CharField()
    download_url = serializers.CharField()


class ArticleGalleyListSerializer(serializers.Serializer):
    """Response of `ArticleGalleyListView`: an article's galleys, by type and sequence."""

    article_id = serializers.IntegerField()
    items = GalleyItemSerializer(many=True)
```

- [ ] **Step 4: Convert the view**

In `wjs/plugins/wjs_review/api/views.py`:

- Change the `rest_framework.generics` import to add `GenericAPIView`:

```python
from rest_framework.generics import GenericAPIView, ListAPIView, get_object_or_404
```

- Add `ArticleGalleyListSerializer` to the `.serializers` import block:

```python
from .serializers import (
    ArticleGalleyListSerializer,
    CollaborationSerializer,
    ErrorSerializer,
    GalleyUploadSerializer,
    ProductionArticleSerializer,
)
```

- Change the class declaration and the end of `get()`. Before:

```python
class ArticleGalleyListView(LoggedRequestMixin, PublishedArticleAccessMixin, APIView):
    def get(self, request, pk: int):
```

After:

```python
class ArticleGalleyListView(LoggedRequestMixin, PublishedArticleAccessMixin, GenericAPIView):
    serializer_class = ArticleGalleyListSerializer

    def get(self, request, pk: int):
```

Leave the body of `get()` unchanged down to the `items.append(...)` loop. Replace only the final line:

```python
        return Response({"article_id": article.pk, "items": items}, status=status.HTTP_200_OK)
```

with:

```python
        serializer = self.get_serializer(instance={"article_id": article.pk, "items": items})
        return Response(serializer.data, status=status.HTTP_200_OK)
```

- [ ] **Step 5: Run the tests to verify they pass**

Run: `PYTHONPATH=/home/yakky/Projects/projects/sissa-1.8/janeway/src /home/yakky/.pyenv/versions/3.13.14/envs/janeway-5.2/bin/python -m pytest wjs/plugins/wjs_review/tests/test_api.py -v`

Expected: PASS (full file — `test_api_articlegalleys_response_shape` still passes with the new serializer-backed implementation, `test_api_schema_infers_article_galleys_response` now passes too, and `test_api_articlegalleys_logged` from before is unaffected).

- [ ] **Step 6: Commit**

```bash
git add wjs/plugins/wjs_review/api/serializers.py wjs/plugins/wjs_review/api/views.py wjs/plugins/wjs_review/tests/test_api.py
git commit -m "refactor(api): serve article galleys via a real serializer instead of a hand-built dict"
```

---

### Task 4: `CollaborationsExportSerializer` for `CollaborationListView`

**Files:**
- Modify: `wjs/plugins/wjs_review/api/serializers.py` (new serializer, after `CollaborationSerializer`)
- Modify: `wjs/plugins/wjs_review/api/views.py` (`CollaborationListView.list()`; imports)
- Test: `wjs/plugins/wjs_review/tests/test_api.py`

**Interfaces:**
- Consumes: `ErrorSerializer` (Task 2), `reverse("schema")` (Task 1).
- Produces: `CollaborationsExportSerializer` in `wjs/plugins/wjs_review/api/serializers.py`.

- [ ] **Step 1: Write the failing test**

Append to `wjs/plugins/wjs_review/tests/test_api.py`:

```python
@pytest.mark.django_db
def test_api_schema_documents_collaborations_response(client: Client, eo_user: JCOMProfile):
    """The collaborations entry point documents its envelope and its 400 error response."""
    Token.objects.create(user=eo_user.janeway_account, key="EOTOKEN")

    response = client.get(reverse("schema"), {"format": "json"}, HTTP_AUTHORIZATION="Token EOTOKEN")
    schema = response.json()

    collaborations_get = schema["paths"]["/plugins/wjs-review-articles/api/v1/collaborations/"]["get"]
    assert set(collaborations_get["responses"]) == {"200", "400"}

    component = schema["components"]["schemas"]["CollaborationsExport"]
    assert set(component["properties"]) == {"version", "collaborations"}
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `PYTHONPATH=/home/yakky/Projects/projects/sissa-1.8/janeway/src /home/yakky/.pyenv/versions/3.13.14/envs/janeway-5.2/bin/python -m pytest wjs/plugins/wjs_review/tests/test_api.py::test_api_schema_documents_collaborations_response -v`

Expected: FAIL — `collaborations_get["responses"]` is `{"200"}` only, and `"CollaborationsExport"` is absent from `schema["components"]["schemas"]` (`KeyError`).

- [ ] **Step 3: Add the serializer**

In `wjs/plugins/wjs_review/api/serializers.py`, add after `class CollaborationSerializer` (its full block, including its `Meta`), before `class GalleyUploadSerializer`:

```python
class CollaborationsExportSerializer(serializers.Serializer):
    """Response of `CollaborationListView`: the versioned "tabellone.json"-like envelope."""

    version = serializers.FloatField()  # COLLABORATIONS_EXPORT_VERSION is a float (1.0)
    collaborations = CollaborationSerializer(many=True)
```

- [ ] **Step 4: Annotate and rebuild `CollaborationListView.list()`**

In `wjs/plugins/wjs_review/api/views.py`:

- Add `CollaborationsExportSerializer` to the `.serializers` import block:

```python
from .serializers import (
    ArticleGalleyListSerializer,
    CollaborationSerializer,
    CollaborationsExportSerializer,
    ErrorSerializer,
    GalleyUploadSerializer,
    ProductionArticleSerializer,
)
```

- Decorate `list()` and rebuild its final `payload`/`Response` construction. Before:

```python
    def list(self, request, *args, **kwargs):  # noqa: A003 (DRF's own hook name)
        """
        Serve the collaborations wrapped in the same envelope as "tabellone.json".

        :return: The versioned list of collaborations, or an error when "public_listing" is unknown.
        :rtype: Response
        """
        if self.get_public_listing() not in PUBLIC_LISTING_FILTERS:
            return Response(
                {
                    "error": {
                        "code": "BAD_REQUEST",
                        "message": "Invalid parameters.",
                        "details": {
                            "public_listing": {
                                "expected": sorted(PUBLIC_LISTING_FILTERS),
                                "got": request.query_params.get("public_listing"),
                            }
                        },
                    }
                },
                status=status.HTTP_400_BAD_REQUEST,
            )

        payload = {
            "version": COLLABORATIONS_EXPORT_VERSION,
            "collaborations": self.get_serializer(self.filter_queryset(self.get_queryset()), many=True).data,
        }
        return Response(payload, status=status.HTTP_200_OK)
```

After:

```python
    @extend_schema(responses={200: CollaborationsExportSerializer, 400: ErrorSerializer})
    def list(self, request, *args, **kwargs):  # noqa: A003 (DRF's own hook name)
        """
        Serve the collaborations wrapped in the same envelope as "tabellone.json".

        :return: The versioned list of collaborations, or an error when "public_listing" is unknown.
        :rtype: Response
        """
        if self.get_public_listing() not in PUBLIC_LISTING_FILTERS:
            return Response(
                {
                    "error": {
                        "code": "BAD_REQUEST",
                        "message": "Invalid parameters.",
                        "details": {
                            "public_listing": {
                                "expected": sorted(PUBLIC_LISTING_FILTERS),
                                "got": request.query_params.get("public_listing"),
                            }
                        },
                    }
                },
                status=status.HTTP_400_BAD_REQUEST,
            )

        export_serializer = CollaborationsExportSerializer(
            instance={
                "version": COLLABORATIONS_EXPORT_VERSION,
                "collaborations": self.filter_queryset(self.get_queryset()),
            },
            context=self.get_serializer_context(),
        )
        return Response(export_serializer.data, status=status.HTTP_200_OK)
```

- [ ] **Step 5: Run the tests to verify they pass**

Run: `PYTHONPATH=/home/yakky/Projects/projects/sissa-1.8/janeway/src /home/yakky/.pyenv/versions/3.13.14/envs/janeway-5.2/bin/python -m pytest wjs/plugins/wjs_review/tests/test_api.py --run-collaborations-api -v`

Expected: PASS — full file, **including** the pre-existing `test_api_collaborations_*` tests (only run with `--run-collaborations-api`), which verify the serializer-built payload is byte-for-byte identical to the old hand-built one (`test_api_collaborations_record_looks_like_tabellone`, `test_api_collaborations_are_ordered_by_short_name`, `test_api_collaborations_rejects_an_unknown_public_listing`, etc.).

- [ ] **Step 6: Commit**

```bash
git add wjs/plugins/wjs_review/api/serializers.py wjs/plugins/wjs_review/api/views.py wjs/plugins/wjs_review/tests/test_api.py
git commit -m "refactor(api): serve collaborations envelope via a real serializer, document its schema"
```

---

### Task 5: Full-schema regression guard

**Files:**
- Test: `wjs/plugins/wjs_review/tests/test_api.py`

**Interfaces:**
- Consumes: everything from Tasks 1–4 (the complete schema).
- Produces: nothing new for later tasks — this is the plan's final checkpoint.

- [ ] **Step 1: Write the test**

Append to `wjs/plugins/wjs_review/tests/test_api.py`:

```python
@pytest.mark.django_db
def test_api_schema_covers_all_entry_points(client: Client, eo_user: JCOMProfile):
    """The generated schema covers every real API entry point and excludes its own docs infrastructure."""
    Token.objects.create(user=eo_user.janeway_account, key="EOTOKEN")

    response = client.get(reverse("schema"), {"format": "json"}, HTTP_AUTHORIZATION="Token EOTOKEN")

    assert response.status_code == 200
    schema = response.json()
    assert schema["paths"].keys() == {
        "/plugins/wjs-review-articles/api/v1/collaborations/",
        "/plugins/wjs-review-articles/api/v1/article/{pk}/zip/",
        "/plugins/wjs-review-articles/api/v1/article/{pk}/galleys/",
        "/plugins/wjs-review-articles/api/v1/article/{pk}/galley/{file_type}/",
        "/plugins/wjs-review-articles/api/v1/article/{pk}/galley/{file_type}/{sequence}/",
        "/plugins/wjs-review-articles/api/v1/journal/{code}/production/",
    }
```

- [ ] **Step 2: Run the full test suite for this file**

Run: `PYTHONPATH=/home/yakky/Projects/projects/sissa-1.8/janeway/src /home/yakky/.pyenv/versions/3.13.14/envs/janeway-5.2/bin/python -m pytest wjs/plugins/wjs_review/tests/test_api.py --run-collaborations-api -v`

Expected: PASS. If this fails because `schema/`, `schema/swagger-ui/`, or `schema/redoc/` show up as extra keys, the `@extend_schema_view(get=extend_schema(exclude=True))` from Task 1, Step 5 wasn't applied to all three views — go back and check.

- [ ] **Step 3: Run the whole plugin's test suite as a final regression check**

Run: `PYTHONPATH=/home/yakky/Projects/projects/sissa-1.8/janeway/src /home/yakky/.pyenv/versions/3.13.14/envs/janeway-5.2/bin/python -m pytest wjs/plugins/wjs_review/ --run-collaborations-api -q`

Expected: PASS, no new failures introduced anywhere else in the plugin (e.g. nothing else imports `ArticleGalleyListView`/`CollaborationListView` in a way that assumed the old dict-building internals).

- [ ] **Step 4: Commit**

```bash
git add wjs/plugins/wjs_review/tests/test_api.py
git commit -m "test(api): add full-schema regression guard for the OpenAPI endpoint"
```

---

## Note: expect a documentation-wording follow-up

Once Tasks 1–5 are merged, expect a human to actually open Swagger UI/Redoc
(`swagger-ui`/`redoc` URL names) and review the rendered docs. That review
commonly turns up wording issues that don't need new tests or behavior
changes — vague `description`s, a `summary` that should be added to an
`@extend_schema` call, a `SPECTACULAR_SETTINGS["DESCRIPTION"]` that reads
wrong once seen rendered, an example value worth adding via
`OpenApiExample`. Treat those as small inline edits to the `@extend_schema`
calls and `SPECTACULAR_SETTINGS` added in this plan (mainly in
`wjs/plugins/wjs_review/api/views.py` and `wjs/defaults/settings.py`), not
as a reason to revisit the structural design (serializer choices, view base
classes, gating) — that part is already verified by the tests above. No new
task is defined for this since the exact wording requests aren't known yet;
handle them as a follow-up pass after human review of the rendered UI.
