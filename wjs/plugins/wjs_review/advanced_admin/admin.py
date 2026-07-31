from django.apps import apps
from django.contrib import admin
from django.http import HttpRequest
from wjs.advanced_admin.admin import advanced_admin_site

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
        "pk",
        "article__identifier__identifier",
    )
    readonly_fields = ("tex_report_pdf", "review_file_display")
    fields = (
        "tex_report_pdf",
        "review_file_display",
        "reviewer_report",
    )
    list_display = [
        "pk",
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
    search_fields = ("workflow__article__identifier__identifier",)
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
