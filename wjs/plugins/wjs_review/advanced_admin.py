from django.apps import apps
from django.contrib import admin
from django.http import HttpRequest
from wjs.advanced_admin.admin import advanced_admin_site

WjsSection = apps.get_model("wjs_review", "WjsSection")
EditorRevisionRequest = apps.get_model("wjs_review", "EditorRevisionRequest")


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
