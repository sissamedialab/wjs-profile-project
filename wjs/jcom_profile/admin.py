"""Register the models with the admin interface."""
from core.admin import AccountAdmin
from core.models import Account
from django.conf import settings
from django.contrib import admin, messages
from django.core.mail import send_mail
from django.http import HttpResponseRedirect
from django.shortcuts import render
from django.urls import re_path, reverse
from journal.admin import IssueAdmin
from journal.models import Issue
from modeltranslation.admin import TranslationAdmin
from submission.admin import KeywordAdmin
from submission.models import Keyword

from wjs.jcom_profile import forms, models
from wjs.jcom_profile.models import (
    Correspondence,
    EditorAssignmentParameters,
    EditorKeyword,
    JCOMProfile,
    Recipient,
    SpecialIssue,
)
from wjs.jcom_profile.utils import generate_token


class JCOMProfileInline(admin.StackedInline):
    """Helper class to "inline" account profession."""

    model = JCOMProfile
    fields = ["profession", "gdpr_checkbox", "invitation_token"]
    # TODO: No! this repeats all the fields (first name, password,...)


# TODO: use settings.AUTH_USER_MODEL
class UserAdmin(AccountAdmin):
    """Another layer..."""

    inlines = (JCOMProfileInline,)

    def get_urls(self):
        """Get admin urls."""
        urls = super().get_urls()
        import_users_url = [
            re_path(
                "invite/",
                self.admin_site.admin_view(self.invite),
                name="invite",
            ),
        ]
        return import_users_url + urls

    def invite(self, request):
        """Invite external users from admin Account interface.

        The user is created as inactive and his/her account is marked
        without GDPR explicitly accepted, Invited user base
        information are encoded to generate a token to be appended to
        the url for GDPR acceptance.

        """
        if request.method == "POST":
            form = forms.InviteUserForm(request.POST)
            if form.is_valid():
                email = form.data["email"]
                if not JCOMProfile.objects.filter(email=email):
                    if request.journal:
                        # generate token from email (which is unique)
                        token = generate_token(email, request.journal.code)
                        # create inactive account with minimal data
                        models.JCOMProfile.objects.create(
                            email=email,
                            first_name=form.data["first_name"],
                            last_name=form.data["last_name"],
                            department=form.data["department"],
                            institution=form.data["institution"],
                            invitation_token=token,
                            is_active=False,
                        )
                        # Send email to user allowing him/her to accept
                        # GDPR policy explicitly
                        #
                        # FIXME: Email setting should be handled using the
                        # janeway settings framework.  See
                        # https://gitlab.sissamedialab.it/wjs/wjs-profile-project/-/issues/4
                        acceptance_url = request.build_absolute_uri(reverse("accept_gdpr", kwargs={"token": token}))
                        send_mail(
                            settings.JOIN_JOURNAL_SUBJECT,
                            settings.JOIN_JOURNAL_BODY.format(
                                form.data["first_name"],
                                form.data["last_name"],
                                form.data["message"],
                                acceptance_url,
                            ),
                            settings.DEFAULT_FROM_EMAIL,
                            [email],
                        )
                        messages.success(
                            request=request,
                            message="Account created",
                        )
                    else:
                        messages.warning(
                            request=request,
                            message="Journal not set.",
                        )
                else:
                    messages.warning(
                        request=request,
                        message="An account with the specified email already exists.",
                    )
                return HttpResponseRedirect(reverse("admin:core_account_changelist"))

        template = "admin/core/account/invite.html"
        context = {
            "form": forms.InviteUserForm(),
        }
        return render(request, template, context)


admin.site.unregister(Account)
admin.site.register(Account, UserAdmin)


@admin.register(Correspondence)
class CorrespondenceAdmin(admin.ModelAdmin):
    """Helper class to "admin" correspondence."""

    list_filter = ("source",)


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


class KeywordTranslationAdmin(KeywordAdmin, TranslationAdmin):
    """Keyword translations."""


admin.site.unregister(Keyword)
admin.site.register(Keyword, KeywordTranslationAdmin)


class IssueTranslationAdmin(IssueAdmin, TranslationAdmin):
    """Issue translations."""


admin.site.unregister(Issue)
admin.site.register(Issue, IssueTranslationAdmin)
