"""My views. Looking for a way to "enrich" Janeway's `edit_profile`."""

from django.urls import reverse
from django.contrib import messages
from django.db import IntegrityError
from django.shortcuts import render, redirect
from django.core.validators import validate_email
from django.core.exceptions import ValidationError
from django.contrib.auth.decorators import login_required

from core import logic

from wjs.jcom_profile.forms import JCOMProfileForm
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
