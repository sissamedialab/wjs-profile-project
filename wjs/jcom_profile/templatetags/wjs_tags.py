"""WJS tags."""
from django import template
from wjs.jcom_profile.models import SpecialIssue

register = template.Library()


@register.simple_tag
def journal_has_open_si(journal):
    """Return true if this journal has any special issue open for submission."""
    # The timeline.html template should show/hide the SI step as
    # necessary.
    has_open_si = SpecialIssue.objects.filter(is_open_for_submission=True).exists()
    return has_open_si
