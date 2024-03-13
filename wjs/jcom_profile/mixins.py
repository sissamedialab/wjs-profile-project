from django.contrib.auth.mixins import UserPassesTestMixin

from wjs.jcom_profile.permissions import has_any_journal_role


class HtmxMixin:
    """Mixin to detect if request is an htmx request."""

    htmx = False

    def dispatch(self, request, *args, **kwargs):
        if "HX-Request" in request.headers and request.headers["HX-Request"]:
            self.htmx = True
        return super().dispatch(request, *args, **kwargs)


class HasJournalRoleMixin(UserPassesTestMixin):
    """
    Mixin to check if user is logged in and as any role in journal.

    This is the lowest level of access check on the journal staff pages.
    """

    def test_func(self):
        try:
            return self.request.user.is_authenticated and has_any_journal_role(self.request.journal, self.request.user)
        except AttributeError:
            return False
