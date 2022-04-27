"""Forms for the additional fields in this profile extension."""

from django.forms import ModelForm
from wjs.jcom_profile.models import JCOMProfile


class JCOMProfileForm(ModelForm):
    """Additional fields of the JCOM profile."""

    class Meta:
        model = JCOMProfile
        fields = '__all__'
