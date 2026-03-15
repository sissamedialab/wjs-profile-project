from django import forms
from django.conf import settings
from django.utils.translation import gettext_lazy as _
from utils import logic as utils_logic
from utils.forms import CaptchaForm

from wjs.jcom_profile.forms import logger
from wjs.jcom_profile.models import Recipient
from wjs.jcom_profile.settings_helpers import get_journal_language_choices


class RegisterUserNewsletterForm(CaptchaForm):
    """Register an Anonymous user to a newsletter."""

    email = forms.EmailField(required=True, widget=forms.EmailInput(attrs={"placeholder": _("Your email address")}))


class NewsletterTopicForm(forms.ModelForm):
    topics = forms.ModelMultipleChoiceField(
        label="",
        queryset=None,
        widget=forms.CheckboxSelectMultiple,
        required=False,
    )
    news = forms.BooleanField(required=False, label=_("I want to receive alerts about news published in the journal."))
    language = forms.ChoiceField(
        required=True,
        label=_("Preferred language for alerts"),
        choices=settings.LANGUAGES,
    )

    class Meta:
        model = Recipient
        fields = (
            "topics",
            "news",
            "language",
        )

    def __init__(self, *args, **kwargs):
        """Prepare the queryset for topics."""
        self.base_fields["topics"].queryset = kwargs.get("instance").journal.keywords.all().order_by("word")

        # Manage the language field's choices
        request = utils_logic.get_current_request()
        available_languages = []
        if request and request.journal:
            available_languages = get_journal_language_choices(request.journal)

        super().__init__(*args, **kwargs)

        if len(available_languages) > 1:
            self.fields["language"].choices = available_languages
        else:
            # Let's hide the language select if there is only one choice
            del self.fields["language"]

    def clean(self):
        """
        Log a warning if the user choose no topics and no news.

        We do _not_ raise a Validation error untill specs#474 is done.
        """
        cleaned_data = super().clean()

        topics = cleaned_data.get("topics")
        news = cleaned_data.get("news")
        if len(topics) == 0 and news is False:
            logger.warning(f"Recipient {self.instance.email}/{self.instance.user} selected no topics and no news.")
            # after #474 # raise ValidationError(
            # after #474 #     _('You have selected no news and no topics.
            # after #474 #        Please either choose something or click "Unsubscribe".'),
            # after #474 # )

        return cleaned_data
