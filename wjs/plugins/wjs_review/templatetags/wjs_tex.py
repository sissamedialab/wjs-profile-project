from django import template

register = template.Library()


@register.filter
def space_to_tilde(string: str) -> str:
    """Replace spaces with tildes; useful for names and similar."""
    return string.replace(" ", "~")
