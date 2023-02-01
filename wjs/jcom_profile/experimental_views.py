"""Experimental views."""
from django.utils import timezone
from django.views.generic import TemplateView
from journal.models import Issue, IssueType


class IssuesForceGraph(TemplateView):
    """Display issues with DS3.js ForceGraph."""

    template_name = "experimental/journal/issues.html"

    # TODO: how do I apply Janeway's function decorators to class-based views?
    # @has_journal       from security.decorators
    # @frontend_enabled  from journal.decorators
    def get_context_data(self, **kwargs):
        """Get the list of issues.

        Same as journal.views.issues
        """
        context = super().get_context_data(**kwargs)
        issue_type = IssueType.objects.get(
            code="issue",
            journal=self.request.journal,
        )
        issue_objects = Issue.objects.filter(
            journal=self.request.journal,
            issue_type=issue_type,
            date__lte=timezone.now(),
        )
        context = {
            "issues": issue_objects,
            "issue_type": issue_type,
        }
        return context
