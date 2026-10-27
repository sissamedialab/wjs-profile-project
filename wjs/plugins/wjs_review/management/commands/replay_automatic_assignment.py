"""Shim: the replay command lives in the ``replay`` subpackage, remove it together with that package."""

from ...replay.command import Command  # noqa: F401
