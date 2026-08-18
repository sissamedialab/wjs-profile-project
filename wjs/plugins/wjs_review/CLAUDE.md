# CLAUDE.md — `wjs_review`

Guidance specific to this plugin. The repo-root `CLAUDE.md` and `.claude/rules/*.md` still apply — this file
only documents what's particular to `wjs_review`'s architecture and the conventions (and inconsistencies)
that have accumulated in this specific plugin. Don't repeat the generic Django/repo rules from
`.claude/rules/architecture-django.md` here; do check the "Known deviations" section at the bottom before
assuming this plugin follows them uniformly — several of the rules there are honored in most places but
violated in specific, documented spots.

## What this plugin is

The editorial pipeline (submission → editor assignment → peer review → decision → revision → typesetting
→ proofreading → publication), modeled as a `django-fsm` state machine on `ArticleWorkflow`. This is the
largest, most central piece of business logic in the repo (`models.py` ~2400 lines, `logic.py` ~4900 lines,
`views.py` ~4100 lines). Everything below assumes familiarity with the root `CLAUDE.md`'s "`wjs_review`: the
review-workflow engine" section.

## The state machine: `ArticleWorkflow` + `states.py`

### `ArticleWorkflow` (`models.py:393`)

`state = FSMField(default=ReviewStates.INCOMPLETE_SUBMISSION, choices=ReviewStates.choices, ...)`. 19 states
(`ReviewStates`, `models.py:394-413`): `EDITOR_TO_BE_SELECTED`, `EDITOR_SELECTED`, `SUBMITTED`,
`TO_BE_REVISED`, `WITHDRAWN`, `REJECTED`, `INCOMPLETE_SUBMISSION`, `NOT_SUITABLE`,
`PAPER_HAS_EDITOR_REPORT`, `ACCEPTED`, `TYPESETTER_SELECTED`, `PAPER_MIGHT_HAVE_ISSUES`, `PROOFREADING`,
`READY_FOR_TYPESETTER`, `PUBLISHED`, `READY_FOR_PUBLICATION`, `SEND_TO_EDITOR_FOR_CHECK`,
`PUBLICATION_IN_PROGRESS`, `UNDER_APPEAL`.

29 `@transition`-decorated methods (`models.py:844-1193`) drive movement between them. Every transition
passes a `permission=` callable from `permissions.py`; most carry a `# TODO: conditions=[]` comment — deferred,
not forgotten. Only two transitions (`typesetter_deems_paper_ready_for_publication`,
`author_deems_paper_ready_for_publication`) actually use `conditions=[can_be_set_rfp_wrapper]`. One transition
(`system_process_submission`) picks its target dynamically via `GET_STATE(process_submission, states=[...])`,
delegating the real decision to the module-level `process_submission()` function (`models.py:98-112`).

`ReviewComputedStates` (`models.py:443-453`) are **virtual, non-stored** sub-states (e.g.
`REQUESTED_MINOR_REVISION`, `WAITING_FOR_DECISION`, `IN_REVIEW`) computed by `ArticleWorkflow.state_value`
(`@cached_property`, `models.py:557-611`) from pending/completed assignments and revision requests — used
only for finer-grained display labels (`state_label`), never for `@transition` gating.

`Decisions` (`models.py:415-436`, `TextChoices`) re-uses Janeway's `review.const.EditorialDecisions` values
for the decisions that must interoperate with Janeway core (`minor_revisions`, `major_revisions`,
`technical_revisions`, `open_appeal`) and adds WJS-only ones (`accept`, `reject`, `not_suitable`,
`requires_resubmission`). `Decisions.decision_choices` (a `@classmethod @property`) filters out
`REQUIRES_RESUBMISSION`/`OPEN_APPEAL` for use in decision forms.

### `states.py` — state→action registry

**Pattern: one `BaseState` subclass per `ReviewStates` value**, named identically in CamelCase
(`ReviewStates.EDITOR_SELECTED` → `class EditorSelected(BaseState)`, 19 classes total, `states.py:672-1529`).
Lookup is **`globals()`-as-registry**, not an explicit dict:

```python
@classmethod
def get_state_class(cls, workflow: ArticleWorkflow) -> Type["BaseState"]:
    return globals()[workflow.state]
```

