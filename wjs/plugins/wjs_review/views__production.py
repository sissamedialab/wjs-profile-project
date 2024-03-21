"""Views related to typesetting/production."""

from django.contrib.auth import get_user_model
from django.contrib.auth.mixins import LoginRequiredMixin, UserPassesTestMixin
from django.views.generic import ListView
from journal.models import Journal

from wjs.jcom_profile import permissions as base_permissions

from .models import ArticleWorkflow

Account = get_user_model()


class TypesetterPending(LoginRequiredMixin, UserPassesTestMixin, ListView):
    """A view showing all paper that a typesetter could take in charge.

    AKA "codone" :)
    """

    model = ArticleWorkflow
    template_name = "wjs_review/typesetter_pending.html"
    context_object_name = "workflows"

    def test_func(self):
        """Allow access to typesetters and EO."""
        return base_permissions.has_typesetter_role_on_any_journal(self.request.user) or base_permissions.has_eo_role(
            self.request.user,
        )

    def get_queryset(self):
        """List articles ready for typesetter for each journal that the user is typesetter of.

        List all articles ready for typesetter if the user is EO.
        """
        base_qs = ArticleWorkflow.objects.filter(
            state__in=[ArticleWorkflow.ReviewStates.READY_FOR_TYPESETTER],
        ).order_by("-article__date_accepted")

        if base_permissions.has_eo_role(self.request.user):
            return base_qs
        else:
            typesetter_role_slug = "typesetter"
            journals_for_which_user_is_typesetter = Journal.objects.filter(
                accountrole__role__slug=typesetter_role_slug,
                accountrole__user__id=self.request.user.id,
            ).values_list("id", flat=True)
            return base_qs.filter(article__journal__in=journals_for_which_user_is_typesetter)


class TypesetterWorkingOn(LoginRequiredMixin, UserPassesTestMixin, ListView):
    """A view showing all papers that a certain typesetter is working on."""

    model = ArticleWorkflow
    template_name = "wjs_review/typesetter_pending.html"
    context_object_name = "workflows"

    def test_func(self):
        """Allow access to typesetters and EO."""
        return base_permissions.has_typesetter_role_on_any_journal(self.request.user)

    def get_queryset(self):
        """List articles assigned to the user and still open."""
        qs = ArticleWorkflow.objects.filter(
            state__in=[
                ArticleWorkflow.ReviewStates.TYPESETTER_SELECTED,
                ArticleWorkflow.ReviewStates.PROOFREADING,
            ],
            article__typesettinground__isnull=False,
            article__typesettinground__typesettingassignment__typesetter__pk=self.request.user.pk,
            article__typesettinground__typesettingassignment__completed__isnull=True,
        ).order_by("-article__date_accepted")

        return qs


class TypesetterArchived(LoginRequiredMixin, UserPassesTestMixin, ListView):
    """A view showing all past papers of a typesetter."""
