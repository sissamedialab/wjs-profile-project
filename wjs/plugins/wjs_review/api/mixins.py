from django.shortcuts import get_object_or_404
from rest_framework.authentication import (
    SessionAuthentication as DRFSessionAuthentication,
)
from rest_framework.authentication import TokenAuthentication
from rest_framework.negotiation import DefaultContentNegotiation
from submission import models as submission_models
from utils.logger import get_logger

from .permissions import IsEOOrTypesetterForArticle

logger = get_logger(__name__)


class IgnoreAcceptHeaderNegotiation(DefaultContentNegotiation):
    """Content negotiation that never refuses a request because of its "Accept" header.

    The entry points serving a file answer with the media type of the file itself, which is
    decided by the resource and not by the client, and they do it with a plain Django response
    that never goes through a renderer. DRF's negotiation, on the other hand, matches "Accept"
    against the view's *renderers* -- JSON, which these views need only for their error envelopes
    -- and raises `NotAcceptable` for anything else, from `APIView.initial()`, i.e. before
    authentication has even run. A client following this API's OpenAPI schema asks for exactly
    the media type the schema promises ("Accept: application/zip", ...), and would get a 406.

    Picking the first renderer whatever the client asked for keeps the error envelopes JSON and
    lets the file responses through untouched. Parser selection is left to
    `DefaultContentNegotiation`: the request body's own content type is still honoured.
    """

    def select_renderer(self, request, renderers, format_suffix=None):
        """
        Return the view's first renderer, whatever the client asked for.

        :return: the renderer to use, and the media type to report for it.
        :rtype: tuple
        """
        return (renderers[0], renderers[0].media_type)


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
    """
    Restrict an entry point to EO members and typesetters.

    Two credentials get a request past authentication: an API token, or the session of a user
    already logged into Janeway in the browser. The session is what makes the OpenAPI docs usable
    at all -- a browser navigating to Swagger UI or Redoc cannot set an ``Authorization`` header
    -- and, on the API entry points themselves, what lets that same human try the API out from
    Swagger UI without first minting a token for themselves. The permission check
    (`IsEOOrTypesetterForArticle`) is the same whichever credential was used.

    `TokenAuthentication` is listed first on purpose: DRF's `APIView.handle_exception()` picks the
    401-vs-403 status for an unauthenticated request from ``get_authenticate_header()``, which
    only ever consults the *first* configured authenticator. `SessionAuthentication` has no
    ``WWW-Authenticate`` header of its own (it returns `None`), so putting it first would coerce
    every credential-less request to a 403 instead of the expected 401.

    A session-authenticated *write* still needs a CSRF token, as `SessionAuthentication` enforces
    it for unsafe methods -- and must: a cookie alone would let any other site make a logged-in
    EO's browser replace an article's sources. Swagger UI sends the token on every non-GET
    same-origin request, so "Try it out" works there; a token-authenticated client is not
    concerned, since `SessionAuthentication` never runs for it.
    """

    authentication_classes = [TokenAuthentication, SessionAuthentication]
    permission_classes = [IsEOOrTypesetterForArticle]


class PublishedArticleAccessMixin(EOOrTypesetterAccessMixin):
    def get_article(self, request, pk: int):
        article = get_object_or_404(submission_models.Article, pk=pk, stage=submission_models.STAGE_PUBLISHED)
        self.check_object_permissions(request, article)
        return article
