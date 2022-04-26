"""My views. Looking for a way to "enrich" Janeway's `edit_profile`."""
from django.shortcuts import render
from wjs_profession.forms import JCOMProfileForm
from wjs_profession.forms import JCOMProfileFormDerived
from django.contrib.auth.decorators import login_required
from core.forms import EditAccountForm


@login_required
def prova(request):
    """Una prova."""
    user = request.user
    form = JCOMProfileForm(instance=user)
    context = dict(form=form, user_to_edit=user)
    return render(request, 'aaa.html', context)
