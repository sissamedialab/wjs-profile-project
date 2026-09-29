# WJS Review API: OpenAPI documentation

## What it is

The `wjs_review` plugin exposes a small REST API (collaborations, article zip/galleys, journal production
list, typesetter papers). Its contract is described as an **OpenAPI 3 schema**, generated automatically from the
code by [drf-spectacular](https://drf-spectacular.readthedocs.io/): the views, serializers and `@extend_schema`
annotations are the single source of truth, so the docs cannot drift from the implementation.

The schema is a machine-readable file (JSON or YAML) listing every endpoint, its parameters, auth, response
shapes/media types and the shared `{"error": {...}}` envelope. It is consumed by:

- the browsable UIs (Swagger UI, Redoc);
- API clients (Postman, Bruno, Insomnia) that import it to build a ready-to-run request collection;
- code generators / contract tests, if ever needed.

Only the real API is described; the three docs views themselves are excluded from the schema.

## Endpoints

All paths are relative to the journal's host (e.g. `https://jcom.example.org`) and live under `/plugins/wjs-review-articles/api/v1/`.

| URL | What                                                |
|------------------|-----------------------------------------------------|
| `/plugins/wjs-review-articles/api/v1/schema/` | The raw schema. YAML by default, `?format=json` for JSON |
| `/plugins/wjs-review-articles/api/v1/schema/swagger-ui/` | Swagger UI: interactive, can execute requests ("Try it out") |
| `/plugins/wjs-review-articles/api/v1/schema/redoc/` | Redoc: read-only, nicer for reading/reference        |

**Access**: same gating as the rest of the API: Editorial Office members or typesetters only.
Docs views accept either an API token or a normal logged-in Janeway session, so an EO/typesetter user can just
log in and open Swagger UI/Redoc in the browser. The real API endpoints stay token-only.

## Authentication

The API uses DRF token authentication: send the header

```
Authorization: Token <key>
```

(the literal word `Token`, **not** `Bearer`). A token is created per Janeway account, for an EO or typesetter
user. Existing tokens can be looked up, and new ones created, in the Django admin (*Auth Token → Tokens*):

- list / retrieve: `/admin/authtoken/tokenproxy/` (search by username; the key is shown in the list)
- create: `/admin/authtoken/tokenproxy/add/` (pick the user; the key is generated on save)

You need a staff/superuser admin login. Or from a Django shell:

```python
from rest_framework.authtoken.models import Token
Token.objects.get_or_create(user=account)  # account: the Janeway Account
```

Treat tokens as passwords; don't commit them, and prefer a dedicated account per integration.

## Using the UIs

- **Swagger UI**: log in to the journal as EO/typesetter, open `/plugins/wjs-review-articles/api/v1/schema/swagger-ui/`. Requests made from the
  page reuse your session for the docs views; to call the *API* endpoints via "Try it out", click **Authorize**
  and enter `Token <key>` in the `Authorization` field (if shown), or paste the header manually.
- **Redoc**: open `/plugins/wjs-review-articles/api/v1/schema/redoc/`. Documentation only.

### Downloading the schema from the UI

The easiest way to get the schema file, no token needed (your login session is enough):

- **Swagger UI**: the link right under the API title (`/plugins/wjs-review-articles/api/v1/schema/`) opens the
  raw schema; save the page (or use the browser's *Save as*). Append `?format=json` to the URL for JSON instead
  of YAML.
- **Redoc**: use the **Download** button at the top of the left-hand menu.

The downloaded file is what Postman and Bruno import (see below).

## Importing into Postman

1. Get the schema: download it from the Swagger UI/Redoc link (see above), or from the command line
   (Postman can't send your token when importing from a URL):
   ```
   curl -H "Authorization: Token $TOKEN" \
        "https://<host>/plugins/wjs-review-articles/api/v1/schema/?format=json" -o wjs-review-api.json
   ```
2. **Import** → drop the file → choose *OpenAPI 3.0 with a Postman Collection*.
3. Set the base URL: on the collection, **Variables**, set `baseUrl` to `https://<host>` (the imported paths
   already include `/plugins/wjs-review-articles/api/v1/`, i.e. requests go to `<baseUrl>/plugins/wjs-review-articles/api/v1/...`; adjust if the import uses another variable name).
4. Authentication: collection → **Authorization** → type **API Key**, Key `Authorization`, Value
   `Token <key>`, Add to *Header*. (Don't use the *Bearer Token* type: it sends `Bearer`.) Better, store the key in
   an environment variable, e.g. Value `Token {{apiToken}}`, and keep the secret in the environment's *current
   value* only.
5. Requests inherit auth from the collection: pick one, fill path params (`{code}`, `{pk}`, ...), **Send**.
   To run everything: collection → **Run** (Collection Runner), select an environment and requests. Fill in
   real values for path parameters first (article pk, journal code...) since the imported requests have
   placeholders.

## Importing into Bruno

1. Download the schema as above, from the UI link or with `curl` (Bruno imports files).
2. **Collections → Import Collection → OpenAPI V3**, pick `wjs-review-api.json`, choose a location.
3. **Environments**: create e.g. `staging` with variables `baseUrl` = `https://<host>` and `apiToken` = the
   token (mark it *secret*). Select the environment.
4. Auth: collection settings → **Auth** → mode **API Key**, key `Authorization`, value `Token {{apiToken}}`,
   placement *Header* (or add a collection-level header with the same content). Requests set to *inherit*
   use it.
5. Open a request, fill path params, **Send** (`Ctrl+Enter`). Collection **Run** executes all requests in
   order. From the terminal (CI-friendly):
   ```
   bru run --env staging
   ```
   (needs `npm i -g @usebruno/cli`; run inside the collection folder).

## Importing into Insomnia

1. Download the schema as above, from the UI link or with `curl` (Insomnia imports the file).
2. **Create → Import** (or drag the file in), choose the file, and import it as an OpenAPI spec/collection.
3. **Environments**: edit the *Base Environment* (JSON) and set:
   ```json
   {"baseUrl": "https://<host>", "apiToken": "<key>"}
   ```
   Requests use `{{ _.baseUrl }}` if the import created that variable; otherwise set the collection's base
   URL to `https://<host>`. Keep the token in a private (non-synced) sub-environment.
4. Authentication: on the collection/folder, add a header `Authorization` = `Token {{ _.apiToken }}`
   (or **Auth → API Key**, key `Authorization`, value `Token {{ _.apiToken }}`, add to *Header*). Don't use
   the *Bearer* auth type: it sends `Bearer`.
5. Open a request, fill path params, **Send**. To run many requests, use the Collection Runner; from the terminal
   use the Inso CLI (`inso run collection <name>`).

## Regenerating / checking the schema

The schema is generated on request from the running code, so `/plugins/wjs-review-articles/api/v1/schema/` is
always up to date and is the best way to get the current OpenAPI schema. There is no committed file.

Generating a file from the command line is only needed to share the schema offline (e.g. with someone who has no
access to the journal). From the janeway checkout:

```
python manage.py spectacular --file wjs-review-api.yaml --validate
```

`test_api_schema_covers_all_entry_points` (in `wjs_review/tests/test_api.py`) fails when a new API endpoint is
added without being reflected in the schema: update the expected list there when adding endpoints, and document
new views with `@extend_schema` (use the shared `ErrorSerializer` for error responses).
