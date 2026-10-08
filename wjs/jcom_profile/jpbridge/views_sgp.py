import logging

from django.http import HttpResponse, HttpResponseBadRequest, HttpResponseForbidden
from django.views import View

from wjs.jcom_profile.models import Correspondence

from .logic import get_sgp_correspondence

logger = logging.getLogger(__name__)


class GetSGPCodeView(View):
    """
    GET /services/getSGPcodfpf.jsp

    Jp calls this as a server-to-server request, forwarding the cookies it
    received on the browser request that reached it from MyPaymentsView
    (WJS and Jp share the same domain in production, so the browser's WJS
    session cookie is already present on that request). The caller is
    identified via that forwarded session cookie (request.user), not via
    any request parameter.

    Looks up the Correspondence row with source="sgp" for the logged-in
    account (populated by the import_sgp_correspondence management command)
    and returns its user_cod, which holds the SGP code
    (all_users.codice_utente_sgp).

    Returns: <SGPCOD>usercod</SGPCOD>
    """

    def get(self, request, *args, **kwargs):
        if not request.user.is_authenticated:
            logger.warning("getSGPcodfpf: no authenticated session on request")
            return HttpResponseForbidden("no authenticated session")

        # --- lookup: account_id -> sgp code via Correspondence(source="sgp") ---
        try:
            correspondence = get_sgp_correspondence(request.user.id)
        except Correspondence.DoesNotExist:
            logger.error("getSGPcodfpf: no sgp correspondence for account_id=%s", request.user.id)
            return HttpResponseBadRequest("no correspondence found")
        except Correspondence.MultipleObjectsReturned:
            logger.error(
                "getSGPcodfpf: multiple sgp correspondences for account_id=%s",
                request.user.id,
            )
            return HttpResponseBadRequest("multiple correspondences found")

        sgp_code = correspondence.user_cod

        xml = f"<SGPCOD>{sgp_code}</SGPCOD>"
        return HttpResponse(xml, content_type="application/xml")
