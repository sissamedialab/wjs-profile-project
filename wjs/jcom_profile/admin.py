"""Register the models with the admin interface."""
from core.admin import AccountAdmin
from core.models import Account
from django.contrib import admin
from journal.admin import IssueAdmin
from journal.models import Issue
from modeltranslation.admin import TranslationAdmin
from submission.admin import KeywordAdmin
from submission.models import Keyword

from wjs.jcom_profile.models import (
    Correspondence,
    EditorAssignmentParameters,
    EditorKeyword,
    JCOMProfile,
    Recipient,
    SpecialIssue,
)

admin.site.unregister(Account)
admin.site.unregister(Issue)
admin.site.unregister(Keyword)


class JCOMProfileInline(admin.StackedInline):
    """Helper class to "inline" account profession."""

    model = JCOMProfile
    fields = ["profession", "gdpr_checkbox", "invitation_token"]
    # TODO: No! this repeats all the fields (first name, password,...)


@admin.register(Account)
class UserAdmin(AccountAdmin):
    """Another layer..."""

    inlines = (JCOMProfileInline,)


@admin.register(Correspondence)
class CorrespondenceAdmin(admin.ModelAdmin):
    """Helper class to "admin" correspondence."""

    list_filter = ("source",)
    search_fields = ["account__last_name", "email", "account__email"]


@admin.register(SpecialIssue)
class SpecialIssueAdmin(admin.ModelAdmin):
    """Helper class to "admin" special issues."""


@admin.register(EditorAssignmentParameters)
class EditorAssignmentParametersAdmin(admin.ModelAdmin):
    """Helper class to "admin" editor assignment parameters."""


@admin.register(EditorKeyword)
class EditorKeywordAdmin(admin.ModelAdmin):
    """Helper class to "admin" editor keyword."""


@admin.register(Recipient)
class RecipientAdmin(admin.ModelAdmin):
    """Helper class to "admin" recipient."""

    list_filter = ["journal"]
    search_fields = ["email", "user__email"]


@admin.register(Keyword)
class KeywordTranslationAdmin(KeywordAdmin, TranslationAdmin):
    """Keyword translations."""

    list_filter = ["journal"]
    list_display = ["word", "id"]


@admin.register(Issue)
class IssueTranslationAdmin(IssueAdmin, TranslationAdmin):
    """Issue translations."""
