import dataclasses
from datetime import datetime
from typing import Literal, NamedTuple, Optional, Tuple, TypedDict

from django.db import models
from django.utils.safestring import mark_safe
from django.utils.translation import gettext_lazy as _

from .models import PermissionAssignment, WorkflowReviewAssignment

ButtonSize = Literal["small", "medium", "large"]


class BootstrapButtonProps(TypedDict):
    value: str
    "JSON payload for hx_vals attribute."
    css_class: str
    "Button additional CSS class."
    disabled: bool
    "Button disabled state."
    disabled_cause: str
    "Button tooltip (disabled cause)."


class PermissionTargetObject(NamedTuple):
    """
    Object that needs permissions assigned.
    """

    object_type: int
    object: models.Model  # noqa: A003
    round: int  # noqa: A003
    date_reference: datetime
    author_notes: bool = False


class PermissionInitial(TypedDict):
    """Configuration of the initial data for the form."""

    object_type: int
    object_id: int
    object: models.Model  # noqa: A003
    round: int  # noqa: A003
    author_notes: bool
    permission: Optional[str]
    permission_secondary: Optional[str]


PermissionConfiguration = dict[Tuple[int, int], PermissionAssignment.PermissionType]


class ReviewAssignmentActionConfiguration(TypedDict):
    """
    Configuration of the review assignment action.
    """

    assignment: "WorkflowReviewAssignment"
    """Review assignment instance."""
    name: str
    """Action symbolic name."""
    label: str
    """Action public label."""
    tooltip: str
    """Action additional text description."""
    url: str
    """URL of the linked view."""


class ReviewAssignmentStatus(NamedTuple):
    """
    Review assignment status.
    """

    code: str
    """Symbolic code."""

    def label(self) -> str:
        """
        Return the public label.
        """
        labels = {
            "withdrawn": _("Withdrawn"),
            "complete": _("Review completed"),
            "accept": _("Assignment Accepted"),
            "declined": _("Declined"),
            "wait": _("Selected"),
            "late": _("Selected"),
        }
        return labels[self.code]

    def css_class(self) -> str:
        """
        Return the css to decorate the status label.
        """
        classes = {
            "withdrawn": "dot dot--declined",
            "complete": "dot dot--submitted",
            "accept": "dot dot--accepted",
            "declined": "dot dot--declined",
            "wait": "dot dot--pending",
            "late": "dot dot--pending",
        }
        return classes[self.code]


@dataclasses.dataclass
class ReviewAssignmentAttentionCondition:
    """
    Review assignment attention conditions.

    This class is used to define conditions that require attention from the journal staff.
    """

    code: str
    """Symbolic code."""
    message: str
    """Public label."""
    style: str = dataclasses.field(default_factory=str)
    """CSS classes."""
    icon_value: str = dataclasses.field(default_factory=str)
    """Condition icon - Use :py:attr:`icon` property for output."""

    @property
    def icon(self) -> str:
        """
        Return the icon string marked as safe as it may contain HTML.
        """
        return mark_safe(self.icon_value)

    @icon.setter
    def icon(self, value):
        """Set the icon value to the internal attribute."""
        self.icon_value = value


class BreadcrumbItem(NamedTuple):
    """
    Breadcrumb item.
    """

    url: str
    """URL of the breadcrumb item."""
    title: str
    """Title of the breadcrumb item."""
    current: bool = False
    """If breadcrumb is the current view."""
