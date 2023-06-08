"""The model for a field "profession" for JCOM authors."""
from core.models import Account, AccountManager
from django.conf import settings
from django.core.serializers.json import DjangoJSONEncoder
from django.db import models
from django.db.models import JSONField, Q
from django.urls import reverse
from django.utils import timezone
from django.utils.translation import gettext as _
from journal.models import Issue, Journal
from sortedm2m.fields import SortedManyToManyField
from submission.models import Article, Section
from utils import logic as utils_logic

# TODO: use settings.AUTH_USER_MODEL

PROFESSIONS = (
    (
        0,
        "A researcher in S&T studies," " science communication or neighbouring field",
    ),
    (
        1,
        "A practitioner in S&T" " (e.g. journalist, museum staff, writer, ...)",
    ),
    (2, "An active scientist"),
    (3, "Other"),
)


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
    profession = models.IntegerField(null=True, choices=PROFESSIONS)
    gdpr_checkbox = models.BooleanField(_("GDPR acceptance checkbox"), default=False)
    invitation_token = models.CharField(_("Invitation token"), max_length=500, default="")


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
    )
    source = models.CharField(max_length=6, choices=sources)
    notes = JSONField(blank=True, null=True, encoder=DjangoJSONEncoder)
    email = models.EmailField(blank=True, null=True)
    orcid = models.CharField(max_length=40, null=True, blank=True)
    used = models.BooleanField(blank=True, null=False, default=False)

    class Meta:
        """Model's Meta."""

        unique_together = ("account", "user_cod", "source")

    def __str__(self):
        """Show representation (used in admin UI)."""
        return f"{self.account} <{self.account.email}> @ {self.source}"


class SIQuerySet(models.QuerySet):
    """Query sets (filters) for Special Issues."""

    def open_for_submission(self):
        """Build a queryset of Special Issues open for submission."""
        _now = timezone.now()
        return self.filter(models.Q(close_date__isnull=True) | models.Q(close_date__gte=_now), open_date__lte=_now)

    def current_journal(self):
        """Build a queryset of all Special Issues of the "requested" journal."""
        request = utils_logic.get_current_request()
        if request and request.journal:
            return self.filter(journal=request.journal)
        else:
            return self.none()

    def current_user(self):
        """Build a queryset of Special Issues available to the current user.

        This means Special Issues without invitees
        or with the user in the invitees.
        """
        request = utils_logic.get_current_request()
        if request and request.user:
            return self.filter(
                Q(invitees__isnull=True) | Q(invitees=request.user),
            )
        else:
            return self.none()


class SpecialIssue(models.Model):
    """A Special Issue.

    A "container" of articles to which authors (maybe directly
    invited) can direct their submission.

    Special Issues are relative to a single journal and can be set to
    accept submission only for a limited time span. They may contain
    also additional material, that can or cannot be made visible to
    the public.

    """

    objects = SIQuerySet().as_manager()

    name = models.CharField(max_length=121, help_text="Name / title / long name", blank=False, null=False)
    short_name = models.SlugField(
        max_length=21,
        help_text="Short name or code (please only [a-zA-Z0-9_-]",
        blank=False,
        null=False,
    )
    description = models.TextField(help_text="Description or abstract", blank=False, null=False)

    open_date = models.DateTimeField(
        help_text="Authors can submit to this special issue only after this date",
        blank=True,
        null=False,
        default=timezone.now,
    )
    close_date = models.DateTimeField(
        help_text="Authors cannot submit to this special issue after this date",
        blank=True,
        null=True,
    )
    journal = models.ForeignKey(to=Journal, on_delete=models.CASCADE)
    documents = models.ManyToManyField(to="core.File", limit_choices_to={"article_id": None}, blank=True, null=True)
    invitees = models.ManyToManyField(
        to="core.Account",
        related_name="special_issue_invited",
    )
    # A S.I. can impose a filter on submittable article types ("sections")
    allowed_sections = models.ManyToManyField(to="submission.Section")
    editors = models.ManyToManyField("core.Account", blank=True)

    def get_absolute_url(self):
        """Get the absolute URL (where create-view redirects on success)."""
        return reverse("si-update", kwargs={"pk": self.pk})

    def save(self, *args, **kwargs):
        """Set the default for field allowed_sections."""
        super().save(*args, **kwargs)
        if not self.allowed_sections.exists():
            self.allowed_sections.set(Section.objects.filter(journal=self.journal))

    def is_open_for_submission(self):
        """Compute if this special issue is open for submission."""
        # WARNING: must be coherent with queryset SIQuerySet
        now = timezone.now()
        return self.open_date <= now and self.close_date >= now

    def __str__(self):
        """Show representation (used in admin UI)."""
        if self.is_open_for_submission:
            return self.name
        else:
            return f"{self.name} - closed"


# class ArticleWrapper(Article):
class ArticleWrapper(models.Model):
    """An enrichment of Janeway's Article."""

    # Do not inherit from Article, otherwise we get Article's method
    # `save()` which does things that raise IntegrityError when called
    # from here...
    janeway_article = models.OneToOneField(
        Article,
        on_delete=models.CASCADE,
        parent_link=True,
        primary_key=True,
    )
    special_issue = models.ForeignKey(
        to=SpecialIssue,
        on_delete=models.DO_NOTHING,  # TODO: check me!
        related_name="articles",
        null=True,
    )
    nid = models.IntegerField(
        help_text="Drupal's Node ID. Keeping for future reference and extra check during import.",
        unique=True,
        blank=True,
        null=True,
    )


class EditorAssignmentParameters(models.Model):
    # FIXME: Change keywords field when Keyword will be linked to a specific Journal
    keywords = models.ManyToManyField("submission.Keyword", through="EditorKeyword", blank=True)
    editor = models.ForeignKey("core.Account", on_delete=models.CASCADE)
    journal = models.ForeignKey("journal.Journal", on_delete=models.CASCADE)
    workload = models.PositiveSmallIntegerField(default=0)
    brake_on = models.PositiveSmallIntegerField(default=0)

    def __str__(self):  #
        return f"{self.editor} - Assignment parameters"


class EditorKeyword(models.Model):
    editor_parameters = models.ForeignKey(EditorAssignmentParameters, on_delete=models.CASCADE)
    keyword = models.ForeignKey("submission.Keyword", on_delete=models.CASCADE)
    weight = models.PositiveIntegerField(default=0)

    def __str__(self):  # NOQA: D105
        return f"{self.editor_parameters.editor} - Editor keyword: {self.keyword}"


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


class Genealogy(models.Model):
    """Maintain relations of type parent/children between articles."""

    parent = models.OneToOneField(
        Article,
        verbose_name=_("Introduction"),
        on_delete=models.CASCADE,
        related_name="genealogy",
    )
    children = SortedManyToManyField(
        Article,
        related_name="ancestors",
    )

    def __str__(self):
        return f"Genealogy: article {self.parent} has {self.children.count()} kids"


class Newsletter(models.Model):
    last_sent = models.DateTimeField(
        verbose_name=_("Last time newsletter emails have been sent to users"),
        auto_now=True,
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
