from typing import TypedDict


class BootstrapButtonProps(TypedDict):
    value: str
    "JSON payload for hx_vals attribute."
    css_class: str
    "Button additional CSS class."
    disabled: bool
    "Button disabled state."
