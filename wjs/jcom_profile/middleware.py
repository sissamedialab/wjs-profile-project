"""Middleware for JCOM account profile."""

from django.conf import settings
from django.contrib import messages
from django.shortcuts import redirect, reverse
from django.utils.deprecation import MiddlewareMixin
from utils.logger import get_logger

logger = get_logger(__name__)


# TODO: We might want to rewrite as function based middleware
class PrivacyAcknowledgedMiddleware(MiddlewareMixin):
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

        if not hasattr(request.user, "jcomprofile"):
            logger.warning(f"User {request.user.id} has no extended profile!")
            # TODO: raise exception
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
        logger.debug(f"Redirecting {request.user.id} to profile page to acknowledge privacy.")
        return redirect(reverse("core_edit_profile"), permanent=False)
