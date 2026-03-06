"""Hooks."""

from django.template.loader import render_to_string
from django.utils.translation import gettext as _
from plugins.wjs_review.models import WjsSection

from . import permissions


def extra_core_fields_hook(request_context):
    """Add hook to render extra profile fields."""
    template_name = "elements/accounts/extra_core_fields.html"
    context = {"form": request_context.get("form"), "journal_settings": request_context.get("journal_settings")}
    rendered = render_to_string(template_name, context)
    return rendered


def extra_edit_profile_parameters_hook(request_context):
    """Add hook to add assignment parameter card."""
    user = request_context.request.user
    journal = request_context.request.journal
    rendered = ""
    if (
        user
        and journal
        and (user.check_role(journal, "section-editor", staff_override=False) or permissions.has_eo_role(user))
    ):
        template_name = "elements/accounts/extra_edit_profile_card_block.html"
        rendered = render_to_string(
            template_name,
            {
                "card_title": _("Edit assignment parameters"),
                "card_paragraph": _("Go to your your assignment parameters by clicking the button below."),
                "url_name": _("assignment_parameters"),
                "button_text": _("Assignment parameters"),
            },
        )
    return rendered


def extra_edit_subscription_hook(request_context):
    """Add hook to add newsletters card."""
    template_name = "elements/accounts/extra_edit_profile_card_block.html"
    rendered = render_to_string(
        template_name,
        {
            "card_title": _("Newsletters"),
            "card_paragraph": _("Edit your subscription settings by clicking the button below."),
            "url_name": _("edit_newsletters"),
            "button_text": _("Edit my subscription"),
        },
    )
    return rendered


def wjs_section_information(request_context):
    """
    Retrieve and render the information about sections for a specific journal from the request context.

    :param request_context: Dictionary containing request and journal context data.
    :type request_context: dict
    :return: Rendered HTML string for the sections information template.
    :rtype: str
    :raises KeyError: If "request" key is not found in request_context.
    """
    request = request_context["request"]
    sections = WjsSection.objects.filter(journal=request.journal)
    context = {"sections": sections, "default_section": 0}
    template_name = "wjs_review/templatetags/sections_info.html"
    rendered = render_to_string(
        template_name,
        context,
    )
    return rendered
