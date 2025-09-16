"""Import users from wjapp."""

import mariadb
from core.models import Account
from django.conf import settings
from django.core.management.base import BaseCommand
from django.db.models import Q
from journal.models import Journal
from utils.logger import get_logger

from wjs.jcom_profile import models as wjs_models
from wjs.jcom_profile.import_utils import JOURNALS_DATA, check_mappings

logger = get_logger(__name__)


class Command(BaseCommand):
    help = "Connect to wjApp jcom database and read users data."  # noqa A003

    def handle(self, *args, **options):
        """Command entry point."""

        self.options = options
        for journal_code in ("JCOM",):
            self.journal = Journal.objects.get(code=journal_code)
            self.journal_data = JOURNALS_DATA[journal_code]
            self.import_users(**options)

    def add_arguments(self, parser):
        """Add arguments to command."""

        parser.add_argument(
            "--editors",
            default=False,
            action="store_true",
            help="import all users with editor role from wjapp",
            required=False,
        )

    def import_users(self, **options):
        """Process one article."""

        if self.options["editors"]:

            setting = f"WJAPP_{self.journal.code.upper()}_IMPORT_CONNECTION_PARAMS"
            connection_parameters = getattr(settings, setting, None)
            if connection_parameters is None:
                logger.error(
                    f"Missing connection parameters for {self.journal.code}. "
                    f'Please ensure "{setting}" exists in settings.'
                    f"Cannot connect, quitting."
                )
                return
            elif connection_parameters.get("user", "") == "":
                logger.error(
                    f'Empty connection parameters for "{setting}". Please ensure `user`, `host`, etc. are correct.'
                    f"Cannot connect, quitting."
                )
                return

            self.connection = mariadb.connect(**connection_parameters)
            all_editors = self.read_editors_data()
            for e in all_editors:
                self.save_editor(e)
            self.connection.close()

    def read_editors_data(self):
        """Read editors data from wjapp."""

        cursor_editors = self.connection.cursor(dictionary=True)
        query_editors = """
SELECT
u.userCod,
u.lastname,
u.firstname,
u.email,
u.privacy,
r.roleID,
f.featureId
FROM User u
LEFT JOIN User_Role ur USING (userCod)
LEFT JOIN Role r USING (roleCod)
LEFT JOIN User_Feature uf USING (userCod)
LEFT JOIN Feature f USING (featureCod)
WHERE
    r.roleId='EDT'
    and ur.disableFlag=0
    and f.featureId='EB_MEMBER'
"""
        cursor_editors.execute(query_editors)
        editors = cursor_editors.fetchall()
        cursor_editors.close()
        return editors

    def save_editor(self, row):
        """Save editor in wjs."""

        # exclude Rod Lamberts 8734 rod.lamberts@anu.edu.au (no more editor)
        # (this is an exception una-tantum, explicitly requested by MT)
        if row["userCod"] in [8734] and row["email"] == "rod.lamberts@anu.edu.au":
            logger.warning(f"excluded from editors import: {row}")
            return

        editor = account_get_or_create_check_correspondence(
            self.journal.code.lower(),
            row["userCod"],
            row["lastname"],
            row["firstname"],
            row["email"],
            row["privacy"],
        )
        if not editor.check_role(self.journal, "section-editor", staff_override=False):
            editor.add_account_role("section-editor", self.journal)
            logger.debug(f"added missing section-editor role for {editor}")


def account_get_or_create_check_correspondence(source, user_cod, last_name, first_name, imported_email, privacy):
    """Get a user account - check Correspondence and eventually create new account."""

    # ex: source: jcom, jcomal, prophy, ...
    # Check if we know this person form some other journal or by email

    if imported_email == "jcom_hidden_user@jcom.sissa.it":
        # using the wjapp userCod in the email the same hidden user is identified
        # in Correspondence in unique way after the hiding action on wjapp.
        # Searching in Correspondence with source and user_cod only can find
        # more than one match (email changes)
        imported_email = f"{user_cod}_jcom_hidden_user@invalid.com"
        logger.debug(f"found wjapp hidden user {user_cod=} {imported_email}")

    account_created = False
    mappings = wjs_models.Correspondence.objects.filter(
        Q(user_cod=user_cod, source=source) | Q(email=imported_email),
    )
    if mappings.count() == 0:
        # We never saw this person in other journals.
        account, account_created = Account.objects.get_or_create(
            email=imported_email,
            defaults={
                "first_name": first_name,
                "last_name": last_name,
            },
        )
        mapping = wjs_models.Correspondence.objects.create(
            user_cod=user_cod,
            source=source,
            email=imported_email,
            account=account,
        )
    elif mappings.count() >= 1:
        # We know this person from another journal
        logger.debug(
            f"wjs mapping exists ({mappings.count()} correspondences)" f" for {user_cod}/{source} or {imported_email}"
        )
        mapping = check_mappings(mappings, imported_email, user_cod, source)

    account = mapping.account
    # `used` indicates that this usercod from this source
    # has been used to create the core.Account record
    if account_created:
        mapping.used = True
        mapping.save()

    # to activate hijack function
    if not account.is_active:
        account.is_active = True
        account.save()

    # set gdpr checkbox. privacy in wjapp can be 'Y', 'N' or empty string
    if not account.jcomprofile.gdpr_checkbox:
        if privacy == "Y":
            account.jcomprofile.gdpr_checkbox = True
        elif privacy == "N":
            account.jcomprofile.gdpr_checkbox = False
        else:
            # if we don't have a clear value we do nothing (which generally result in
            # the checkbox to be false, which is safe)
            pass
        account.jcomprofile.save()
        account.save()

    return account
