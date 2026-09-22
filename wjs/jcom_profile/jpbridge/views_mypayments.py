import logging

import mariadb
from django.conf import settings
from django.contrib.auth.mixins import LoginRequiredMixin
from django.http import HttpResponse, HttpResponseRedirect
from django.urls import reverse
from django.views import View

from wjs.jcom_profile.models import Correspondence

from .constants import WJS_IDENTITY_COOKIE
from .logic import get_sgp_correspondence

logger = logging.getLogger(__name__)


class MyPaymentsView(LoginRequiredMixin, View):
    """
    GET /myPayments/

    Before redirecting to Jp, checks that the user's SGP code has actually
    been notified (i.e. has at least one row in the external `spedizioni`
    table). If not, the redirect is aborted and an error is shown instead.

    If the check passes, sets a cookie `identity` containing the WJS
    account id, then redirects the user to Jp. WJS and Jp must live under
    the same domain (or a shared parent domain) for Jp to be able to read
    this cookie. The cookie name matches what Jp already expects from the
    old wjapp system, so Jp itself requires no changes.
    """

    def get(self, request, *args, **kwargs):
        account = request.user

        sgp_code = self._get_sgp_code(account)
        if sgp_code is None:
            logger.error("myPayments: no sgp correspondence for account_id=%s", account.id)
            return HttpResponse(
                "Error: no SGP code associated with this account.",
                status=400,
                content_type="text/plain",
            )

        if not self._sgp_code_was_notified(sgp_code):
            logger.error(
                "myPayments: sgp_code=%s (account_id=%s) not found in spedizioni, " "aborting redirect",
                sgp_code,
                account.id,
            )
            return HttpResponse(
                "Error: the access to the form is not enabled. " "Please contact support.",
                status=400,
                content_type="text/plain",
            )

        jp_url = self._get_jp_url(request, account)

        response = HttpResponseRedirect(jp_url)
        response.set_cookie(
            WJS_IDENTITY_COOKIE,
            str(account.id),
            # WJS_COOKIE_DOMAIN is only meant for local/test setups where WJS and Jp are
            # on different subdomains; in a real deployment they share the exact same
            # domain, so force None outside DEBUG rather than trust every instance's
            # settings to remember to unset a test-only value.
            domain=settings.WJS_COOKIE_DOMAIN if settings.DEBUG else None,
            secure=request.journal.is_secure,
            httponly=True,  # not needed by JS, only read server-side by Jp
            samesite="Lax",  # allows the cookie to survive the cross-page redirect
            max_age=settings.WJS_IDENTITY_COOKIE_MAX_AGE,  # e.g. 300 (seconds), short-lived
        )

        logger.info(
            "myPayments: set identity cookie for account_id=%s, redirecting to %s",
            account.id,
            jp_url,
        )

        return response

    def _get_sgp_code(self, account):
        """
        Returns the SGP code (Correspondence.user_cod, source='sgp') for this
        account, or None if no such correspondence exists.
        """
        try:
            return get_sgp_correspondence(account.id).user_cod
        except Correspondence.DoesNotExist:
            return None
        except Correspondence.MultipleObjectsReturned:
            logger.error("myPayments: multiple sgp correspondences for account_id=%s", account.id)
            return None

    def _sgp_code_was_notified(self, sgp_code):
        """
        Checks the external MariaDB `spedizioni` table for at least one row
        matching this SGP code. Returns True if found, False otherwise.
        """
        query = "SELECT * FROM spedizioni WHERE codice_utente_sgp=?"

        connection = mariadb.connect(**settings.PROD_DB_PAG_CONNECTION_PARAMS)
        try:
            cursor = connection.cursor()
            cursor.execute(query, (sgp_code,))
            rows = cursor.fetchall()
        finally:
            connection.close()

        return len(rows) > 0

    def _get_jp_url(self, request, account):
        """
        Builds the Jp URL to redirect to for this journal.

        Journals not configured in WJS_JP_URLS, or configured with a falsy
        value (e.g. JCOM, JCOMAL, which don't use Jp), are silently sent to
        the journal's homepage instead.
        """
        return settings.WJS_JP_URLS.get(request.journal.code) or reverse("website_index")
