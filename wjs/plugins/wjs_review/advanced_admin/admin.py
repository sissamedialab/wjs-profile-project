from django import forms
from django.apps import apps
from django.contrib import admin
from django.db.models import Q
from django.db.models.functions import Lower
from django.http import HttpRequest, HttpResponseRedirect
from django.shortcuts import render
from django.urls import path, reverse
from submission.models import Article, FrozenAuthor
from wjs.advanced_admin.admin import advanced_admin_site

from ..ac_service import evaluate_blacklisted_author
from .forms import WorkflowReviewAssignmentForm

WjsSection = apps.get_model("wjs_review", "WjsSection")
EditorRevisionRequest = apps.get_model("wjs_review", "EditorRevisionRequest")
EditorDecision = apps.get_model("wjs_review", "EditorDecision")
WorkflowReviewAssignment = apps.get_model("wjs_review", "WorkflowReviewAssignment")


@admin.register(WjsSection, site=advanced_admin_site)
class WjsSectionAdmin(admin.ModelAdmin):
    fields = ["doi_sectioncode", "pubid_and_tex_sectioncode", "description"]
    list_display = ["name", "journal"]
    list_filter = ["journal"]

    def has_add_permission(self, request):  # noqa: PLR6301
        """
        Prevents adding new sections through this interface.

        New sections must be created in the standard Django admin first,
        then their WjsSection parameters can be edited here.
        """
        return False


@admin.register(EditorRevisionRequest, site=advanced_admin_site)
class EditorRevisionRequestAdmin(admin.ModelAdmin):
    readonly_fields = ("author_note",)
    fields = (
        "cover_letter_file",
        "author_note",
    )
    autocomplete_fields = ("cover_letter_file",)

    list_display = ["article_title", "pubid", "article_journal", "state"]
    list_filter = ["article__journal", "article__articleworkflow__state"]
    ordering = ("-id",)
    search_fields = ("article__identifier__identifier", "editor__email")

    def render_change_form(self, request, context, *args, **kwargs):
        context["title"] = "Change authors cover letter"
        return super().render_change_form(request, context, *args, **kwargs)

    def changelist_view(self, request, extra_context=None):
        extra_context = extra_context or {}
        extra_context["title"] = "Select authors cover letter to change"
        return super().changelist_view(request, extra_context=extra_context)

    def has_add_permission(self, request: HttpRequest) -> bool:  # noqa: PLR6301
        """
        Determine if the user has permission to add an object.

        Current implementation blocks all users from adding new EditorRevisionRequest.

        :param request: The HTTP request object containing user information and metadata
        :type request: HttpRequest
        :return: False indicating that the user does not have permission to add
        :rtype: bool
        """
        return False

    def state(self, obj: EditorRevisionRequest) -> str:  # noqa: PLR6301
        """
        Retrieve the display name of the current state of the object's article workflow.

        :param obj: The object whose article workflow state display name is retrieved
        :type obj: EditorRevisionRequest
        :return: The display name of the current state of the object's article workflow
        :rtype: str
        """
        return obj.article.articleworkflow.get_state_display()

    def article_title(self, obj: EditorRevisionRequest) -> str:  # noqa: PLR6301
        """
        Retrieve the title of the object's article.

        :param obj: The object whose article title is retrieved
        :type obj: EditorRevisionRequest
        :return: The article title of the object's article
        :rtype: str
        """
        return obj.article.title

    def article_journal(self, obj: EditorRevisionRequest) -> str:  # noqa: PLR6301
        """
        Retrieve the journal of the object's article.

        :param obj: The object whose workflow state display name is retrieved
        :type obj: EditorRevisionRequest
        :return: The journal of the object's article
        :rtype: str
        """
        return obj.article.journal

    def pubid(self, obj: EditorRevisionRequest) -> str:  # noqa: PLR6301
        """
        Retrieve the pubid of the EditorRevisionRequest.article.

        :param obj: The object whose article pubid is retrieved
        :type obj: EditorRevisionRequest
        :return: The pubid of the article
        :rtype: str
        """
        return obj.article.get_pubid()


