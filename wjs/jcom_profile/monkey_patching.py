"""Alter Janeway's behaviour from the outside."""

from django import forms
# from core.views import edit_profile
from core.forms import EditAccountForm

# does not work...
setattr(EditAccountForm,
        'profession',
        forms.CharField(max_length=250))

# EditAccountForm.fields.setdefault(
#     'profession',
#     models.CharField(max_length=250, blank=True))
