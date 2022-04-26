"""Forms for the additional fields in this profile extension."""

from django.forms import ModelForm
# from wjs_profession.models import AccountProfession
from wjs_profession.models import JCOMProfile
from core.forms import EditAccountForm


# class JCOMProfileForm(EditAccountForm):
#     """Additional fields of the JCOM profile."""

#     class Meta:
#         model = AccountProfession
#         fields = '__all__'


class JCOMProfileForm(ModelForm):
    """Additional fields of the JCOM profile."""

    class Meta:
        model = JCOMProfile
        fields = '__all__'
