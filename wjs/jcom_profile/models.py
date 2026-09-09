"""The model for a field "profession" for JCOM authors."""

from typing import Optional

from core.model_utils import MiniHTMLFormField
from core.models import Account, AccountManager
from django.conf import settings
from django.core.cache import cache
from django.core.exceptions import ValidationError
from django.core.serializers.json import DjangoJSONEncoder
from django.db import models
from django.db.models import JSONField
from django.forms import TextInput
from django.utils import timezone
from django.utils.timezone import now
from django.utils.translation import gettext as _
from django_bleach.forms import BleachField as BleachFormField
from journal.models import Issue, Journal
from tinymce.widgets import TinyMCE

from .constants import CAREER_STAGES, GENDER_CHOICES, PROFESSIONS
from .managers import StaffWorkloadParametersQuerySet

# TODO: use settings.AUTH_USER_MODEL


class WjsSimpleBleach(BleachFormField):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.widget = TextInput()
        self.bleach_options["tags"] = []
        self.bleach_options["attributes"] = {}
        self.bleach_options["strip_comments"] = True  # Remove also html comments


class JCOMProfile(Account):
    """An enrichment of Janeway's Account."""

    objects = AccountManager()
    # The following is redundant.
    # If not explicitly given, django creates a OTOField
    # named account_id_ptr.
    # But then I'm not sure how I should link the two:
    # see signals.py
    janeway_account = models.OneToOneField(Account, on_delete=models.CASCADE, primary_key=True, parent_link=True)
    # Even if EO wants "profession" to be mandatory, we cannot set it
    # to `null=False` (i.e. `NOT NULL` at DB level) because we do not
    # have this data for most of our existing users.
    profession = models.IntegerField(null=True, choices=PROFESSIONS, blank=True)
    career_stage = models.IntegerField(null=True, choices=CAREER_STAGES, blank=True)
    gdpr_checkbox = models.BooleanField(_("GDPR acceptance checkbox"), default=False)
    gdpr_acceptance = models.DateTimeField(_("GDPR acceptance date"), null=True, blank=True)
    invitation_token = models.CharField(_("Invitation token"), max_length=500, default="", blank=True)
    keywords = models.ManyToManyField("submission.Keyword", verbose_name=_("Interests"), blank=True)
    usernotes = models.TextField(_("User notes"), blank=True, default="")
    records_scix = models.URLField(_("My Records on SciX"), max_length=500, default="", blank=True)
    records_inspire = models.URLField(_("My Records on Inspire"), max_length=500, default="", blank=True)
    records_arxiv = models.URLField(_("My Records on ArXiv"), max_length=500, default="", blank=True)
    records_other = models.URLField(
        _("Others (personal website, records on Google Scholar/ResearchGate, etc)"),
        max_length=500,
        default="",
        blank=True,
    )
    gender = models.CharField(_("Gender"), choices=GENDER_CHOICES, default="", blank=True)
    year_of_birth = models.IntegerField(_("Year of birth"), null=True, blank=True)
    alternative_email = models.EmailField(_("Alternative email address"), null=True, blank=True)

    def save(self, *args, **kwargs):
        # is_admin is a flag of janeway which protects some manager site parts
        self.is_admin = self.is_superuser
        if self.gdpr_checkbox and not self.gdpr_acceptance:
            self.gdpr_acceptance = now()
        super().save(*args, **kwargs)

    def parameters(self, journal: Journal) -> Optional["StaffWorkloadParameters"]:
        """
        Retrieve and cache staff workload parameters for the given journal.

        This method attempts to retrieve workload parameters for a specific journal and user pair
        from the cache. If unavailable, it queries the database, caches the result, and returns it.

        :param journal: The journal object for which workload parameters are retrieved.
        :type journal: Journal
        :return: The workload parameters instance, or None if not found.
        :rtype: StaffWorkloadParameters
        """
        cache_key = f"StaffWorkloadParameters:{journal.pk}:{self.pk}"
        params = cache.get(cache_key)
        if not params:
            params = StaffWorkloadParameters.objects.filter(journal=journal, user=self).first()
            cache.set(cache_key, params)
        return params

    def is_available_as_editor(self, journal: Journal) -> bool:
        """
        Check if the user is available as an editor for a given journal.

        Determine whether the current user can serve as an editor for the specified
        journal, based on their workload parameters.

        :param journal: The journal to check against.
        :type journal: Journal
        :return: True if the user is available as an editor for the journal,
            otherwise False.
        :rtype: bool
        """
        try:
            return self.parameters(journal).is_available
        except AttributeError:
            return False

    def is_enabled_as_editor(self, journal: Journal) -> bool:
        """
        Check if the current object is enabled as an editor for the given journal.

        This method determines whether the object has editing privileges enabled
        for a specific journal instance. It only checks the general flag, not possible vacancy dates
        (use :py:meth:`is_available_as_editor` if you need to check vacancy dates)

        :param journal: The journal instance to check editing privileges for
        :type journal: Journal
        :return: True if editor has marked themselves as available
        :rtype: bool
        """
        try:
            return self.parameters(journal).enabled
        except AttributeError:
            return False

    def vacancy_dates(self, journal: Journal) -> str:
        """
        Generate and return a string representation of vacancy dates for a given journal.

        Retrieve the vacancy start and end dates from the journal's parameters and
        format them as a string. If the dates are not available, return an empty string.

        Only intervals with no end / in the future are considered.

        :param journal: The journal object containing parameter details.
        :type journal: Journal
        :return: Formatted string of vacancy start and end dates, or an empty string
                 if dates are not available.
        :rtype: str
        """
        try:
            params = self.parameters(journal)
            # check if the interval is completely past (in this case we ignore it)
            future_interval = not params.vacancy_end or params.vacancy_end > timezone.localtime(now()).date()
            if future_interval and (params.vacancy_start or params.vacancy_end):
                dates = ""
                if params.vacancy_start:
                    dates = f"{dates} from {params.vacancy_start}"
                if params.vacancy_end:
                    dates = f"{dates} to {params.vacancy_end}"
                return dates.strip()
        except AttributeError:
            pass
        return ""


