"""My views. Looking for a way to "enrich" Janeway's `edit_profile`."""
import base64
import json

from django.urls import reverse
from django.conf import settings
from django.contrib import messages
from django.db import IntegrityError
from django.shortcuts import render, redirect
from django.shortcuts import get_object_or_404
from django.core.validators import validate_email
from django.core.exceptions import ValidationError
from django.contrib.auth.decorators import login_required
from django.core.mail import send_mail

from core import logic
from core import models as core_models

from wjs.jcom_profile import forms
from wjs.jcom_profile.forms import JCOMProfileForm, JCOMRegistrationForm
from wjs.jcom_profile.models import JCOMProfile


@login_required
def prova(request):
    """Una prova."""
    user = JCOMProfile.objects.get(pk=request.user.id)
    form = JCOMProfileForm(instance=user)
    # import ipdb; ipdb.set_trace()

    # from core.views.py::edit_profile:358ss
    if request.POST:
        if 'email' in request.POST:
            email_address = request.POST.get('email_address')
            try:
                validate_email(email_address)
                try:
                    logic.handle_email_change(request, email_address)
                    return redirect(reverse('website_index'))
                except IntegrityError:
                    messages.add_message(
                        request,
                        messages.WARNING,
                        'An account with that email address already exists.',
                    )
            except ValidationError:
                messages.add_message(
                    request,
                    messages.WARNING,
                    'Email address is not valid.',
                )

        elif 'change_password' in request.POST:
            old_password = request.POST.get('current_password')
            new_pass_one = request.POST.get('new_password_one')
            new_pass_two = request.POST.get('new_password_two')

            if old_password and request.user.check_password(old_password):

                if new_pass_one == new_pass_two:
                    problems = request.user.password_policy_check(request,
                                                                  new_pass_one)
                    if not problems:
                        request.user.set_password(new_pass_one)
                        request.user.save()
                        messages.add_message(request, messages.SUCCESS,
                                             'Password updated.')
                    else:
                        [messages.add_message(
                            request,
                            messages.INFO,
                            problem) for problem in problems]
                else:
                    messages.add_message(request, messages.WARNING,
                                         'Passwords do not match')

            else:
                messages.add_message(request, messages.WARNING,
                                     'Old password is not correct.')

        elif 'edit_profile' in request.POST:
            form = JCOMProfileForm(request.POST, request.FILES, instance=user)

            if form.is_valid():
                form.save()
                messages.add_message(request, messages.SUCCESS,
                                     'Profile updated.')
                return redirect(reverse('core_edit_profile'))

        elif 'export' in request.POST:
            return logic.export_gdpr_user_profile(user)

    context = dict(form=form, user_to_edit=user)
    template = 'core/accounts/edit_profile.html'
    return render(request, template, context)


# from src/core/views.py::register
def register(request):
    """
    Display a form for users to register with the journal.

    If the user is registering on a journal we give them
    the Author role.
    :param request: HttpRequest object
    :return: HttpResponse object
    """
    token, token_obj = request.GET.get('token', None), None
    if token:
        token_obj = get_object_or_404(core_models.OrcidToken, token=token)

    form = JCOMRegistrationForm()

    if request.POST:
        form = JCOMRegistrationForm(request.POST)

        password_policy_check = logic.password_policy_check(request)

        if password_policy_check:
            for policy_fail in password_policy_check:
                form.add_error('password_1', policy_fail)

        if form.is_valid():
            if token_obj:
                new_user = form.save(commit=False)
                new_user.orcid = token_obj.orcid
                new_user.save()
                token_obj.delete()
            else:
                new_user = form.save()

            if request.journal:
                new_user.add_account_role('author', request.journal)
            logic.send_confirmation_link(request, new_user)

            messages.add_message(
                request,
                messages.SUCCESS,
                'Your account has been created, please follow the'
                'instructions in the email that has been sent to you.')
            return redirect(reverse('core_login'))

    template = 'core/accounts/register.html'
    context = {
        'form': form,
    }

    return render(request, template, context)


def confirm_gdpr_acceptance(request, token):
    """
    The view for explicit GDPR acceptance for invited users.
    The token encodes base user information (name, surname and email)
    """
    template = "admin/core/account/gdpr_acceptance.html"

    # verify the account existence
    try:
        account = JCOMProfile.objects.get(invitation_token=token)
    except JCOMProfile.DoesNotExist:
        context = {"error": True}
        return render(request, template, context, status=404)

    context = {
        "first_name": account.first_name,
        "last_name": account.last_name,
        "form": forms.GDPRAcceptanceForm(),
    }
    if request.POST:
        form = forms.GDPRAcceptanceForm(request.POST)
        if form.is_valid():
            template = "admin/core/account/thankyou.html"
            # if the form is valid and the existing account does not have the GDPR policy accepted, it is updated
            if not account.gdpr_checkbox:
                account.is_active = True
                account.gdpr_checkbox = True
                account.invitation_token = ""
                account.save()
                context["activated"] = True
                # Generate a temporary token to set a brand-new password
                core_models.PasswordResetToken.objects.filter(account=account).update(expired=True)
                reset_token = core_models.PasswordResetToken.objects.create(account=account)
                reset_psw_url = request.build_absolute_uri(
                    reverse("core_reset_password", kwargs={"token": reset_token.token}))
                # Send email.
                # FIXME: Email setting should be handled using the janeway settings framework.
                # See https://gitlab.sissamedialab.it/wjs/wjs-profile-project/-/issues/4
                send_mail(
                    settings.RESET_PASSWORD_SUBJECT,
                    settings.RESET_PASSWORD_BODY.format(account.first_name, account.last_name, reset_psw_url),
                    settings.DEFAULT_FROM_EMAIL,
                    [account.email]
                )
        else:
            context["form"] = form

    return render(request, template, context)
