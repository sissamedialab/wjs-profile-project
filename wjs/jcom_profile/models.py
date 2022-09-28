"""The model for a field "profession" for JCOM authors."""

from django.db import models
from django.contrib.postgres.fields import JSONField
from django.core.serializers.json import DjangoJSONEncoder
from core.models import Account
from core.models import AccountManager

from django.utils.translation import ugettext_lazy as _

# TODO: use settings.AUTH_USER_MODEL
# from django.conf import settings

PROFESSIONS = (
    (
        0,
        "A researcher in S&T studies,"
        " science communication or neighbouring field",
    ),
    (
        1,
        "A practitioner in S&T"
        " (e.g. journalist, museum staff, writer, ...)",
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
    janeway_account = models.OneToOneField(
        Account,
        on_delete=models.CASCADE,
        primary_key=True,
    )
    # Even if EO wants "profession" to be mandatory, we cannot set it
    # to `null=False` (i.e. `NOT NULL` at DB level) because we do not
    # have this data for most of our existing users.
    profession = models.IntegerField(null=True, choices=PROFESSIONS)
    gdpr_checkbox = models.BooleanField(_("GDPR acceptance checkbox"), default=False)
    invitation_token = models.CharField(_("Invitation token"), max_length=500, default="")


class Correspondence(models.Model):
    """Storage area for wjapp, PoS, SGP,... userCods."""

    # TODO: drop pk and use the three fields as pk

    account = models.ForeignKey(
        to=Account, on_delete=models.CASCADE, related_name="usercods"
    )
    userCod = models.PositiveIntegerField()

    # django >= 3.0
    # class Sources(models.IntegerChoices):
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

        # django >= 2...
        # constraints = [
        #     models.UniqueConstraint(fields=("account", "userCod", "source")),
        # ]
        unique_together = ("account", "userCod", "source")