Each class exposes up to three tuples of dataclass instances, always **concatenated with the base**
(`BaseState.article_actions + (...)`), never overridden outright:
- `article_actions: tuple[ArticleAction, ...]` — whole-article actions (accept, reject, assign reviewer, ...).
- `article_buttons: tuple[ArticleButton, ...]` — generic UI buttons not tied to a transition.
- `review_assignment_actions: tuple[ReviewAssignmentAction, ...]` — actions scoped to one
  `ReviewAssignment`/`WorkflowReviewAssignment`.

Each dataclass (`ArticleButton` at `states.py:329`, `ArticleAction(ArticleButton)` at `states.py:395`,
`ReviewAssignmentAction` at `states.py:428`) carries `permission: Optional[Callable]` (a `permissions.py`
function) and `condition: Optional[Callable]` (a `conditions.py` function), ANDed together in
`.is_available(...)`:

```python
def is_available(self, workflow, user) -> bool:
    """Return true if permission and condition are both met."""
    return self._has_permission(workflow, user) and self._condition_is_met(workflow, user)
```

**Never hardcode state-based UI logic in views/templates** — always go through
`BaseState.get_state_class(workflow)` for the tuple appropriate to the *current* state, then filter with
`.is_available()`. Two consumption points: template tags `get_article_actions`/`get_article_buttons`
(`templatetags/wjs_review.py:145-183`), and `WorkflowReviewAssignment.get_actions_for_user`
(`models.py:1896-1905`) for review-scoped actions. `views__production.py:472` is the one place a view calls
`get_state_class` directly (server-side gating), rather than only templates.

`BaseState.article_requires_attention(cls, article, user)` (`states.py:643-658`) is a **role-keyed hook-method
convention**: it dispatches to `article_requires_<role>_attention` on the state subclass if one exists for the
caller's role (via `communication_utils.role_for_article`), falling back to a generic unread-message check —
define the hook only where a state actually needs to flag urgency for that role; absence means no flag, not
an error.

## Business logic layer: `@dataclass` + `run()`

The pattern from `.claude/rules/architecture-django.md` ("Business logic architecture") is followed with high
consistency across **65 classes** (`logic.py`: 38, `logic__production.py`: 18, `logic__visibility.py`: 9).
Constructor holds all context (article/workflow/request/user/form_data); `run()` wraps the mutation in
`transaction.atomic()` and returns the primary affected model instance:

```python
@dataclasses.dataclass
class AssignToEditor:
    editor: Account
    article: Article
    request: HttpRequest
    workflow: Optional[ArticleWorkflow] = None
    ...

    def run(self) -> WjsEditorAssignment:
        with transaction.atomic():
            self._create_workflow()
            if not self._check_conditions():
                raise ValueError("Invalid state transition")
            self.assignment = BaseAssignToEditor(...).run()
            self._update_state()
        return self.assignment
```

`HandleDecision.run()` (`logic.py:2332-2947`) is the **one class in the whole set** that uses
`select_for_update()` (`logic.py:2937`, to serialize concurrent decisions against double-click races) — many
other classes doing read-check-write on shared rows (`AssignToEditor`, `AssignToReviewer`, `AssignTypesetter`,
`BeginPublication`) don't, which is a judgment call rather than a rule violation, but worth knowing before
assuming every `run()` is race-safe.

### File split (a real conceptual split, not an arbitrary size cut)

- **`logic.py`** — the core review workflow: round creation, editor/reviewer assignment & invite, review
  evaluate/submit, revision requests (`AuthorHandleRevision` + `PopulateRevisionStep1/4/5/6/7`), decisions
  (`HandleDecision`), messaging (`HandleMessage`), editor deassignment/appeal/withdrawal, plus PDF/LaTeX
  report generation (`YakuninClient`, `ConvertEditorLatexReport`/`ConvertReviewerLatexReport`).
- **`logic__production.py`** — everything downstream of acceptance: `VerifyProductionRequirements`,
  `AssignTypesetter`, `RequestProofs`, `TypesettedFilesUpload`, supplementary/annotated file handling,
  `AttachGalleys`, `ReadyForPublication`, `BeginPublication`, `FinishPublication`.