@admin.register(WorkflowReviewAssignment, site=advanced_admin_site)
class WorkflowReviewAssignmentAdmin(admin.ModelAdmin):
    form = WorkflowReviewAssignmentForm
    search_fields = (
        "id",
        "article__id",
        "article__identifier__identifier",
    )
    readonly_fields = ("tex_report_pdf", "review_file_display")
    fields = (
        "tex_report_pdf",
        "review_file_display",
        "reviewer_report",
    )
    list_display = [
        "report_id",
        "article_id",
        "article_title",
        "version_number",
        "pubid",
        "reviewer",
        "editor",
    ]
    list_filter = [
        "article__journal",
    ]
    ordering = ("-pk",)

    def version_number(self, obj):
        return obj.version[0].number if obj.version else "-"

    def report_id(self, obj):
        return obj.pk

    @admin.display(description="Attachment")
    def review_file_display(self, obj):
        return obj.review_file

    def render_change_form(self, request, context, *args, **kwargs):
        context["title"] = "Change reviewer report"
        return super().render_change_form(request, context, *args, **kwargs)

    def changelist_view(self, request, extra_context=None):
        extra_context = extra_context or {}
        extra_context["title"] = "Select reviewer report to change"
        return super().changelist_view(request, extra_context=extra_context)

    def pubid(self, obj: WorkflowReviewAssignment) -> str:  # noqa: PLR6301
        """
        Retrieve the pubid of the WorkflowReviewAssignment.article.

        :param obj: The object whose article pubid is retrieved
        :type obj: WorkflowReviewAssignment
        :return: The pubid of the article
        :rtype: str
        """
        return obj.article.get_pubid()

    def has_add_permission(self, request: HttpRequest) -> bool:  # noqa: PLR6301
        """
        Determine if the user has permission to add an object.

        Current implementation blocks all users from adding new WorkflowReviewAssignment.

        :param request: The HTTP request object containing user information and metadata
        :type request: HttpRequest
        :return: False indicating that the user does not have permission to add
        :rtype: bool
        """
        return False

    def article_journal(self, obj: WorkflowReviewAssignment) -> str:  # noqa: PLR6301
        """
        Retrieve the journal of the object's article.

        :param obj: The object whose workflow state display name is retrieved
        :type obj: WorkflowReviewAssignment
        :return: The journal of the object's article
        :rtype: str
        """
        return obj.article.journal

    def article_title(self, obj: WorkflowReviewAssignment) -> str:  # noqa: PLR6301
        """
        Retrieve the title of the object's article.

        :param obj: The object whose article title is retrieved
        :type obj: WorkflowReviewAssignment
        :return: The article title of the object's article
        :rtype: str
        """
        return obj.article.title


@admin.register(EditorDecision, site=advanced_admin_site)
class EditorDecisionAdmin(admin.ModelAdmin):
    search_fields = (
        "workflow__article__id",
        "workflow__article__identifier__identifier",
    )
    readonly_fields = ("decision_editor_report_pdf",)
    fields = (
        "decision_editor_report",
        "decision_editor_report_pdf",
    )
    list_display = [
        "article_title",
        "pubid",
        "decision",
        "editor",
    ]
    list_filter = [
        "workflow__article__journal",
    ]
    ordering = ("-id",)

    def render_change_form(self, request, context, *args, **kwargs):
        context["title"] = "Change editor report"
        return super().render_change_form(request, context, *args, **kwargs)

    def changelist_view(self, request, extra_context=None):
        extra_context = extra_context or {}
        extra_context["title"] = "Select editor report to change"
        return super().changelist_view(request, extra_context=extra_context)

    def pubid(self, obj: EditorDecision) -> str:  # noqa: PLR6301
        """
        Retrieve the pubid of the EditorDecision.workflow.article.

        :param obj: The object whose workflow article pubid is retrieved
        :type obj: EditorDecision
        :return: The pubid of the workflow article
        :rtype: str
        """
        return obj.workflow.article.get_pubid()

    def has_add_permission(self, request: HttpRequest) -> bool:  # noqa: PLR6301
        """
        Determine if the user has permission to add an object.

        Current implementation blocks all users from adding new EditorDecision.

        :param request: The HTTP request object containing user information and metadata
        :type request: HttpRequest
        :return: False indicating that the user does not have permission to add
        :rtype: bool
        """
        return False

    def article_title(self, obj: EditorDecision) -> str:  # noqa: PLR6301
        """
        Retrieve the title of the object's workflow article.

        :param obj: The object whose workflow article title is retrieved
        :type obj: EditorDecision
        :return: The article title of the object's workflow article
        :rtype: str
        """
        return obj.workflow.article.title

    def decision(self, obj: EditorDecision) -> str:
        """
        Retrieve the title of the object's decision.

        :param obj: The object whose decision display is retrieved
        :type obj: EditorDecision
        :return: The decision representation of the object
        :rtype: str
        """
        return obj.get_decision_display()


# -- Blacklisted author emails --

BlacklistedAuthorEmail = apps.get_model("wjs_review", "BlacklistedAuthorEmail")


class BlacklistedEmailBulkForm(forms.Form):
    """Form for bulk-adding blacklisted emails via a textarea."""

    emails = forms.CharField(
        widget=forms.Textarea(attrs={"rows": 20, "cols": 80}),
        help_text=(
            "One email per line. Optionally append a note after a comma, "
            'e.g. "bad.author@example.com, Reason for blacklist".'
        ),
        required=True,
    )


