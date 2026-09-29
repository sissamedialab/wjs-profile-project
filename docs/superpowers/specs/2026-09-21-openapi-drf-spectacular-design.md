# OpenAPI support via drf-spectacular

Date: 2026-09-21
Status: design approved for details discussed; implementation not started

## Context

`wjs_review` is the only place in this repository with a DRF API
(`wjs/plugins/wjs_review/api/`), mounted at `api/v1/` under the plugin's
`urls.py`. No `REST_FRAMEWORK` settings dict is defined by this project today
(only Janeway core's, via `janeway_global_settings.py`: pagination only).
`rest_framework` itself is already in Janeway core's `INSTALLED_APPS`, and
`rest_framework.authtoken` is added by `wjs/defaults/settings.py`.

The existing API views:

- `CollaborationListView` (`ListAPIView`) — overrides `list()` to wrap
  `CollaborationSerializer` output in a `{"version", "collaborations"}`
  envelope, plus a hand-built `{"error": {...}}` 400 response for an invalid
  `public_listing` query param.
- `JournalProductionListView` (`ListAPIView`) — standard, `serializer_class`
  + `get_queryset()`, no overrides. Already fully auto-introspectable.
- `ArticleZipDownloadView` (`APIView`) — GET only, streams a zip file
  (`files.serve_file_to_browser`); hand-built `{"error": {...}}` 404s.
- `ArticleGalleyListView` (`APIView`) — GET only, hand-builds
  `{"article_id", "items": [...]}` from a queryset.
- `ArticleGalleyView` (`APIView`) — GET streams a galley file (content-type
  varies by `file_type`); POST uploads/replaces a galley from the raw
  `request.body` via `GalleyUploadSerializer` (which validates `request.body`
  directly, not `request.data`). Both hand-build `{"error": {...}}` responses
  for 400/404/409.

All of `ArticleZipDownloadView`, `ArticleGalleyListView`, and
`ArticleGalleyView` share `PublishedArticleAccessMixin` (article lookup +
object permission check). All API views use `EOOrTypesetterAccessMixin`
(token auth, EO-or-typesetter permission) except the two published-article
galley/zip views, which use `PublishedArticleAccessMixin` (which extends
`EOOrTypesetterAccessMixin`). `LoggedRequestMixin` logs every call.

## Decisions

- **Docs UI**: expose the raw schema plus Swagger UI and Redoc (drf-spectacular
  ships these views for free).
- **Access control**: the schema and docs UI are gated the same way as the
  rest of the API (token auth + EO-or-typesetter permission) — the API is
  internal-only, so its documentation should be too.
- **Annotation depth**: go beyond minimal wiring — convert what can genuinely
  become real serializers, and annotate what can't (binary responses) with
  `@extend_schema`, rather than leaving auto-inference thin for those views.

## Design

### Dependency

Add `drf-spectacular ~= 0.28` to `setup.cfg` `install_requires`, matching the
`~=` pin style already used there (e.g. `pandas ~= 2.0`, `django-filter ~=
25.1`).

### Settings (`wjs/defaults/settings.py`)

- Add `"drf_spectacular"` to `INSTALLED_APPS`, next to
  `"rest_framework.authtoken"`.
- Add a `REST_FRAMEWORK` dict that extends Janeway core's rather than
  replacing it (core sets `DEFAULT_PAGINATION_CLASS` /
  `PAGE_SIZE`, which `CollaborationListView` relies on being present by
  default):
  ```python
  from core.janeway_global_settings import REST_FRAMEWORK as _CORE_REST_FRAMEWORK
  # (same pattern already used for STATIC_URL/TEMPLATES imports in this file)

  REST_FRAMEWORK = {
      **_CORE_REST_FRAMEWORK,
      "DEFAULT_SCHEMA_CLASS": "drf_spectacular.openapi.AutoSchema",
  }
  ```
- Add `SPECTACULAR_SETTINGS`: `TITLE`, `DESCRIPTION`, `VERSION`,
  `SERVE_INCLUDE_SCHEMA=False`.

### Schema/docs views and URLs

New gated subclasses in `wjs/plugins/wjs_review/api/views.py`, mixing in the
same access-control mixins as the rest of the API:

```python
class SchemaView(LoggedRequestMixin, EOOrTypesetterAccessMixin, SpectacularAPIView):
    pass

class SwaggerUIView(LoggedRequestMixin, EOOrTypesetterAccessMixin, SpectacularSwaggerView):
    pass

class RedocUIView(LoggedRequestMixin, EOOrTypesetterAccessMixin, SpectacularRedocView):
    pass
```

Wired into the existing `wjs/plugins/wjs_review/api/urls.py` (no new
top-level URL config needed):

```python
path("schema/", SchemaView.as_view(), name="schema"),
path("schema/swagger-ui/", SwaggerUIView.as_view(url_name="schema"), name="swagger-ui"),
path("schema/redoc/", RedocUIView.as_view(url_name="schema"), name="redoc"),
```

### Serializers replacing hand-built dicts

New serializers in `wjs/plugins/wjs_review/api/serializers.py`:

```python
class ErrorDetailSerializer(serializers.Serializer):
    code = serializers.CharField()
    message = serializers.CharField()
    details = serializers.JSONField(required=False)

class ErrorSerializer(serializers.Serializer):
    error = ErrorDetailSerializer()

class GalleyItemSerializer(serializers.Serializer):
    type = serializers.CharField()
    sequence = serializers.IntegerField()
    filename = serializers.CharField()
    contentType = serializers.CharField()
    download_url = serializers.CharField()

class ArticleGalleyListSerializer(serializers.Serializer):
    article_id = serializers.IntegerField()
    items = GalleyItemSerializer(many=True)

class CollaborationsExportSerializer(serializers.Serializer):
    version = serializers.FloatField()  # COLLABORATIONS_EXPORT_VERSION is a float (1.0)
    collaborations = CollaborationSerializer(many=True)
```

`ErrorSerializer`/`ErrorDetailSerializer` are reused across every
`@extend_schema` error response below instead of duplicating the envelope
shape per call site.

### View changes

**`ArticleGalleyListView`: `APIView` → `GenericAPIView`.** Its response is a
single object (`{"article_id", "items"}`), not a list of resources, so
`ListAPIView` doesn't fit — but plain `GenericAPIView` does:

```python
class ArticleGalleyListView(LoggedRequestMixin, PublishedArticleAccessMixin, GenericAPIView):
    serializer_class = ArticleGalleyListSerializer

    def get(self, request, pk: int):
        ...  # same logic building `items`
        serializer = self.get_serializer(instance={"article_id": article.pk, "items": items})
        return Response(serializer.data, status=status.HTTP_200_OK)
```

Because drf-spectacular's `AutoSchema` reads `get_serializer_class()` on any
`GenericAPIView` and (absent `ListModelMixin`/pagination) treats a GET as
returning a single instance of that serializer, **this view's success
response is inferred automatically — no `@extend_schema` needed for it.**
Its 404 error response still needs `@extend_schema(responses={404:
ErrorSerializer})`.

**`ArticleZipDownloadView` and `ArticleGalleyView`: stay `APIView`.** Their
success responses are binary file streams (`files.serve_file_to_browser`),
not serialized resources — inheriting from a generic view would discard its
machinery for no benefit, since there's no serializer that can represent a
byte stream. Converting `PublishedArticleAccessMixin.get_article()` callers
to `RetrieveAPIView`'s `get_object()`/`queryset` was considered and rejected:
it's shared by all three galley/zip views for consistent lookup+permission
behavior, and the object-lookup 404 (`get_object_or_404` → standard `Http404`)
is already schema-agnostic regardless of base class, so converting only some
of them would fragment the pattern for no OpenAPI gain.

Document via `@extend_schema`:
- `ArticleZipDownloadView.get`: `responses={200: OpenApiTypes.BINARY, 404: ErrorSerializer}`.
- `ArticleGalleyView.get`: `responses={200: OpenApiTypes.BINARY, 400: ErrorSerializer, 404: ErrorSerializer, 409: ErrorSerializer}`.
- `ArticleGalleyView.post`: `request=OpenApiTypes.BINARY` (content-type varies
  by `file_type`, described in the operation description rather than modeled
  as a typed body — not worth a `oneOf` schema for v1), `responses={201: None,
  400: ErrorSerializer, 404: ErrorSerializer, 409: ErrorSerializer, 415:
  ErrorSerializer}`.

**`ArticleGalleyView.post` stays `APIView`, not `CreateAPIView`.**
`GalleyUploadSerializer` validates the raw `request.body` against a
content-type that varies by galley type, not `request.data` mapped to
serializer fields — `CreateAPIView.create()`'s contract doesn't apply here.

**`CollaborationListView`: stays `ListAPIView`, keeps its `list()` override.**
Its envelope wrapping and custom 400 path aren't auto-inferred by
`AutoSchema`, so it needs `@extend_schema(responses={200:
CollaborationsExportSerializer, 400: ErrorSerializer})`.

**`JournalProductionListView`: no changes.** Standard `ListAPIView` usage,
already fully auto-introspectable.

### Tests

Extend `wjs/plugins/wjs_review/tests/test_api.py` following its existing
conventions (`Token.objects.create`, `reverse()` by view name, `client.get`):
schema endpoint returns 200 for an authenticated EO/typesetter token, and
401/403 without one.

## Open items for implementation

- Confirm the exact `drf-spectacular` version to pin (`~= 0.28` is a
  placeholder based on current latest; verify against what's actually
  available/compatible at implementation time).
- Decide `SPECTACULAR_SETTINGS["TITLE"]`/`DESCRIPTION` wording.