class Correspondence(models.Model):
    """Storage area for wjapp, PoS, SGP,... userCods."""

    # TODO: drop pk and use the three fields as pk

    account = models.ForeignKey(to=Account, on_delete=models.CASCADE, related_name="usercods")
    user_cod = models.PositiveIntegerField()
    sources = (
        ("jhep", "jhep"),
        ("pos", "pos"),
        ("jcap", "jcap"),
        ("jstat", "jstat"),
        ("jinst", "jinst"),
        ("jcom", "jcom"),
        ("jcomal", "jcomal"),
        ("sgp", "sgp"),
        ("tex", "TeX"),
    )
    source = models.CharField(max_length=6, choices=sources)
    notes = JSONField(blank=True, null=True, encoder=DjangoJSONEncoder)
    email = models.EmailField(blank=True, null=True)
    orcid = models.CharField(max_length=40, null=True, blank=True)
    used = models.BooleanField(blank=True, null=False, default=False)

    class Meta:
        """Model's Meta."""

        unique_together = ("account", "user_cod", "source", "email")

    def __str__(self):
        """Show representation (used in admin UI)."""
        return f"{self.account} <{self.account.email}> @ {self.source}"


class StaffWorkloadParameters(models.Model):
    keywords = models.ManyToManyField("submission.Keyword", through="StaffKeyword", blank=True)
    user = models.ForeignKey("core.Account", on_delete=models.CASCADE)
    journal = models.ForeignKey("journal.Journal", on_delete=models.CASCADE)
    workload = models.PositiveSmallIntegerField(default=0, verbose_name=_("Max. monthly assignments as Editor"))
    brake_on = models.PositiveSmallIntegerField(default=0)
    vacancy_start = models.DateField(_("Vacancy start"), null=True, blank=True)
    vacancy_end = models.DateField(_("Vacancy end"), null=True, blank=True)
    enabled = models.BooleanField(_("I am generally available to be assigned new submissions"), default=True)

    objects = StaffWorkloadParametersQuerySet().as_manager()

    class Meta:
        unique_together = ("user", "journal")

    def __str__(self):  # NOQA: D105
        return f"{self.user} - Assignment parameters"

    def save(self, *args, **kwargs):
        """
        Validate the instance and save it to the database.

        Performs validation on the instance before saving it to ensure
        that all data complies with the defined constraints and rules
        set in the model. This method delegates the actual save operation
        to the parent class.

        :param args: Positional arguments to pass to the parent save method.
        :param kwargs: Keyword arguments to pass to the parent save method.
        :return: None
        """  #
        cache.delete(f"StaffWorkloadParameters:{self.journal.pk}:{self.user.pk}")
        self.full_clean()
        super().save(*args, **kwargs)

    def clean(self, exclude=None):
        """
        Validate the consistency of vacancy start and end dates.

        Ensure that the vacancy end date occurs after the vacancy start date,
        raising an appropriate validation error if this condition is not met.

        :param exclude: A set of fields to exclude during validation.
        :type exclude: Optional[set]
        :return: None
        :raises ValidationError: If the vacancy end date is before the vacancy
            start date.
        """
        if self.vacancy_end and self.vacancy_start and self.vacancy_end < self.vacancy_start:
            raise ValidationError({"vacancy_end": _("Vacancy end date must be after vacancy start date.")})

    @property
    def is_available(self):
        """
        Determine if the object is currently available.

        This method checks the availability of the object based on the `enabled`
        status and its potential vacancy period defined by `vacancy_start` and
        `vacancy_end` dates. The current date is considered to establish whether
        the object is on vacancy. If the object is on vacancy or not enabled,
        it is considered unavailable.

        :return: True if the object is enabled and not currently on vacancy,
            otherwise False.
        :rtype: bool
        """
        today = timezone.now().date()
        on_vacancy = False
        if self.vacancy_start and self.vacancy_end:
            on_vacancy = self.vacancy_start <= today <= self.vacancy_end
        elif self.vacancy_start:
            on_vacancy = self.vacancy_start <= today
        elif self.vacancy_end:
            on_vacancy = today <= self.vacancy_end

        return self.enabled and not on_vacancy


