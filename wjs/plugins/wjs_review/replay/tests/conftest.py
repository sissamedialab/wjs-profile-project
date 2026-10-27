"""Reuse the wjs_review test fixtures for the replay tests."""

from ...tests import conftest as _review_conftest
from ...tests.conftest import *  # noqa

# The parent conftest registers the --run-academic option: registering it twice is an error.
del pytest_addoption  # noqa: F821
del _review_conftest
