from django.urls import reverse
from django.views.generic import RedirectView

from ..mixins import HasJournalRoleMixin
from .logic import user_primary_role_page


class RedirectDashboard(RedirectView):
    """Redirect janeway's dashboard to the public home page."""

    def get_redirect_url(self, *args, **kwargs):
        return reverse("website_index")


class RedirectMyPages(HasJournalRoleMixin, RedirectView):
    """Redirect janeway's dashboard to wjs_review my*pages according to the main role."""

    def get_redirect_url(self, *args, **kwargs):
        return user_primary_role_page(self.request.journal, self.request.user)


class RedirectArticleStatus(HasJournalRoleMixin, RedirectView):
    """Redirect janeway's article status page to wjs_review article details page."""

    def get_redirect_url(self, *args, **kwargs):
        return reverse("wjs_article_details", kwargs={"pk": self.kwargs["article_id"]})