class StaffKeyword(models.Model):
    parameters = models.ForeignKey(StaffWorkloadParameters, on_delete=models.CASCADE)
    keyword = models.ForeignKey("submission.Keyword", on_delete=models.CASCADE)
    weight = models.PositiveIntegerField(default=0)

    def __str__(self):  # NOQA: D105
        return f"{self.parameters.user} - Editor keyword: {self.keyword}"


class IssueParameters(models.Model):
    issue = models.OneToOneField("journal.Issue", verbose_name=_("Issue"), on_delete=models.CASCADE)
    batch_publish = models.BooleanField(_("Batch published"), default=False)
    latex_fragment = models.CharField(
        verbose_name=_("LaTeX fragment"),
        help_text=_("LaTeX fragment that should appear in the PDF of all papers of this issue."),
        max_length=500,
        blank=True,
        null=True,
    )

    class Meta:
        verbose_name = _("Issue parameters")
        verbose_name_plural = _("Issue parameters")

    def __str__(self):  # NOQA: D105
        return f"Issue parameters for {self.issue}"


# Add settings.LANGUAGES choices, but add also the empty value to avoid the need to specify a language as default
# (as it is not sure that, for example, english will be always available in settings.LANGUAGES)
def _get_language_choices():
    return tuple([("", "")] + list(settings.LANGUAGES))


class Recipient(models.Model):
    user = models.ForeignKey(
        Account,
        verbose_name=_("Newsletter topics user"),
        on_delete=models.CASCADE,
        blank=True,
        null=True,
    )
    # Here we can't have the default journal's languages,
    # so the choices' enforcing must be done at the form/template level
    language = models.CharField(
        max_length=10,
        verbose_name=_("Preferred newsletter's language"),
        blank=True,
        choices=_get_language_choices(),
    )
    journal = models.ForeignKey(Journal, verbose_name=_("Newsletter topics' journal"), on_delete=models.CASCADE)
    topics = models.ManyToManyField("submission.Keyword", verbose_name=_("Newsletters topics"), blank=True)
    news = models.BooleanField(verbose_name=_("Generic news topic"), default=False)
    newsletter_token = models.CharField(_("newsletter token for anonymous users"), max_length=500, blank=True)
    email = models.EmailField(_("Anonymous user email"), blank=True, null=True)
    confirmation_email_last_sent = models.DateTimeField(
        _("When the subscription/reminder confirmation email has been sent to an anonymous recipient"),
        blank=True,
        null=True,
    )

    class Meta:
        verbose_name = _("recipient")
        verbose_name_plural = _("recipients")
        unique_together = (
            ("user", "journal"),
            ("email", "journal"),
        )

    def __str__(self):
        return _(f"Recipient user: {self.user if self.user else self.email} - journal: {self.journal} ")

    @property
    def newsletter_destination_email(self):
        """
        Select the email address to which send the newsletter.

        :return: A string representing an email
        """
        if self.user:
            return self.user.email
        else:
            return self.email


class Newsletter(models.Model):
    last_sent = models.DateTimeField(
        verbose_name=_("Last time newsletter emails have been sent to users"),
    )
    journal = models.OneToOneField(
        Journal,
        verbose_name=_("Journal"),
        on_delete=models.CASCADE,
        related_name="newsletter",
    )


def update_display_title(self, save=False):
    """Override for Issue.update_display_title."""
    if save:
        self.save()
        return self.cached_display_title
    title = self.cached_display_title = self.pretty_issue_identifier

    return title


Issue.update_display_title = update_display_title


class WjsMiniHTMLFormField(MiniHTMLFormField):
    def __init__(self, *args, **kwargs):
        """
        Initialize the instance and configure default attributes and options for content sanitization.

        :param args: Positional arguments passed to the base class initializer.
        :param kwargs: Keyword arguments passed to the base class initializer.
            Extracts `height` with a default value of "30rem" if not specified.
        """
        height = kwargs.pop("height", "30rem")
        super().__init__(*args, **kwargs)
        self.bleach_options["tags"] = [
            "a",
            "b",
            "br",
            "div",
            "em",
            "i",
            "li",
            "ol",
            "p",
            "span",
            "strong",
            "sub",
            "sup",
            "u",
        ]
        self.bleach_options["attributes"] = {"a": ["href", "title", "target"]}
        if isinstance(self.widget, TinyMCE):
            self.widget.mce_attrs.update(
                {
                    "plugins": "link lists charmap",
                    "menubar": "",
                    "forced_root_block": "div",
                    "toolbar": "bold italic link numlist charmap",
                    "height": height,
                    "resize": True,
                    "elementpath": False,
                    "paste_data_images": False,
                }
            )
