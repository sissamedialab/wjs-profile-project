from typing import NamedTuple, Optional, Tuple, TypedDict

from django.db import models

from .models import PermissionAssignment


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
