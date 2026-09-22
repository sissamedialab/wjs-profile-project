import logging

from django.http import HttpResponse, HttpResponseBadRequest
from django.views import View

from wjs.jcom_profile.models import Correspondence

from .logic import get_sgp_correspondence

logger = logging.getLogger(__name__)


class GetSGPCodeView(View):
    """
    GET /services/getSGPcodfpf.jsp?wjs_account_id=<id>

    Looks up the Correspondence row with source="sgp" for the given
    wjs_account_id (populated by the import_sgp_correspondence management
    command) and returns its user_cod, which holds the SGP code
    (all_users.codice_utente_sgp).

    No token/auth check: Jp is not modified to send one, keeping this URL
    and its contract identical to the old wjapp service.

    Returns: <SGPCOD>usercod</SGPCOD>
    """

    def get(self, request, *args, **kwargs):
        # --- param validation ---
        wjs_account_id = request.GET.get("wjs_account_id")
        if not wjs_account_id:
            logger.warning("getSGPcodfpf: missing wjs_account_id param")
            return HttpResponseBadRequest("missing wjs_account_id")

        # --- lookup: account_id -> sgp code via Correspondence(source="sgp") ---
        try:
            correspondence = get_sgp_correspondence(wjs_account_id)
        except Correspondence.DoesNotExist:
            logger.error("getSGPcodfpf: no sgp correspondence for account_id=%s", wjs_account_id)
            return HttpResponseBadRequest("no correspondence found")
        except Correspondence.MultipleObjectsReturned:
            logger.error(
                "getSGPcodfpf: multiple sgp correspondences for account_id=%s",
                wjs_account_id,
            )
            return HttpResponseBadRequest("multiple correspondences found")

        sgp_code = correspondence.user_cod

        xml = f"<SGPCOD>{sgp_code}</SGPCOD>"
        return HttpResponse(xml, content_type="application/xml")
