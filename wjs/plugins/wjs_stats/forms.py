from django import forms


class DummyManagerForm(forms.Form):
    dummy_field = forms.CharField()
