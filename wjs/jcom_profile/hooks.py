"""Hooks."""
from django.template.loader import render_to_string


def prova_hook(request_context):
    """Test hooks."""
    # import ipdb; ipdb.set_trace()
    theme = request_context.request.press.theme
    journal = request_context.request.journal
    if journal is not None:
        theme = journal.get_setting('general', 'journal_theme')
    template_name = f"{theme}/field.html"
    context = dict(form=request_context.get("form"), journal_settings=request_context.get("journal_settings"))
    rendered = render_to_string(template_name, context)
    return rendered
