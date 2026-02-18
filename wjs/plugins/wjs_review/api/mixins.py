from django.shortcuts import get_object_or_404
from rest_framework.authentication import TokenAuthentication
from submission import models as submission_models

from .permissions import IsEOOrTypesetterForArticle


class PublishedArticleAccessMixin:
    authentication_classes = [TokenAuthentication]
    permission_classes = [IsEOOrTypesetterForArticle]

    def get_article(self, request, pk: int):
        article = get_object_or_404(submission_models.Article, pk=pk, stage=submission_models.STAGE_PUBLISHED)
        self.check_object_permissions(request, article)
        return article