- **`logic__visibility.py`** — a different concern entirely: **who can see what**, not workflow mutation.
  Classes implement `check()`/`check_default()`, not `run()`. `PermissionChecker` (`logic__visibility.py:579`)
  is the one genuine registry in the permission system — see "Permissions & visibility" below.

### Invocation

Forms call `self.get_logic_instance(cleaned_data)` to build the logic object, then `save()` calls
`.run()` inside try/except, re-raising `ValidationError` as a non-field form error (per
architecture-django.md's documented pattern — e.g. `forms.py:335-351` `AssignToReviewer`, `forms.py:799-821`
`HandleDecision`). **Deviation**: `logic__production.py` classes are invoked from `views__production.py`, and
some button-only production views (no form data at all) call `.run()` directly from a plain `View.post()`,
bypassing the form layer — e.g. `TypesetterTakeInCharge.post()` (`views__production.py:612-646`). Treat this
as an accepted exception for parameterless actions, not a pattern to copy for anything with form data.

Shared behavior between logic classes is composed via `Base*` dataclasses (`BaseAssignToEditor`,
`BaseDeassignEditor`), not mixins — matching the "extract to a mixin/helper, don't subclass one business
logic class from another" rule in spirit if not literally. **Deviation**: `BasePopulateRevisionStep` →
`PopulateRevisionStep1/4/5/6/7` and `BaseConvertLatexReport` → `ConvertEditorLatexReport`/
`ConvertReviewerLatexReport` genuinely subclass one business-logic class from another (template-method style)
— an intentional exception in these two spots, not the general pattern to follow for new code.

**Known correctness-adjacent deviations worth knowing before writing new logic classes here:**
- Most condition-check failures raise plain `ValueError`, not `django.core.exceptions.ValidationError` as the
  architecture rule specifies — only a minority of classes actually raise `ValidationError`
  (`logic.py:1175,1286,2407,2409,2411,3075,3303,3409`; `logic__production.py:502,511`). Callers compensate by
  catching `except (ValueError, ValidationError)` everywhere. This is systemic, not accidental — but don't
  assume catching only `ValidationError` is safe anywhere in this plugin.
- A handful of `run()` methods have no `transaction.atomic()` at all (`PressNotificationHandler`,
  `ConvertManuscriptToPdf`, `BaseConvertLatexReport`, `AccessModeSpecialRequestNotification` in `logic.py`;
  `HandleDownloadRevisionFiles`, `AttachGalleys`, `TypesetterTestsGalleyGeneration` in
  `logic__production.py`) — some are legitimately I/O-only, but `AttachGalleys` does write to the DB
  (galley creation) without one.

## Forms & views

CBV-heavy (`ListView`/`DetailView`/`UpdateView`/`CreateView`/`FormView`/`DeleteView`/`View`,
`django_filters.views.FilterView`), 89 view classes across `views.py`/`views__production.py`/
`views__visibility.py`, 43 form classes across `forms.py`/`forms__production.py`/`forms__visibility.py`. The
same `<module>.py` / `<module>__production.py` / `<module>__visibility.py` split used for `logic.py` is
applied consistently to `forms.py` and `views.py` too.

### Mixins

`mixins.py` defines exactly 5: `EditorRequiredMixin`, `ReviewerRequiredMixin`, `AuthenticatedUserPassesTest`
(login + `UserPassesTestMixin` + a `load_initial()` hook, base for most non-list views),
`OpenReviewMixin` (`DetailView` for `WorkflowReviewAssignment`, access-code **or** logged-in-user lookup —
encapsulates editor/typesetter/EO visibility rules in `get_queryset()`), `ArticleAssignedEditorMixin`.
`HtmxMixin`/`PaginatedViewMixin` come from the shared `wjs.jcom_profile.mixins` (cross-plugin, not
`wjs_review`-local). **Note**: `BaseRelatedViewsMixin` (views.py:175) and `ArticleWorkflowBaseMixin`
(views.py:256) are genuine cross-view mixins used by ~25+ subclasses each (the whole
role-×-lifecycle pending/archived list grid), but live inline in `views.py` rather than in `mixins.py` —
check there too, `mixins.py` alone doesn't show the full mixin surface.

### Form → logic pattern

