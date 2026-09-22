import logging

import requests
from django.contrib.auth.mixins import LoginRequiredMixin, UserPassesTestMixin
from django.http import HttpResponse
from django.urls import reverse
from django.views import View

from ..permissions import get_hijacker
from .constants import WJS_IDENTITY_COOKIE

logger = logging.getLogger(__name__)


class FakeJpView(LoginRequiredMixin, UserPassesTestMixin, View):
    """
    DEV/TEST ONLY — simulates the Jp side of the flow:
    reads the identity cookie, calls WJS's services/getSGPcodfpf.jsp endpoint,
    and displays the raw result.

    Not part of the real Jp application: this only exists to validate the
    WJS side (cookie sharing + endpoint) end-to-end locally, without a real
    Jp instance available. Restricted to staff, since it exposes raw
    request/response details of the SGP bridge. The typical test flow is a
    staff member hijacking a non-staff author to see the redirect end to
    end, so staff is checked on the hijacker too, not just request.user.
    """

    def test_func(self):
        if self.request.user.is_staff:
            return True
        hijacker = get_hijacker()
        return bool(hijacker and hijacker.is_staff)

    def get(self, request, *args, **kwargs):
        wjs_identity = request.COOKIES.get(WJS_IDENTITY_COOKIE)

        if not wjs_identity:
            logger.warning("fake_jp: identity cookie not received")
            return HttpResponse(
                "ERROR: identity cookie not found on this request.\n"
                "Check that WJS_COOKIE_DOMAIN is a shared parent domain "
                "between WJS and this host.",
                status=400,
                content_type="text/plain",
            )

        # Same host/scheme this view was itself reached on: FakeJpView and the real
        # getSGPcodfpf endpoint are served by the same WJS instance, so this always
        # matches, with no per-environment setting to keep in sync.
        sgp_url = request.build_absolute_uri(reverse("get_sgp_code"))

        try:
            response = requests.get(
                sgp_url,
                params={"wjs_account_id": wjs_identity},
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
            "fake_jp: identity=%s -> status=%s body=%s",
            wjs_identity,
            response.status_code,
            response.text,
        )

        body = (
            f"identity cookie: {wjs_identity}\n"
            f"getSGPcodfpf status: {response.status_code}\n"
            f"getSGPcodfpf body: {response.text}\n"
        )
        return HttpResponse(body, content_type="text/plain")
