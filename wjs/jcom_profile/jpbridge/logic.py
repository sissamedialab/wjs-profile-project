"""Shared lookups for the Jp-SGP bridge views."""

import logging

import mariadb
from django.conf import settings

from wjs.jcom_profile.models import Correspondence

from .constants import SGP_SOURCE

logger = logging.getLogger(__name__)


def get_sgp_correspondence(account_id):
    """Return the Correspondence(source="sgp") row for this WJS account id.

    Raises Correspondence.DoesNotExist / Correspondence.MultipleObjectsReturned;
    left to the caller, since the two callers respond to these differently.
    """
    return Correspondence.objects.get(account_id=account_id, source=SGP_SOURCE)


def is_sgp_code_notified(sgp_code):
    """Whether this SGP code has at least one row in the external `spedizioni` table."""
    query = "SELECT 1 FROM spedizioni WHERE codice_utente_sgp=? LIMIT 1"

    connection = mariadb.connect(**settings.PROD_DB_PAG_CONNECTION_PARAMS)
    try:
        cursor = connection.cursor()
        cursor.execute(query, (sgp_code,))
        row = cursor.fetchone()
    finally:
        connection.close()

    return row is not None


def resolve_service_prefix(journal_code):
    """Path prefix Jp must call services/getSGPcodfpf.jsp back under, for this journal.

    Reads the (url, service_prefix) tuple for this journal from settings.WJS_JP_URLS - override
    the whole dict in a test deployment's local settings for that environment's own convention
    (see the setting's own comment).
    """
    entry = settings.WJS_JP_URLS.get(journal_code)
    return entry[1] if entry else None


def can_access_my_payments(account_id):
    """Whether this account can use the Jp "myPayments" flow.

    Mirrors the checks MyPaymentsView itself does before redirecting: an SGP
    correspondence must exist, and its code must have been notified (have a
    `spedizioni` row). Shared so every entry point to the flow - the header
    link guard, MyPaymentsView, or a direct link e.g. from an email - agrees
    on the same condition.

    Errors reaching the external `spedizioni` DB are logged and treated as
    "no access", so that an unreachable DB does not break the pages that
    merely show the header link.
    """
    try:
        sgp_code = get_sgp_correspondence(account_id).user_cod
    except (Correspondence.DoesNotExist, Correspondence.MultipleObjectsReturned):
        return False
    try:
        return is_sgp_code_notified(sgp_code)
    except mariadb.Error:
        logger.exception("Cannot check spedizioni for sgp_code=%s (account_id=%s)", sgp_code, account_id)
        return False
