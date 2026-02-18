from rest_framework.permissions import BasePermission

from wjs.jcom_profile import permissions as base_permissions

from ..permissions import is_article_typesetter_or_eo


class IsEOOrTypesetterForArticle(BasePermission):
    """
    Requester must be EO or a Typesetter for the given article.
    """

    def has_object_permission(self, request, view, obj):
        user = request.user
        if not user or not user.is_authenticated:
            return False

        article_workflow = getattr(obj, "articleworkflow", None)
        if not article_workflow:
            return False

        return is_article_typesetter_or_eo(article_workflow, user)

    def has_permission(self, request, view):
        user = request.user
        if not user or not user.is_authenticated:
            return False

        return base_permissions.has_typesetter_role_on_any_journal(user) or base_permissions.has_eo_role(user)
