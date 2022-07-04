"""The model for a field "profession" for JCOM authors."""

from django.db import models
from core.models import Account
from core.models import AccountManager

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


class UserCod(models.Model):
    """Storage area for wjapp, PoS, SGP,... userCods."""

    # TODO: drop pk and use the three fields as pk

    account = models.ForeignKey(
        to=Account, on_delete=models.CASCADE, related_name="usercods"
    )
    userCod = models.CharField(max_length=100)

    # django >= 3.0
    # class Sources(models.IntegerChoices):
    #     """Source of the userCod."""
    #     jhep = 0
    #     pos = 1
    #     jcap = 2
    #     jinst = 3
    #     jstat = 4
    #     jcom = 5
    #     jcomal = 6
    #     sgp = 7
    # source = models.IntegerField(choices=Sources.choices)

    sources = (
        (0, "jhep"),
        (1, "pos"),
        (2, "jcap"),
        (3, "jinst"),
        (4, "jstat"),
        (5, "jcom"),
        (6, "jcomal"),
        (7, "sgp"),
    )
    source = models.IntegerField(choices=sources)

    note = models.TextField(blank=True, null=True)

    class Meta:
        """Model's Meta."""

        # django >= 2...
        # constraints = [
        #     models.UniqueConstraint(fields=("account", "userCod", "source")),
        # ]
        unique_together = ("account", "userCod", "source")
