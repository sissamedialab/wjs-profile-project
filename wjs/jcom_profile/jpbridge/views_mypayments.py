import logging

from django.conf import settings
from django.contrib.auth.mixins import LoginRequiredMixin
from django.http import HttpResponse, HttpResponseRedirect
from django.urls import reverse
from django.views import View

from wjs.jcom_profile.models import Correspondence

from .logic import get_sgp_correspondence, is_sgp_code_notified

logger = logging.getLogger(__name__)


class MyPaymentsView(LoginRequiredMixin, View):
    """
    GET /myPayments/

    Before redirecting to Jp, checks that the user's SGP code has actually
    been notified (i.e. has at least one row in the external `spedizioni`
    table). If not, the redirect is aborted and an error is shown instead.

    If the check passes, redirects the user to Jp. No new cookie is set:
    WJS and Jp live under the same domain in production, so the browser's
    existing WJS session cookie is already present on the request that
    reaches Jp. Jp is expected to forward that session cookie on its own
    server-to-server call to services/getSGPcodfpf.jsp (see GetSGPCodeView).
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

        if not is_sgp_code_notified(sgp_code):
            logger.error(
                "myPayments: sgp_code=%s (account_id=%s) not found in spedizioni, aborting redirect",
                sgp_code,
                account.id,
            )
            return HttpResponse(
                "Error: the access to the form is not enabled. Please contact support.",
                status=400,
                content_type="text/plain",
            )

        jp_url = self._get_jp_url(request, account)

        logger.info("myPayments: redirecting account_id=%s to %s", account.id, jp_url)

        return HttpResponseRedirect(jp_url)

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

    def _get_jp_url(self, request, account):
        """
        Builds the Jp URL to redirect to for this journal.

        Journals not configured in WJS_JP_URLS, or configured with a falsy
        value (e.g. JCOM, JCOMAL, which don't use Jp), are silently sent to
        the journal's homepage instead.

        Appends a "sgpservice" query param carrying the path prefix Jp should call
        services/getSGPcodfpf.jsp back under (several journals can share this one deployment -
        see GetSGPCodeView and resolve_service_prefix()).
        """
        entry = settings.WJS_JP_URLS.get(request.journal.code)
        if not entry:
            return reverse("website_index")

        base_url, service_prefix = entry
        separator = "&" if "?" in base_url else "?"
        return f"{base_url}{separator}sgpservice=/{service_prefix}"
