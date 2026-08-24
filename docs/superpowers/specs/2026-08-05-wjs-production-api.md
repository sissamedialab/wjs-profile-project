# WJS Review Production API — Specification

**Status:** Finalized design, ready for implementation planning.
**Scope:** `wjs.plugins.wjs_review` — extends the existing DRF `api` package (currently 3 views / 4 routes, all published-article-only) to cover the production/typesetting phase: file exchange with the typesetter, paper status, messages, and journal-wide production monitoring.

## 1. Goal

Expose, as a token-authenticated JSON API, the set of production-phase operations requested (see §2), reusing existing `wjs_review` logic classes wherever they already implement the behaviour, and extracting shared logic into `logic.py`/`logic__production.py` dataclasses where the same behaviour is currently duplicated (or would become duplicated) between server-rendered views and the new API.

**Non-goals:** no change to the existing 4 published-article galley routes' behaviour; no new UI; no change to FSM transitions themselves (only to which callers can trigger them).

## 2. Requested endpoints vs. today's code

Baseline: `api/urls.py` today defines exactly these 4 routes (Token auth, `IsEOOrTypesetterForArticle`, published articles only):

```
GET      /article/<pk>/zip/                          → ArticleZipDownloadView
GET      /article/<pk>/galleys/                      → ArticleGalleyListView
GET/POST /article/<pk>/galley/<file_type>/[<seq>/]   → ArticleGalleyView
```

