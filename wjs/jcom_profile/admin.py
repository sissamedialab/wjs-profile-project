"""Register the models with the admin interface."""
import base64
import hashlib
import json

from django.contrib import admin, messages

from wjs.jcom_profile import forms, models
from wjs.jcom_profile.models import JCOMProfile
# from django.contrib.admin.sites import NotRegistered
from django.conf import settings
from core.models import Account
from core.admin import AccountAdmin

from django.conf.urls import url
from django.core.mail import send_mail
from django.shortcuts import render
from django.http import HttpResponseRedirect
from django.urls import reverse

from wjs.jcom_profile.utils import generate_token


class JCOMProfileInline(admin.StackedInline):
    """Helper class to "inline" account profession."""

    model = JCOMProfile
    fields = ['profession', 'gdpr_checkbox', 'invitation_token']
    # TODO: No! this repeats all the fields (first name, password,...)


# TODO: use settings.AUTH_USER_MODEL
# from django.conf import settings
class UserAdmin(AccountAdmin):
    """Another layer..."""

    inlines = (JCOMProfileInline,)

    def get_urls(self):
        urls = super().get_urls()
        import_users_url = [
            url("invite/", self.admin_site.admin_view(self.invite), name="invite")
        ]
        return import_users_url + urls

    def invite(self, request):
        """
        Invite external users from admin Account interface.
        The user is created as inactive and his/her account is marked without GDPR explicitly accepted,
        Invited user base information are encoded to generate a token to be appended to the url for GDPR acceptance.
        """
        if request.method == 'POST':
            form = forms.InviteUserForm(request.POST)
            if form.is_valid():
                email = form.data["email"]
                if not JCOMProfile.objects.filter(email=email):
                    # generate token from email (which is unique)
                    token = generate_token(email)
                    # create inactive account with minimal data
                    models.JCOMProfile.objects.create(
                        email=email,
                        first_name=form.data["first_name"],
                        last_name=form.data["last_name"],
                        department=form.data["department"],
                        institution=form.data["institution"],
                        invitation_token=token,
                        is_active=False
                    )
                    # Send email to user allowing him/her to accept GDPR policy explicitly
                    # FIXME: Email setting should be handled using the janeway settings framework.
                    #  See https://gitlab.sissamedialab.it/wjs/wjs-profile-project/-/issues/4
                    acceptance_url = request.build_absolute_uri(reverse("accept_gdpr", kwargs={"token": token}))
                    send_mail(
                        settings.JOIN_JOURNAL_SUBJECT,
                        settings.JOIN_JOURNAL_BODY.format(form.data["first_name"], form.data["last_name"],
                                                          form.data['message'], acceptance_url),
                        settings.DEFAULT_FROM_EMAIL,
                        [email]
                    )
                    messages.success(
                        request=request,
                        message='Account created',
                    )
                else:
                    messages.warning(
                        request=request,
                        message='An account with the specified email already exists.'
                    )
                return HttpResponseRedirect(reverse("admin:core_account_changelist"))

        template = "admin/core/account/invite.html"
        context = {
            "form": forms.InviteUserForm(),
        }
        return render(request, template, context)


admin.site.unregister(Account)
admin.site.register(Account, UserAdmin)
