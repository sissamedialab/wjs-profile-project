"""My views. Looking for a way to "enrich" Janeway's `edit_profile`."""
from django.shortcuts import render
from wjs.jcom_profile.forms import JCOMProfileForm
from django.contrib.auth.decorators import login_required


@login_required
def prova(request):
    """Una prova."""
    user = request.user
    form = JCOMProfileForm(instance=user)
    context = dict(form=form, user_to_edit=user)
    return render(request, 'aaa.html', context)
