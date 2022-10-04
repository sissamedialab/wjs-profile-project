"""Middleware for JCOM account profile."""
from django.conf import settings
from django.contrib import messages
from django.shortcuts import redirect, reverse

from utils.logger import get_logger

logger = get_logger(__name__)


class PrivacyAcknowledgedMiddleware:
    """Ensure that the logged-in user has acknowledged the privacy policy."""

    @staticmethod
    def process_request(request):
        """Ensure that the logged-in user has acknowledged the privacy policy.

        Kick in only if there is a logged-in user (otherwise return None).
        Let alone /logout and /profile.

        If the logged-in user hasn't got a gdpr_policy flag, set a
        flash message and redirect to /profile.

        """
        if not hasattr(request, "user"):
            return None

        # The following fails on <J-CODE>/profile
        # if request.path in ("/logout/", "/profile/"): <--

        # The following fails on <J-CODE>/*
        # (does the resolver know about journals?)
        # match = resolve(request.path)
        # if match.url_name in (
        #     "core_edit_profile",
        #     "core_logout",
        # ):
        free_paths = getattr(settings, "CORE_PRIVACY_MIDDLEWARE_ALLOWED_URLS", [])
        if any(request.path.endswith(free_path) for free_path in free_paths):
            return None
        if request.path.startswith("/admin/"):
            return None

        # I need `if request.user.is_authenticated` because request
        # always have a user attribute, usually an instance of
        # AnonymousUser
        if not request.user.is_authenticated:
            return None

        if hasattr(request.user, "jcomprofile"):
            logger.warning(f"User {request.user.id} has no extended profile!")
            if request.user.jcomprofile.gdpr_checkbox:
                return None

        message_text = """Please acknowledge privacy note (see checkbox below)
        or log-out to continue navigate the site.
        If you do not acknowledge pn, WRITE ME!!!
        """
        messages.add_message(
            request,
            messages.WARNING,
            message_text,
        )
        logger.debug(
            f"Redirecting {request.user.id} to profile page to acknowledge privacy."
        )
        return redirect(reverse("core_edit_profile"), permanent=False)