```python
def get_logic_instance(self, cleaned_data):
    return AssignToReviewer(reviewer=cleaned_data["reviewer"], workflow=self.instance, ...)

def save(self, commit=True):
    try:
        self.get_logic_instance(self.cleaned_data).run()
    except ValidationError as e:
        self.add_error(None, e)
        raise
    return self.instance
```
matching `EvaluateReviewForm.save` (`forms.py:582-600`) and its view `EvaluateReviewRequest.form_valid`
(`views.py:1541-1552`) almost verbatim against the documented sample in architecture-django.md.

**Deviation — forms that skip the logic class and mutate models directly in `save()`**: `MessageForm.save()`
(`forms.py:1141-1186`, wraps its own `transaction.atomic()` and does attachment/recipient/notification work
inline despite `HandleMessage` existing in `logic.py`), `UserPermissionsForm.save()`
(`forms__visibility.py:82-96`, raw `PermissionAssignment.objects.update_or_create(...)`),
`SectionOrderForm.save()`/`move_up`/`move_down` (`forms__production.py:282-334`). Don't take these as the
model to copy for a new form with side effects — prefer the logic-class pattern above.

### Naming conventions

- Forms: 100% consistent `...Form`/`...FormSet` suffix, with `Base<X>Form` abstract bases
  (`BaseInviteSelectReviewerForm` → `SelectReviewerForm`/`InviteUserForm`).
- Views: **inconsistent** — only 26/89 end in `View`; most use bare descriptive names
  (`EditorPending`, `ArticleDecision`, `DeselectReviewer`). Don't assume a `View` suffix is required for new
  views in this plugin; match whichever neighboring view you're extending.
- A strong **role × lifecycle-stage** naming grid drives the pending/archived/production list views:
  `EditorPending`/`EditorArchived`, `EOPending`/`EOArchived`/`EOProduction`/`EOWorkOnAPaper`,
  `DirectorPending`/`DirectorArchived`/`DirectorProduction`/`DirectorWorkOnAPaper`, `AuthorPending`/
  `AuthorArchived`, `ReviewerPending`/`ReviewerArchived`, `TypesetterPending`/`TypesetterWorkingOn`/
  `TypesetterArchived` — all subclass `ArticleWorkflowBaseMixin`, overriding only `_apply_base_filters`/
  `role`/`title`. Follow this grid when adding a new role×stage list rather than inventing a new base.
- Logic-class names mirror form names 1:1 (`AssignToReviewer` ↔ its form, `HandleDecision` ↔ `DecisionForm`).

### URL naming — known, acknowledged violation

