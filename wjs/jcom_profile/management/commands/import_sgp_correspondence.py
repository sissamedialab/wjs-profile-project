import logging

import mariadb
from django.conf import settings
from django.core.management.base import BaseCommand
from django.db import transaction

from wjs.jcom_profile.models import Correspondence

logger = logging.getLogger(__name__)

JCAP_SOURCE = "jcap"
SGP_SOURCE = "sgp"


class Command(BaseCommand):
    help = (  # noqa: A003
        "For each all_users row with a jcapCod, if a Correspondence(source='jcap', "
        "user_cod=jcapCod) exists, create a Correspondence(source='sgp', "
        "user_cod=codice_utente_sgp) pointing to the same account_id."
    )

    def handle(self, *args, **options):
        all_users_rows = self._fetch_all_users()

        created_count = 0
        skipped_no_match = 0
        skipped_already_exists = 0
        error_count = 0

        for row in all_users_rows:
            # explicit key access: if an expected field is missing, fail loudly
            try:
                jcap_cod = row["jcapCod"]
                sgp_code = row["codice_utente_sgp"]
            except KeyError:
                logger.error("import_sgp_correspondence: malformed row %r", row)
                error_count += 1
                continue

            if jcap_cod is None:
                # all_users row not linked to jcap, not relevant here
                continue

            found = False
            jcap_correspondence = None
            try:
                jcap_correspondence = Correspondence.objects.get(user_cod=jcap_cod, source=JCAP_SOURCE)
                found = True
            except Correspondence.DoesNotExist:
                found = False
            except Correspondence.MultipleObjectsReturned:
                logger.error(
                    "import_sgp_correspondence: multiple jcap correspondences for jcapCod=%s",
                    jcap_cod,
                )
                error_count += 1
                continue

            if not found:
                logger.warning(
                    "import_sgp_correspondence: no jcap correspondence for jcapCod=%s (sgp_code=%s)",
                    jcap_cod,
                    sgp_code,
                )
                skipped_no_match += 1
                continue

            # avoid duplicates if the command is run again
            already_exists = Correspondence.objects.filter(
                account_id=jcap_correspondence.account_id,
                user_cod=sgp_code,
                source=SGP_SOURCE,
                email=jcap_correspondence.email,
            ).exists()

            if already_exists:
                skipped_already_exists += 1
                continue

            with transaction.atomic():
                Correspondence.objects.create(
                    account_id=jcap_correspondence.account_id,
                    user_cod=sgp_code,
                    source=SGP_SOURCE,
                    email=jcap_correspondence.email,
                    used=jcap_correspondence.used,
                )

            created_count += 1

        logger.info(
            "import_sgp_correspondence: created=%s skipped_no_match=%s " "skipped_already_exists=%s errors=%s",
            created_count,
            skipped_no_match,
            skipped_already_exists,
            error_count,
        )
        self.stdout.write(
            f"Done. created={created_count} skipped_no_match={skipped_no_match} "
            f"skipped_already_exists={skipped_already_exists} errors={error_count}"
        )

    def _fetch_all_users(self):
        """
        Query all_users on the external MariaDB (connection params from
        settings.PROD_DB_PAG_CONNECTION_PARAMS, not Django's DATABASES).
        Returns a list of dicts with keys 'jcapCod' and 'codice_utente_sgp'.
        """
        query = """
            SELECT jcapCod, codice_utente_sgp
            FROM all_users
        """
        connection = mariadb.connect(**settings.PROD_DB_PAG_CONNECTION_PARAMS)
        try:
            cursor = connection.cursor(dictionary=True)
            cursor.execute(query)
            return cursor.fetchall()
        finally:
            connection.close()