| # | Requested endpoint | Verdict | Notes |
|---|---|---|---|
| — | `GET/PUT /:paperID/{zip,tex,pdf,epub,html,xml}` (single galley download/replace) | **Already fully implemented** | `ArticleGalleyView` GET/POST + `ArticleZipDownloadView` GET. `xml` is already a valid `file_type` (`api/const.py:5`) — not a "future" case. No new work. |
| G1 | `PUT /:paperID/zip?file=` (replace the **published** source zip) | **Gap → new endpoint**, see §3.1 |
| G2/G3 | `GET /:paperID/zip?version={last,X}` (typesetter's per-round output) | **Gap → new endpoint**, see §3.2 |
| G4 | `GET /:paperID/status` (JSON metadata) | **Gap → new endpoint**, see §3.3 |
| G5 | `GET /:paperID/messages?n=` | **Gap → new endpoint**, see §3.4 |
| G6 | `POST /:paperID/zip?file=&message=` (upload + notify) | **Gap, redefined → new endpoint**, see §3.5 |
| G7 | `GET /:journal/production` | **Gap → new endpoint**, see §3.6 |
| G8 | `GET /:journal/:typesetter/papers?start_date=&end_date=` | **Gap → new endpoint**, see §3.7 |
| G9 | `PUT /:paperID/assign/:typesetter` | **Gap → new endpoint**, see §3.8 |

### Implementation notes carried over from the requirements discussion

- **Versioning** = `TypesettingRound.round_number`; "latest version" = highest `round_number` for the article.
- **Messages** are filtered from `article.date_accepted` onward; serialize with a plain DRF `ModelSerializer`.
- **"Production phase"** (for G7) = `ArticleWorkflow.state in {READY_FOR_TYPESETTER, TYPESETTER_SELECTED, PROOFREADING, READY_FOR_PUBLICATION}` — **note this deliberately excludes `ACCEPTED`**, unlike the existing `states_when_article_is_considered_in_production` constant (`logic.py:181-187`, which does include `ACCEPTED`). This is an intentional divergence for this endpoint only; the existing constant is untouched.
- **Typesetter-specific list** (G8) = only the typesetter's *currently assigned* papers (narrows the original "including published, for administrative monitoring" wording — deliberate, per clarification).
- **Assign typesetter** (G9) reuses `AssignTypesetter` as-is.

## 3. Endpoint specifications

### 3.1 G1 — `PUT /article/<pk>/zip/` (replace published source zip)

**Reuse:** the only code that writes `ArticleWorkflow.publication_galleys_source_file` is `BeginPublication._store_prepared_source` (`logic__production.py:1591-1634`) — it does a DOI/pubid `.tex`-macro rewrite, repacks the archive, and calls `save_file_to_article`. It's tightly coupled to the publication FSM step, not a generic "swap this file" operation.

**New component:** extract the reusable half into a standalone dataclass in `logic__production.py`:

```python
@dataclasses.dataclass
class InjectIdentifiersAndStoreSourceZip:
    """Rewrite pubid/DOI/publication-date into the .tex source, repack, and store as
    ArticleWorkflow.publication_galleys_source_file.

    Extracted from BeginPublication._store_prepared_source, decoupled from the
    READY_FOR_PUBLICATION state gate (that check stays in BeginPublication.run(),
    which calls this class as its own tail)."""

    workflow: ArticleWorkflow
    source_zip: Path  # or BytesIO — same contract as BeginPublication.source_files
    user: Account

    def run(self) -> JanewayFile: ...
```

`BeginPublication.prepare_source_files()` becomes a thin caller of this class (same behaviour, no regression).

**New API view:** `ArticleZipUploadView(LoggedRequestMixin, PublishedArticleAccessMixin, APIView).put()` — reads the uploaded body (same `GalleyUploadSerializer`-style validation as the existing galley POST, but content-type = zip), calls `InjectIdentifiersAndStoreSourceZip(workflow, source_zip, user=request.user).run()`.

**Permissions:** same as existing zip GET — `PublishedArticleAccessMixin` / `IsEOOrTypesetterForArticle` (stage=PUBLISHED gate is correct here — this route replaces the *published* zip, unlike G2/G3 which are pre-publication).

### 3.2 G2/G3 — `GET /article/<pk>/zip/?version={last|N}` (typesetter's per-round output)

**Verified data model:** `TypesettingAssignment.round` is a `OneToOneField` to `TypesettingRound` (`typesetting/models.py:99-102`), and `TypesettingRound.round_number` (`typesetting/models.py:43`) is unique per article (`unique_together = ("round_number", "article")`, `typesetting/models.py:47-50`). So `TypesettingAssignment.objects.get(round__article=article, round__round_number=N)` is a clean, always-≤1-row lookup.

**Distinct from `HandleDownloadRevisionFiles`:** that class (`logic__production.py:516-601`, used by the SSR `DownloadRevisionFiles` view, `views__production.py:180-221`) always rebuilds a fresh zip from the article's **live current** manuscript/source/data files (`_gather_files`, `logic__production.py:522-532`) — there is no per-round historical snapshot mechanism in the data model. It answers a different question ("what does the author need to revise right now") than "what did the typesetter upload for round N" (`files_to_typeset`, a plain M2M to `core.File` on `TypesettingAssignment`, `typesetting/models.py:139-143`).

**Decision:** `version=` resolves against `TypesettingAssignment.files_to_typeset`, **not** `HandleDownloadRevisionFiles`.

**New component**, `logic__production.py`:

```python
@dataclasses.dataclass
class GetTypesettingRoundArchive:
    """Look up the typeset-output file for a given (or latest) round.

    Testable/reusable independently of the API (e.g. if an SSR download
    link for a specific round is ever added)."""

    article: Article
    round_number: Optional[int] = None  # None → latest (highest round_number)

    def run(self) -> JanewayFile:
        """Raises TypesettingAssignment.DoesNotExist if no assignment for that round."""
        qs = TypesettingAssignment.objects.filter(round__article=self.article)
        assignment = (
            qs.get(round__round_number=self.round_number)
            if self.round_number is not None
            else qs.order_by("-round__round_number").first()
        )
        if assignment is None:
            raise TypesettingAssignment.DoesNotExist
        return assignment.files_to_typeset.get()
```

**New API view:** extends `ArticleZipDownloadView`'s pattern but pre-publication — needs its own `get_article`/mixin (article not yet `STAGE_PUBLISHED`; see §4 permission note for G9, same class of problem applies here — do **not** reuse `PublishedArticleAccessMixin` unmodified). `?version=` query param: absent or `last` → `round_number=None`; else parsed as `int`. 404 (`NOT_FOUND`) on `TypesettingAssignment.DoesNotExist` or `File.DoesNotExist` (no `files_to_typeset` row), matching the existing error-shape convention in `api/views.py`.

### 3.3 G4 — `GET /article/<pk>/status/`

**No dataclass needed** — pure read-only assembly, nothing else in the codebase computes this today.

**Verified field sources:**
- `article.get_doi()` — `submission/models.py:1635` (Janeway core).
- `article.license` → `Licence` FK (`submission/models.py:1083-1084`); fields available: `name`, `short_name`, `url`, `text` (`submission/models.py:3138-3149`).
- `workflow.state` / `get_state_display()`.
- `workflow.get_latest_typesetting_assignment().round.round_number` — last version (`models.py:691-713`).
- authors: `article.correspondence_author` (`submission/models.py:1109-1114`) + full author list (Janeway's `FrozenAuthor`/`article.frozen_authors()`), title = `article.title`.

**Open, non-blocking (default unless told otherwise):**
- "authors" = `correspondence_author` + frozen authors list.
- "copyright" = the four `Licence` fields nested as an object, not a rendered string.

**New serializer** (plain DRF `Serializer`, no ModelSerializer — fields are pulled from `Article` + `ArticleWorkflow` + `Licence`, not a single model): `ArticleStatusSerializer` in `api/serializers.py` (or a new `api/serializers__production.py` if that file grows — see §5).

**Permissions:** must work pre-publication (production phase) — same permission-model subtlety as G9 (see §4).

### 3.4 G5 — `GET /article/<pk>/messages/?n=`

**Reuse, extend rather than duplicate:** `get_messages_related_to_me()` (`communication_utils.py:58-140`) already implements the full role-based visibility + ordering (`-created`) that both the SSR `ArticleMessages` view (`views.py:2113`) and this endpoint need. It currently takes no date/limit param.

**Change:** add optional kwargs to the existing function signature:

```python
def get_messages_related_to_me(
    user: Account,
    article: Article | None = None,
    journal: Journal | None = None,
    since: datetime.datetime | None = None,
    limit: int | None = None,
) -> QuerySet[Message]:
```
`since` applies `.filter(created__gte=since)` before the existing `.order_by("-created")`; `limit` applies `[:limit]` last. Both SSR `ArticleMessages` and the new endpoint call the same function; the endpoint additionally defaults `since=article.date_accepted` (per the "filter by acceptance date" instruction) and reads `n=` from the querystring into `limit`.

**New serializer:** plain `ModelSerializer` over `Message` (fields: `id`, `actor`, `subject`, `body`, `message_type`, `created`, `recipients` — recipients as PKs or nested minimal repr, TBD at implementation time against what the consumer actually needs).

### 3.5 G6 — combined upload + notify → redefined as message+attachment endpoint

Original spec text conflated this with G1 ("upload zip + notify"). **Redefined during review:** this is just *"send a message with a plain file attachment to the corresponding author, as one JSON call"* — no zip/galley involvement, no dependency on G1.

**Reuse:** `HandleMessage.run()` (`logic.py:3085-3319`) already implements exactly this — create `Message`, attach a plain file, `emit_notification()` — but has **zero current callers** (only its two `@staticmethod`s, `allowed_recipients_for_actor` and `can_write_to`, are used elsewhere — confirmed via full-repo grep). The actual working SSR path is `MessageForm.save()` (`forms.py:1141-1186`), which duplicates the save+attach+notify sequence and additionally handles: `read_by_eo` bookkeeping (`forms.py:1158-1167`), personal-note recipient/read handling (`forms.py:1153-1157`), and replacing a note's previous attachment (`forms.py:1148-1149`).

**Decision (locked in):** revive `HandleMessage.run()` as the single shared implementation:
1. Generalize `HandleMessage` to accept a **list** of recipients (today's `run()` takes a single `form_data["recipient"]` id) and to perform the `read_by_eo` / note bookkeeping that `MessageForm.save()` currently does inline, so it becomes a strict superset.
2. Refactor `MessageForm.save()` (`forms.py:1141-1186`) to build an unsaved `Message` + resolved recipients/attachment from its `cleaned_data`, then delegate to `HandleMessage(...).run()` instead of duplicating the sequence. `current_note` attachment-replacement (`forms.py:1148-1149`) is a note-specific concern and stays in the form (or is passed through as an extra `HandleMessage` field — decide at implementation time based on which reads cleaner; do not silently drop it).
3. Existing tests to keep green through this refactor: `tests/test_communications.py` — `test_write_message_as_eo_sets_read_by_eo_flag` (`:813`), `test_write_message_as_director_does_not_set_read_by_eo_flag` (`:742`), the `MessageRecipients...read is saved_message.read_by_eo` assertion at `:572`, plus all `HandleMessage.can_write_to`/`allowed_recipients_for_actor` tests (`:954-1000`, unaffected — those two methods aren't touched).
4. New API endpoint: `ArticleWriteMessageView.post()` — builds `Message(actor=request.user, target=article, message_type=USER, subject=..., body=...)`, resolves `recipients=[article.correspondence_author]`, `attachment=request.FILES.get(...)`, calls `HandleMessage(...).run()`.

**Risk flag:** this is the one piece of work in this spec that touches existing, tested SSR behaviour (note/read-by-eo bookkeeping) rather than purely adding new code — budget explicit regression testing for it, not just new-endpoint tests.

### 3.6 G7 — `GET /journal/<code>/production/`

**Extract, don't duplicate:** `EOProduction._apply_base_filters` (`views.py:522-531`) and `TypesetterPending._apply_base_filters` (`views__production.py:98-106`) each independently filter `ArticleWorkflow` by a `state__in=[...]` list. Extract the shared shape into one function used by both existing SSR mixins and the new endpoint:

```python
# logic.py, near the states_when_article_is_considered_* constants (~line 187)
def production_articles_queryset(
    journal: Journal, states: list[ArticleWorkflow.ReviewStates]
) -> QuerySet[ArticleWorkflow]:
    return ArticleWorkflow.objects.filter(article__journal=journal, state__in=states)
```
(Exact base queryset/joins to be reconciled against `ArticleWorkflowBaseMixin._apply_base_filters`'s existing implementation at implementation time — the two SSR views currently each start from `self.get_queryset()`/`ArticleWorkflowBaseMixin._apply_base_filters(self, qs)`, which do more than a flat filter, e.g. journal-scoping already baked in via the view's own queryset. The extraction must preserve that behaviour for the two existing callers.)

**States for this endpoint specifically:** `READY_FOR_TYPESETTER, TYPESETTER_SELECTED, PROOFREADING, READY_FOR_PUBLICATION` (see §2 note — deliberately excludes `ACCEPTED`).

**Response fields**, all verified reachable:
- `preprint_id` — `ArticleWorkflow.preprint_id` property (`models.py:540-542`, `f"{journal.code}_{article_id}"`).
- `special_issue` — `article.primary_issue`.
- `status` — `workflow.state` / `get_state_display()`.
- `typesetter` — `workflow.get_latest_typesetting_assignment().typesetter` (`models.py:691-713`; handle `None`).
- `date_accepted` — `article.date_accepted`.
- `last_status_update` — `workflow.modified` (from `TimeStampedModel`).

**New serializer:** `ProductionArticleSerializer`, new viewset/APIView `JournalProductionListView`.

### 3.7 G8 — `GET /journal/<code>/typesetter/<typesetter_pk>/papers/?start_date=&end_date=`

**Reuse:** `TypesetterWorkingOn._apply_base_filters` (`views__production.py:109-120`) already implements exactly this join:

```python
qs.filter(
    state__in=states_when_article_is_considered_typesetter_working_on,
    article__typesettinground__isnull=False,
    article__typesettinground__typesettingassignment__typesetter__pk=self.request.user.pk,
)
```

**Change:** the same join, **parameterized by an arbitrary `typesetter` pk** (path param) instead of `self.request.user.pk`, plus `article__date_accepted__range=[start_date, end_date]`. Confirmed as a deliberate narrowing vs. the original spec text ("including published, for administrative monitoring") — only *currently assigned* papers, per instruction.

**New serializer/view:** `TypesetterPapersListView`, reusing `ProductionArticleSerializer` from §3.6 if the field set matches, otherwise a thin subclass.

### 3.8 G9 — `PUT /article/<pk>/assign/<typesetter_pk>/`

**Reuse:** `AssignTypesetter` (`logic__production.py:150-288`) unchanged — call `AssignTypesetter(article=article, typesetter=target_user, request=request).run()`.

**Verified actor-permission gap:** `AssignTypesetter._check_conditions()` (`logic__production.py:178-195`) checks the FSM state transition and that the **target** has the typesetter role (`has_typesetter_role_by_article`, `permissions.py:659-676`) — it never checks who the **actor** is, and the class calls the raw FSM transition methods (`typesetter_takes_in_charge()`/`system_assigns_typesetter()`), not `has_transition_perm()`, so the `permission=has_typesetter_role_by_article` guard declared on the `typesetter_takes_in_charge` FSM transition (`models.py`) is never enforced by this call path. **The DRF permission class is the only actor-side gate for this endpoint.**

**Permission class decision:** do **not** reuse `IsEOOrTypesetterForArticle` unmodified — traced its call graph:
```
IsEOOrTypesetterForArticle.has_object_permission → is_article_typesetter_or_eo(workflow, user)
  = has_eo_role(user) or is_article_typesetter(workflow, user)
  = has_eo_role(user) or TypesettingAssignment.objects.filter(round__article=article, typesetter=user).exists()
```
`is_article_typesetter` (`permissions.py:677-693`) requires an **existing** `TypesettingAssignment` row. For the primary case — article in `READY_FOR_TYPESETTER`, no assignment yet, typesetter self-assigning — this returns `False` even for the correct/eligible typesetter, blocking the exact action it's meant to permit.

**Resolution:** use `IsEOOrTypesetterForArticle` as `permission_classes` (its `has_permission` — the looser, journal-wide `has_typesetter_role_on_any_journal`/`has_eo_role` check, `jcom_profile/permissions.py:42-53,286-298` — is what actually gates the request, matching `TypesetterTakeInCharge`'s current permission model), **but** write a **new mixin** (not `PublishedArticleAccessMixin`) that:
- fetches the `ArticleWorkflow` filtered by `state=READY_FOR_TYPESETTER` (not `stage=STAGE_PUBLISHED` — assignment happens pre-publication),
- does **not** call `check_object_permissions`/`has_object_permission` (only the looser `has_permission` applies here).

**Path constraint:** `<typesetter_pk>` in the URL must equal `request.user.pk` (self-assign only, matching `TypesetterTakeInCharge`'s existing behaviour) — `AssignTypesetter` itself re-validates the target actually holds the typesetter role, but does not check actor==target, so this check belongs in the view.

## 4. Shared new mixin (G2/G3, G4, G9)

G2/G3, G4, and G9 all need a pre-publication article fetch, unlike the existing `PublishedArticleAccessMixin` (`api/mixins.py:42-49`, which filters `stage=STAGE_PUBLISHED`). Introduce one shared mixin, e.g. `ProductionArticleAccessMixin` in `api/mixins.py`, parameterized by which `ArticleWorkflow.state`(s) are acceptable for that specific endpoint, and by whether object-level permission checks apply (per the G9 note in §3.8, self-assign is `has_permission`-only).

## 5. File-by-file impact summary

| File | Change |
|---|---|
| `logic__production.py` | New `InjectIdentifiersAndStoreSourceZip` (extracted from `BeginPublication._store_prepared_source`); `BeginPublication.prepare_source_files()` updated to call it. New `GetTypesettingRoundArchive`. |
| `logic.py` | New `production_articles_queryset()` near the `states_when_article_is_considered_*` constants (~:187). `HandleMessage` generalized (recipients list, read-by-eo/note bookkeeping) — see §3.5. |
| `communication_utils.py` | `get_messages_related_to_me()` gains `since=`/`limit=` kwargs (~:58). |
| `forms.py` | `MessageForm.save()` (~:1141) refactored to delegate to `HandleMessage.run()`. |
| `views.py` | `EOProduction._apply_base_filters` (~:522) reconciled with the new `production_articles_queryset()` (no behaviour change for the existing view). |
| `views__production.py` | `TypesetterPending`/`TypesetterWorkingOn` (~:65-120) likewise reconciled where they share the extracted queryset helper. |
| `api/mixins.py` | New `ProductionArticleAccessMixin`. |
| `api/permissions.py` | New EO-only permission class for G9 (do not reuse `IsEOOrTypesetterForArticle`'s object-permission half). |
| `api/views.py` or new `api/views__production.py` | New views: `ArticleZipUploadView` (G1), `ArticleZipVersionView` (G2/G3), `ArticleStatusView` (G4), `ArticleMessagesView` (G5), `ArticleWriteMessageView` (G6), `JournalProductionListView` (G7), `TypesetterPapersListView` (G8), `AssignTypesetterView` (G9). Given the existing plugin convention of splitting `views.py`/`views__production.py` by concern, prefer a new `api/views__production.py`. |
| `api/serializers.py` or new `api/serializers__production.py` | New serializers: `ArticleStatusSerializer`, `MessageSerializer`, `ProductionArticleSerializer`. |
| `api/urls.py` | New routes for all of the above. |
| `tests/test_api.py` | New tests per endpoint; extend existing logging/auth test pattern (`_api_request_log_line`, `:13-17`) to the new views. |
| `tests/test_communications.py` | Must stay green through the `HandleMessage`/`MessageForm` refactor (see §3.5 point 3 for the specific tests at risk). |

## 6. Open items to confirm before/while implementing

1. **G4 "authors"/"copyright" shape** — defaulting to `correspondence_author` + frozen-authors list, and nested `Licence` fields — flag if a different consumer-facing shape is expected.
2. **G7 queryset extraction exact shape** — `ArticleWorkflowBaseMixin._apply_base_filters` does more than a flat `state__in` filter (journal-scoping via the view's own `get_queryset()`); the extracted `production_articles_queryset()` must be reconciled against that at implementation time without changing the two existing SSR views' behaviour.
3. **`current_note` attachment-replacement** (§3.5 point 2) — decide whether it lives in the form or is passed through to `HandleMessage` as an extra field; don't drop it silently.
