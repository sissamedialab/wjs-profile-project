"""Shared lookups for the Jp-SGP bridge views."""

import mariadb
from django.conf import settings

from wjs.jcom_profile.models import Correspondence

from .constants import SGP_SOURCE


def get_sgp_correspondence(account_id):
    """Return the Correspondence(source="sgp") row for this WJS account id.

    Raises Correspondence.DoesNotExist / Correspondence.MultipleObjectsReturned;
    left to the caller, since the two callers respond to these differently.
    """
    return Correspondence.objects.get(account_id=account_id, source=SGP_SOURCE)


def is_sgp_code_notified(sgp_code):
    """Whether this SGP code has at least one row in the external `spedizioni` table."""
    query = "SELECT * FROM spedizioni WHERE codice_utente_sgp=?"

    connection = mariadb.connect(**settings.PROD_DB_PAG_CONNECTION_PARAMS)
    try:
        cursor = connection.cursor()
        cursor.execute(query, (sgp_code,))
        rows = cursor.fetchall()
    finally:
        connection.close()

    return len(rows) > 0


def can_access_my_payments(account_id):
    """Whether this account can use the Jp "myPayments" flow.

    Mirrors the checks MyPaymentsView itself does before redirecting: an SGP
    correspondence must exist, and its code must have been notified (have a
    `spedizioni` row). Shared so every entry point to the flow - the header
    link guard, MyPaymentsView, or a direct link e.g. from an email - agrees
    on the same condition.
    """
    try:
        sgp_code = get_sgp_correspondence(account_id).user_cod
    except (Correspondence.DoesNotExist, Correspondence.MultipleObjectsReturned):
        return False
    return is_sgp_code_notified(sgp_code)
