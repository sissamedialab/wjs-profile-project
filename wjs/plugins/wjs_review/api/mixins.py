from django.shortcuts import get_object_or_404
from rest_framework.authentication import (
    SessionAuthentication as DRFSessionAuthentication,
)
from rest_framework.authentication import TokenAuthentication
from submission import models as submission_models
from utils.logger import get_logger

from .permissions import IsEOOrTypesetterForArticle

logger = get_logger(__name__)


class SessionAuthentication(DRFSessionAuthentication):
    """
    `SessionAuthentication`, but only "succeeds" for an actually logged-in user.

    DRF's own `SessionAuthentication.authenticate()` checks ``user.is_active``, not
    ``user.is_authenticated``, and Django's `AnonymousUser.is_active` is `True`. That makes it
    "succeed" (returning the `AnonymousUser`) even for a fully anonymous request. Combined with
    another authenticator such as `TokenAuthentication`, that flips DRF's 401-vs-403 choice in
    `APIView.permission_denied()` -- which looks at whether *any* authenticator succeeded, not at
    whether the resulting user is really authenticated -- turning a request with no credentials
    at all into a 403 instead of the expected 401. Checking ``is_authenticated`` here keeps a
    credential-less request truly unauthenticated.
    """

    def authenticate(self, request):
        user = getattr(request._request, "user", None)
        if not user or not user.is_authenticated:
            return None
        self.enforce_csrf(request)
        return (user, None)


class LoggedRequestMixin:
    def initial(self, request, *args, **kwargs):
        """
        Log any call, including those that fail authentication or permission checks.

        We log in a ``finally`` block (i.e. after ``super().initial()`` has run
        authentication) so that the call is recorded even when authentication
        fails: ``super().initial()`` triggers authentication, which raises for
        an invalid token. By the time we reach ``finally``, DRF has already set
        ``request.user`` to ``AnonymousUser`` (via ``_not_authenticated()``),
        so accessing it here is safe and reports an anonymous user.

        Include the IP via REMOTE_ADDR (i.e. not from X-Forwarded-For);
        this can be an issue if behind a reverse proxy
        (and if the proxy does not use apache mod_remoteip)
        """
        try:
            super().initial(request, *args, **kwargs)
        finally:
            logger.info(
                "API request %s %s by %s from %s",
                request.method,
                request.path,
                request.user.pk or request.user,  # Account.id or "AnonymousUser"
                request.META.get(
                    "REMOTE_ADDR",
                    request.META.get("X-Forwarded-For", "IP not found! Please check apache conf and django settings!"),
                ),
            )


class EOOrTypesetterAccessMixin:
    """Restrict an entry point to EO members and typesetters, as authenticated by their API token."""

    authentication_classes = [TokenAuthentication]
    permission_classes = [IsEOOrTypesetterForArticle]


class EOOrTypesetterDocsAccessMixin(EOOrTypesetterAccessMixin):
    """
    Like `EOOrTypesetterAccessMixin`, but also accepts a logged-in Janeway session.

    Used only by the OpenAPI docs views (the raw schema, Swagger UI and Redoc): a browser
    navigating directly to their URLs cannot set an ``Authorization`` header, so a human already
    logged into a Janeway session in that browser needs another way to authenticate. These views
    are GET-only, so `SessionAuthentication`'s CSRF requirement (for unsafe methods) never
    applies. The permission check (`IsEOOrTypesetterForArticle`) is unchanged.

    `TokenAuthentication` is listed first on purpose: DRF's `APIView.handle_exception()` picks the
    401-vs-403 status for an unauthenticated request from ``get_authenticate_header()``, which
    only ever consults the *first* configured authenticator. `SessionAuthentication` has no
    ``WWW-Authenticate`` header of its own (it returns `None`), so putting it first would coerce
    every credential-less request to a 403 instead of the expected 401; `TokenAuthentication`'s
    header keeps that response a 401, exactly like the rest of this API.
    """

    authentication_classes = [TokenAuthentication, SessionAuthentication]


class PublishedArticleAccessMixin(EOOrTypesetterAccessMixin):
    def get_article(self, request, pk: int):
        article = get_object_or_404(submission_models.Article, pk=pk, stage=submission_models.STAGE_PUBLISHED)
        self.check_object_permissions(request, article)
        return article
