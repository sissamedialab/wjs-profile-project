"""Views related to typesetting/production."""

from django.contrib.auth import get_user_model
from django.contrib.auth.mixins import LoginRequiredMixin, UserPassesTestMixin
from django.views.generic import ListView

from .models import ArticleWorkflow

Account = get_user_model()


# https://gitlab.sissamedialab.it/wjs/specs/-/issues/665 - pile
class TypesetterPending(LoginRequiredMixin, UserPassesTestMixin, ListView):
    """A view showing all paper that a typesetter could take in charge.

    AKA "codone" :)
    """

    # TBV:
    model = ArticleWorkflow
    ordering = "id"
    template_name = "wjs_review/typesetter_pending.html"
    context_object_name = "workflows"

    def test_func(self):
        """Allow access only to...."""
        return True

    def get_queryset(self):
        """..."""
        return ArticleWorkflow.objects.filter(
            article__journal=self.request.journal,
            state__in=ArticleWorkflow.ReviewStates.READY_FOR_TYPESETTER,
        )


# https://gitlab.sissamedialab.it/wjs/specs/-/issues/665 - my list / "in lavorazione"
class TypesetterWorkingOn(LoginRequiredMixin, UserPassesTestMixin, ListView):
    """A view showing all papers assigned to a typesetter not yet published."""

    # TBV: might be sufficient to filter by states TYPESETTER_SELECTED or PROOFREADING


class TypesetterArchived(LoginRequiredMixin, UserPassesTestMixin, ListView):
    """A view showing all past papers of a typesetter."""
