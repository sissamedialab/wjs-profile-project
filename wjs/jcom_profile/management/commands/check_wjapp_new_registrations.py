"""Compare newly registered users in wjapp with possibile match in Janeway."""
from urllib.parse import urlencode

import mariadb
from core.models import Account
from django.conf import settings
from django.core.mail import send_mail
from django.core.management.base import BaseCommand
from django.db.models import Q
from journal.models import Journal
from utils.logger import get_logger
from utils.setting_handler import get_setting

logger = get_logger(__name__)


class Command(BaseCommand):
    help = "Compare newly registered users in wjapp with possibile match in Janeway."  # NOQA A003

    def add_arguments(self, parser):
        """Add arguments to command."""
        parser.add_argument(
            "--lookback-days",
            default=1,
            help="Number of days (form today) to take into consideration for new registrations in wjapp."
            "Defaults to %(default)s",
        )
        parser.add_argument(
            "--mail-recipients",
            default="wjs-support@medialab.sissa.it",
            help="Recipients of eventual notifications (comma-separated). Defaults to %(default)s",
        )

    def handle(self, *args, **options):
        """Command entry point."""
        message = ""
        for journal_code in ("JCOM", "JCOMAL"):
            journal = Journal.objects.get(code=journal_code)
            tmp = self.process_journal(journal, **options)
            if tmp:
                message += f"\n\n# From {journal_code}\n\n"
                message += tmp

        if message:
            message += "\n\n🙂\n"
            from_address = get_setting(
                "general",
                "from_address",
                journal,
                create=False,
                default=True,
            ).value
            send_mail(
                subject=f"{options['journal_code']} new users to check",
                message=message,
                from_email=from_address,
                recipient_list=options["mail_recipients"].split(","),
                fail_silently=False,
            )

    def process_journal(self, journal, **options):
        """Process one journal."""
        setting = f"WJAPP_{journal.code.upper()}_CONNECTION_PARAMS"
        connection_parameters = getattr(settings, setting, None)
        if connection_parameters is None:
            logger.error(f'Unknown journal {journal.code}. Please ensure "{setting}" exists in settings.')
            return
        logger.debug(f"Processing {journal.code}")
        connection = mariadb.connect(**connection_parameters)
        cursor = connection.cursor(dictionary=True)
        cursor.execute(
            "select count(*) as count from User where registrationDate > date_sub(now(), interval ? day)",
            (options["lookback_days"],),
        )
        row = cursor.fetchone()
        new_user_count = row["count"]
        if new_user_count == 0:
            logger.debug("No new users, nothing to report.")
            return

        # NB: AFAICT, there is nothing that prevents a User to have
        # multiple Orcidid, so this query could yield multiple rows
        # for the same user. As of today there are no orcid in JCOM
        # and the net result would just be a "confused" message, which
        # is an acceptable risk.
        statement = """
        select u.userCod, u.firstName, u.lastName, u.email,
        date_format(u.registrationDate, "%d/%m/%Y %H:%i") as date_registered,
        o.orcidid
        from User u left join OrcidId o on o.userCod = u.userCod
        where u.registrationDate > date_sub(now(), interval ? day)
        order by u.registrationDate;
        """
        cursor.execute(statement, (options["lookback_days"],))

        message = ""
        base_url = journal.site_url()
        current_source = journal.code.lower()
        for new_user in cursor:
            logger.debug(f'{new_user["userCod"]} - {new_user["firstName"]} {new_user["lastName"]}')

            similar_accounts = Account.objects.filter(
                Q(email=new_user["email"]) | Q(last_name__icontains=new_user["lastName"]),
            )

            # Each similar account could or could not already have a mapping.
            # - the similar account does have mapping
            #   - always propose to edit the existing mappings
            #   - one of the mappings is from the journal under consideration
            #     --> noop (why are we here??? double registration on wjapp???)
            #   - no mapping from the journal under consideration
            #     --> propose to add mapping for the journal
            # - the similar account does NOT have mapping
            #     --> propose to add mapping
            #
            # Mapping are used during import from wjapp to identify existing authors.
            # See import_from_wjapp.py:241.

            if similar_accounts.exists():
                logger.debug(f"  similar to {similar_accounts}")
                message += "\n"
                message += f"{new_user['userCod']} - {new_user['firstName']} {new_user['lastName']}"
                message += f" <{new_user['email']}> (registered {new_user['date_registered']})"
                message += " is similar to:\n"

                for a in similar_accounts:
                    if a.usercods.exists():
                        for mapping in a.usercods.all():
                            if mapping.source != current_source:
                                new_mapping_message = self.new_mapping_message(current_source, base_url, new_user, a)
                                message += new_mapping_message

                            message += f" ✏ {mapping}: {base_url}"
                            message += f"/admin/jcom_profile/correspondence/{mapping.id}/change/\n"
                    else:
                        new_mapping_message = self.new_mapping_message(current_source, base_url, new_user, a)
                        message += new_mapping_message

        return message

    def new_mapping_message(self, source, url, wjapp_user, janeway_account):
        """Build and return the URL path and query string for a new mapping (aka Correspondence)."""
        params = {
            "source": source,
            "user_cod": wjapp_user["userCod"],
            "account": janeway_account.id,
            "email": wjapp_user["email"],
        }
        if wjapp_user["orcidid"] is not None:
            params["ordcid"] = wjapp_user["orcidid"]
        query_string = urlencode(params)
        path = f"admin/jcom_profile/correspondence/add/?{query_string}"
        return f" ➕ {janeway_account} <{janeway_account.email}>: {url}/{path}\n"