@admin.register(BlacklistedAuthorEmail, site=advanced_admin_site)
class BlacklistedAuthorEmailAdmin(admin.ModelAdmin):
    """Admin interface for managing blacklisted author emails.

    Supports individual CRUD and a custom admin action for bulk loading
    a list of emails (one per line, optionally with a note after a comma).
    """

    list_display = ["email", "note", "created_at"]
    search_fields = ["email", "note"]
    list_filter = ["created_at"]
    ordering = ("email",)
    change_list_template = "admin/wjs_review/blacklisted_authoremail/change_list.html"

    def get_urls(self):
        """Add a custom URL for the bulk-add view."""
        urls = super().get_urls()
        custom_urls = [
            path(
                "bulk-add/",
                self.admin_site.admin_view(self.bulk_add_view),
                name="blacklisted_authoremail_bulk_add",
            ),
        ]
        return custom_urls + urls

    @staticmethod
    def _reevaluate_articles_for_emails(emails: list[str]) -> None:
        """Re-evaluate the blacklisted-author AC for articles matching the given emails.

        Finds all articles that have a FrozenAuthor whose email (frozen or
        linked account) matches any of the given emails, then calls
        :func:`ac_service.evaluate_blacklisted_author` for each.

        This ensures that when the EO adds or removes a blacklisted email via
        the admin, the attention conditions on existing articles are updated
        immediately—not only at the next nightly rebuild.

        See issue #2980.
        """
        emails_lower = [e.lower() for e in emails if e]
        if not emails_lower:
            return
        article_ids = (
            FrozenAuthor.objects.annotate(
                lower_frozen_email=Lower("frozen_email"),
                lower_author_email=Lower("author__email"),
            )
            .filter(Q(lower_frozen_email__in=emails_lower) | Q(lower_author_email__in=emails_lower))
            .values_list("article_id", flat=True)
            .distinct()
        )
        for article in Article.objects.filter(id__in=article_ids):
            evaluate_blacklisted_author(article)

    def save_model(self, request, obj, form, change):
        """After saving a BlacklistedAuthorEmail, re-evaluate affected articles."""
        super().save_model(request, obj, form, change)
        self._reevaluate_articles_for_emails([obj.email])

    def delete_model(self, request, obj):
        """After deleting a BlacklistedAuthorEmail, re-evaluate affected articles.

        The re-evaluation will resolve the AC on articles that no longer have
        a matching blacklisted email.
        """
        email = obj.email
        super().delete_model(request, obj)
        self._reevaluate_articles_for_emails([email])

    def delete_queryset(self, request, queryset):
        """Bulk delete: re-evaluate affected articles after deletion."""
        emails = list(queryset.values_list("email", flat=True))
        super().delete_queryset(request, queryset)
        self._reevaluate_articles_for_emails(emails)

    def bulk_add_view(self, request: HttpRequest):
        """Custom view for bulk-adding emails via a textarea."""
        if request.method == "POST":
            form = BlacklistedEmailBulkForm(request.POST)
            if form.is_valid():
                emails_text = form.cleaned_data["emails"]
                added = 0
                skipped = 0
                processed_emails: list[str] = []
                for line in emails_text.splitlines():
                    line = line.strip()
                    if not line:
                        continue
                    # Split email and optional note on the first comma
                    if "," in line:
                        email, note = line.split(",", 1)
                        email = email.strip().lower()
                        note = note.strip()
                    else:
                        email = line.lower()
                        note = ""
                    obj, created = BlacklistedAuthorEmail.objects.get_or_create(
                        email=email,
                        defaults={"note": note},
                    )
                    if created:
                        added += 1
                    else:
                        skipped += 1
                    processed_emails.append(email)
                # Re-evaluate affected articles immediately.
                self._reevaluate_articles_for_emails(processed_emails)
                self.message_user(
                    request,
                    f"Bulk import complete: {added} added, {skipped} already existed.",
                )
                return HttpResponseRedirect(reverse("admin:blacklisted_authoremail_changelist"))
        else:
            form = BlacklistedEmailBulkForm()

        context = {
            **self.admin_site.each_context(request),
            "title": "Bulk add blacklisted author emails",
            "form": form,
            "opts": self.model._meta,
            "has_change_permission": True,
            "has_view_permission": True,
        }
        return render(request, "admin/wjs_review/blacklisted_authoremail/bulk_add.html", context)

    def changelist_view(self, request, extra_context=None):
        extra_context = extra_context or {}
        extra_context["title"] = "Blacklisted author emails"
        return super().changelist_view(request, extra_context=extra_context)
