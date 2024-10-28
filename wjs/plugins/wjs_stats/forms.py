from datetime import timedelta

from django import forms
from django.utils.timezone import now
from easy_select2 import Select2Multiple


class DummyManagerForm(forms.Form):
    dummy_field = forms.CharField()


class FilterForm(forms.Form):
    """Filter stats result by period and issue."""

    from_date = forms.DateField(
        label="From Date",
        widget=forms.DateInput(attrs={"type": "date"}),
        initial=now().date() - timedelta(days=30),
    )
    to_date = forms.DateField(
        label="To Date",
        widget=forms.DateInput(attrs={"type": "date"}),
        initial=now().date(),
    )
    issues = forms.ModelMultipleChoiceField(
        queryset=None,
        widget=Select2Multiple,
        label="Select Issues",
        required=False,
    )
