"""My views. Looking for a way to "enrich" Janeway's `edit_profile`."""
from django.shortcuts import render
from wjs.jcom_profile.forms import JCOMProfileForm
from wjs.jcom_profile.models import JCOMProfile
from django.contrib.auth.decorators import login_required


@login_required
def prova(request):
    """Una prova."""
    user = JCOMProfile.objects.get(pk=request.user.id)
    form = JCOMProfileForm(instance=user)
    # import ipdb; ipdb.set_trace()
    context = dict(form=form, user_to_edit=user)
    return render(request, 'aaa.html', context)
