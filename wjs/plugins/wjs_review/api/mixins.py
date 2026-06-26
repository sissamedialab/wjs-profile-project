from django.shortcuts import get_object_or_404
from rest_framework.authentication import TokenAuthentication
from submission import models as submission_models
from utils.logger import get_logger

from .permissions import IsEOOrTypesetterForArticle

logger = get_logger(__name__)


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


class PublishedArticleAccessMixin:
    authentication_classes = [TokenAuthentication]
    permission_classes = [IsEOOrTypesetterForArticle]

    def get_article(self, request, pk: int):
        article = get_object_or_404(submission_models.Article, pk=pk, stage=submission_models.STAGE_PUBLISHED)
        self.check_object_permissions(request, article)
        return article