`.claude/rules/code-style-django.md` requires hyphenated URL names/paths (`my-list` / `/my-list/`). **This
plugin's `urls.py` almost entirely uses underscores instead** (~93 of ~97 `name=` values, ~28 of ~32 path
segments) — e.g. `path("select_reviewer/<int:pk>/", ..., name="wjs_select_reviewer")`. Only a handful of
recently-added entries near the end of the file use hyphens
(`name="toggle-issue-batch"`, `name="elaborate-latex-report"`). There's a standing `# TODO: rethink naming of
views` comment (`urls.py:263-264`) acknowledging the inconsistency. **When adding a new URL here, prefer the
hyphenated house style from `code-style-django.md`** — don't propagate the underscore convention just because
it's the local majority; the plugin is already drifting toward hyphens for new entries.

### Other known deviations (fat views / missing error handling)

- `ArticleDecision.form_valid` (`views.py:2007-2033`) runs a service class directly in the view instead of
  through the form.
- `SelectReviewerView` (`views.py:963-1146`) hand-rolls pagination for a side queryset instead of using
  django-filter/a queryset method.
- `ArticleDetails.get_current_review_assignment`/`pending_proofs_version` (`views.py:1310-1344`) and all of
  `EditUserPermissions` (`views__visibility.py:34-302`, ~170 lines of cross-model query/filter logic) put
  non-trivial domain logic in the view rather than a queryset method or logic class.
- `DeselectReviewer` (`views.py:3363-3437`) has no `form_valid` override even though
  `DeselectReviewerForm.save()` can raise `ValidationError` — an unhandled `ValidationError` here surfaces as
  a raw 500 instead of a form error. **When adding `form_valid` to a new view, always catch
  `(ValueError, ValidationError)`** per the working examples (`EvaluateReviewRequest`, `TypesetterUploadFiles`),
  not per `DeselectReviewer`.

## Permissions & visibility

Two independent layers — don't confuse them:

1. **Button/action visibility** (`permissions.py` + `conditions.py`, consumed by `states.py`): `permissions.py`
   defines ~40 plain predicate functions `fn(instance, user) -> bool` (e.g. `is_article_editor`,
   `has_eo_role_by_article`); OR-composition is just Python `or` inside a function body, there's no
   combinator class. `conditions.py` defines business-state predicates (`reviewer_is_late`, `review_done`,
   `pending_revision_request`, ...). Both are wired into `states.py`'s `ArticleAction`/`ArticleButton`/
   `ReviewAssignmentAction` dataclasses via `permission=`/`condition=` fields, ANDed in `.is_available()`
   (see "states.py" above). This is **not** the registry pattern despite the naming — it's direct function
   references passed as dataclass field values.

2. **Per-object visibility override** (`PermissionAssignment` model + `logic__visibility.PermissionChecker`):
   `PermissionAssignment` (`models.py:2019-2126`) is a generic-FK, DB-backed grant
   (`user` × `content_type`/`object_id` → `permission: all|no_names|deny` +
   `permission_secondary: all|deny`), unique per `(user, content_type, object_id)`. Any model that can be a
   grant's target implements duck-typed `permission_label`/`permission_subject` properties (`ArticleWorkflow`,
   `EditorDecision`, `EditorRevisionRequest`, `WorkflowReviewAssignment` all do). **This is the one place in
   the whole permission subsystem that matches the repo's "prefer registries over direct imports" rule
   literally**: `PermissionChecker._permission_classes` (`logic__visibility.py:584-592`) is a dict of
   `permissions.py` predicate → role-specific `BasePermissionChecker` subclass
   (`SuperUserPermissionChecker`, `DirectorPermissionChecker`, `EditorPermissionChecker` →
   `SpecialIssueEditorPermissionChecker`, `TypesetterPermissionChecker`, `AuthorPermissionChecker`,
   `ReviewerPermissionChecker`); `PermissionChecker.__call__` iterates it, ORing in every role the user
   actually has (so a director who's also a reviewer gets the union). Each checker's `.check()` queries
   `PermissionAssignment` and falls back to a hardcoded `.check_default()` when no explicit grant exists.
   `ArticleWorkflow.get_review_versions()`/`get_production_versions()` and the `ArticleDetails` view's
   `test_func` both go through `PermissionChecker`, never `PermissionAssignment` directly.

**`role_cache.py`** caches the computed `role_for_article(user, article)` string (`EDITOR_ROLE`,
`REVIEWER_ROLE`, `AUTHOR_ROLE`, `TYPESETTER_ROLE`, `DIRECTOR_ROLE`, ...) per `(user_id, article_id)`, key
`wjs_review:role_for_article:v{ROLE_CACHE_VER}:{user_id}:{article_id}` (`ROLE_CACHE_VER`/`ROLE_CACHE_TTL`
overridable via settings, 15-day default TTL). Invalidation is mostly **refresh-in-place** (recompute and
overwrite specific entries) triggered from `signals.py` receivers on `Article`/`AccountRole`/assignment models,
always deferred via `transaction.on_commit`; `invalidate_role_cache_for_user` uses Redis's
`delete_pattern` for a full per-user wildcard clear (requires Redis-backed cache — see the root CLAUDE.md's
note on `CACHES` staying Redis-backed) and `events/handlers.py:clear_cache` does a blunt `django_cache.clear()`
on `ON_ARTICLE_PUBLISHED`.

## Events & per-journal pluggable functions

`WjsReviewConfig.ready()` (`apps.py:10-63`) registers/unregisters handlers on Janeway's `events.logic.Events`
bus (see root CLAUDE.md) and defines a custom event, `ReviewEvent.ON_ARTICLEWORKFLOW_SUBMITTED`
(`events/__init__.py:16-17`), raised manually from inside `sync_article_articleworkflow`.

The **per-journal pluggable-function pattern** (three settings dicts in `wjs/defaults/settings.py`:
`WJS_REVIEW_CHECK_FUNCTIONS`, `WJS_ARTICLE_ASSIGNMENT_FUNCTIONS`, `WJS_ARTICLE_EO_ASSIGNMENT_FUNCTIONS`,
`WJS_REVIEW_READY_FOR_TYP_CHECK_FUNCTIONS`) always resolves the same way: journal-code lookup with a
`None`-keyed fallback, then `django.utils.module_loading.import_string(fn_path)(article)` — functions are
referenced by dotted path in settings (not imported directly), which *is* the plugin's registry-style
indirection for this case, just settings-backed rather than a Python dict of callables. Call sites:
`events/handlers.py:dispatch_checks`, `events/assignment.py:dispatch_assignment`/`dispatch_eo_assignment`,
`logic__production.py:VerifyProductionRequirements._perform_checks`. When adding a new per-journal behavior,
add a function to the relevant `events/*.py` module and wire it into the journal-keyed settings dict — don't
branch on journal code inline (this is also the repo-wide "Multi-journal configuration pattern" from the root
CLAUDE.md, applied concretely here).

`apps.py.ready()` also monkeypatches ~18 methods (`filter_reviewers`, `annotate_is_author`,
`annotate_is_active_reviewer`, `annotate_worked_with_me`, `annotate_ordering_score`, ...) onto **both**
Janeway's core `AccountManager` and `AccountQuerySet` (deliberately both, per the comment at `apps.py:16-19`,
so the method works as `Account.objects.filter_reviewers()` and `Account.objects.all().filter_reviewers()`),
implemented in `users.py`.

## Messaging & Reminders

- **`Message`/`MessageRecipients`/`MessageThread`** (`models.py:1387-1708`): `Message` targets an `Article` or
  `Journal` via a `GenericForeignKey`; recipients are M2M through `MessageRecipients` (per-recipient `read`/
  `protected` flags); `related_messages` M2M-through-self via `MessageThread` (`FORWARD`/`REPLY`) handles
  threading. `verbosity` (`FULL`/`TIMELINE`/`EMAIL`) controls whether a message shows in the in-app timeline,
  goes out by email, or both. `emit_notification()` sends per-recipient email respecting `verbosity`/
  `message_type`/the `NO_NOTIFICATION` setting. The central creation helper used everywhere else in the plugin
  is `communication_utils.log_operation(...)`, not constructing `Message` by hand — use it for any new
  notification/audit-trail write.
- **`Reminder`** (`models.py:2129-2374`): generic-FK target (`ReviewAssignment`/`WjsEditorAssignment`/
  `Article`), `ReminderCodes` (fine-grained, numbered per occurrence, e.g.
  `REVIEWER_SHOULD_EVALUATE_ASSIGNMENT_1/2/3`) grouped into `ReminderClasses` families for escalation.
  Subject/body are rendered once at creation time (not re-rendered later, so template edits don't retroactively
  change already-created reminders). Actual due-date/template logic lives in `reminders/settings.py`'s
  `ReminderManager`, imported lazily inside `Reminder`'s methods to avoid circularity — the model itself only
  stores state and exposes thin wrappers (`update_due_date`, `create_message`).

## Managers / querysets

Per the repo's "no `.objects.filter()` shortcuts" rule, all three plugin-specific models expose their
filtering exclusively through custom querysets in `managers.py`:

| QuerySet (`managers.py`) | Model | Notable methods |
|---|---|---|
| `ArticleWorkflowQuerySet` | `ArticleWorkflow` | `get_article_with_latest_round(mode)`, `with_unread_messages(user, other_users_messages=False)`, `with_pending_reviews()`, `waiting_for_decision()`, `submitted_re()` |
| `WjsEditorAssignmentQuerySet` | `WjsEditorAssignment` | `get_current(article)`, `get_all(article)`, `get_final_reviews_in_timeframe(...)`, `get_pending_reviews_in_timeframe(...)` |
| `WorkflowReviewAssignmentQuerySet` | `WorkflowReviewAssignment` | `by_current_round`, `not_withdrawn`/`declined_or_withdrawn`/`not_declined_or_withdrawn`, `active` (= `not_declined_or_withdrawn`), `pending`, `completed`, `valid` (= `active().by_current_round(...)`) |

`Account`/`AccountQuerySet` filtering extensions (`filter_reviewers`, `annotate_*`) live in `users.py` and are
monkeypatched onto Janeway's core manager rather than defined in `managers.py` — same pattern, different
attachment point, because the target model belongs to Janeway core, not this plugin.

## Tests (`tests/`, plugin-specific conventions)

Beyond the repo-wide rules in `.claude/rules/tests.md`:

- **`conftest.py` builds a fixture chain mirroring `ReviewStates` progression**: `submitted_article` →
  `assigned_article` → `assigned_article_with_reviewer` → `accepted_article` → `rejected_article` →
  `under_appeal_article` → `appeal_submitted_article` → `ready_for_typesetter_article` →
  `assigned_to_typesetter_article` → `stage_proofing_article` → `rfp_article`. Each public `@pytest.fixture`
  has a matching private `_foo_article(...)` helper doing the actual state mutation, callable directly when a
  test needs custom parameters (e.g. to skip email side effects between setup steps). When a test needs an
  article in a given workflow state, reach for the existing fixture in this chain before hand-rolling setup.
- **Factory-fixture pattern**: several fixtures return a callable rather than a fixed object
  (`create_note`/`create_user_message`/`zip_with_tex_with_query`) — `def _foo(...): ...` +
  `@pytest.fixture def foo(): return _foo`, letting tests call the fixture with custom args.
  `wjs.jcom_profile.tests.conftest` is star-imported, so fixtures are layered across two apps.
- **`--run-academic` custom pytest option** (registered in this plugin's `conftest.py`) gates slow/expensive
  tests (`test_academic.py`, parts of `test_attentionconditions.py`/`test_crossref.py`) behind
  `@pytest.mark.skipif("not config.getoption('--run-academic')")` — pass `--run-academic` explicitly to include
  them; they're skipped by default.
- Test files map roughly one-per-module (`test_logic.py` ↔ `logic.py`, `test_production.py` ↔
  `logic__production.py`, `test_visibility.py` ↔ `logic__visibility.py`, `test_actions.py` ↔ `states.py`,
  `test_permissions.py` ↔ `permissions.py`, `test_managers.py` ↔ `managers.py`), as flat `def test_xxx(...)`
  functions, not one test class per production class.

## Known deviations — quick reference

A consolidated list of places this plugin's code doesn't match `.claude/rules/architecture-django.md` /
`code-style-django.md` to the letter. Documented so you don't "fix" them as drive-by cleanup without being
asked, and so you don't copy them into new code either:

| Area | Deviation | Where |
|---|---|---|
| Business logic | `ValueError` instead of `ValidationError` on most condition failures | Most of `logic.py`/`logic__production.py`; only a handful of spots use `ValidationError` |
| Business logic | Subclassing one logic class from another instead of composition | `BasePopulateRevisionStep`→`PopulateRevisionStep1/4/5/6/7`, `BaseConvertLatexReport`→its two subclasses |
| Business logic | `run()` with no `transaction.atomic()` | `PressNotificationHandler`, `ConvertManuscriptToPdf`, `BaseConvertLatexReport`, `AccessModeSpecialRequestNotification`, `AttachGalleys`, `TypesetterTestsGalleyGeneration` |
| Business logic | `select_for_update()` used in exactly one class | `HandleDecision` only |
| Views | Business logic run directly in a view, bypassing the form | `ArticleDecision.form_valid`, `TypesetterTakeInCharge.post()` |
| Views | Hand-rolled pagination instead of django-filter/queryset method | `SelectReviewerView` |
| Views | Domain query/filter logic living on the view, not a queryset | `ArticleDetails`, `EditUserPermissions` |
| Views | Missing `form_valid` `ValidationError` catch | `DeselectReviewer` |
| Forms | `save()` mutates models directly, no logic class | `MessageForm`, `UserPermissionsForm`, `SectionOrderForm` |
| Mixins | Two widely-used mixins live in `views.py`, not `mixins.py` | `BaseRelatedViewsMixin`, `ArticleWorkflowBaseMixin` |
| URLs | Underscore names/paths instead of the required hyphenated style | Almost all of `urls.py`; only a few recent entries use hyphens — prefer hyphens for anything new |
