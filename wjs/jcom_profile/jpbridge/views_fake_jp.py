import logging

import requests
from django.contrib.auth.mixins import LoginRequiredMixin, UserPassesTestMixin
from django.http import HttpResponse
from django.urls import reverse
from django.views import View

from ..permissions import get_hijacker
from .logic import resolve_service_prefix

logger = logging.getLogger(__name__)


class FakeJpView(LoginRequiredMixin, UserPassesTestMixin, View):
    """
    DEV/TEST ONLY — simulates the Jp side of the flow: forwards this
    request's cookies (which include the real WJS session cookie, since
    this view itself requires login) to WJS's services/getSGPcodfpf.jsp
    endpoint, and displays the raw result.

    Not part of the real Jp application: this only exists to validate the
    WJS side (session-cookie forwarding + endpoint) end-to-end locally,
    without a real Jp instance available. Restricted to staff, since it
    exposes raw request/response details of the SGP bridge. The typical
    test flow is a staff member hijacking a non-staff author to see the
    redirect end to end, so staff is checked on the hijacker too, not just
    request.user.
    """

    def test_func(self):
        if self.request.user.is_staff:
            return True
        hijacker = get_hijacker()
        return bool(hijacker and hijacker.is_staff)

    def get(self, request, *args, **kwargs):
        # Same host/scheme this view was itself reached on: FakeJpView and the real
        # getSGPcodfpf endpoint are served by the same WJS instance, so this always
        # matches. There's no real Jp here to have received a "sgpservice" query param, so this
        # builds the same prefix the real Jp would have been told to use (see resolve_service_prefix()).
        service_prefix = resolve_service_prefix(request.journal.code)
        sgp_url = request.build_absolute_uri(reverse("get_sgp_code", kwargs={"service_prefix": service_prefix}))

        try:
            response = requests.get(
                sgp_url,
                # Simulates Jp relaying whatever cookies arrived on its own request,
                # including the real WJS session cookie.
                cookies=request.COOKIES,
                timeout=5,
            )
        except requests.RequestException as exc:
            logger.error("fake_jp: request to %s failed: %s", sgp_url, exc)
            return HttpResponse(
                f"ERROR: request to {sgp_url} failed: {exc}",
                status=502,
                content_type="text/plain",
            )

        logger.info(
            "fake_jp: forwarded cookies -> status=%s body=%s",
            response.status_code,
            response.text,
        )

        body = f"getSGPcodfpf status: {response.status_code}\ngetSGPcodfpf body: {response.text}\n"
        return HttpResponse(body, content_type="text/plain")
