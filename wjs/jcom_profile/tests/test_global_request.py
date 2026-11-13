"""Tests for the isolation of the global request between tests (wjs-profile-project#302)."""

import pytest
from core.middleware import GlobalRequestMiddleware

from wjs.jcom_profile.tests.global_request import isolated_global_request


@pytest.mark.django_db
def test_isolated_global_request_clears_and_restores(fake_request, rf):
    """The helper clears the global request inside the block and restores the previous one on exit."""
    GlobalRequestMiddleware.process_request(fake_request)
    assert GlobalRequestMiddleware.get_current_request() is fake_request, "Precondition: request not set"

    with isolated_global_request():
        assert GlobalRequestMiddleware.get_current_request() is None, "Global request not cleared inside the block"
        inner = rf.get("/")
        GlobalRequestMiddleware.process_request(inner)
        assert GlobalRequestMiddleware.get_current_request() is inner, "Request set inside the block not visible"

    assert GlobalRequestMiddleware.get_current_request() is fake_request, "Previous global request not restored"


@pytest.mark.django_db
def test_isolated_global_request_restores_on_exception(fake_request):
    """The previous global request is restored even when the block raises."""
    GlobalRequestMiddleware.process_request(fake_request)

    with pytest.raises(ValueError):
        with isolated_global_request():
            raise ValueError

    assert GlobalRequestMiddleware.get_current_request() is fake_request, "Global request not restored after error"


def test_clear_global_request_fixture_is_active(request):
    """The autouse fixture resetting the global request is active for jcom_profile tests."""
    assert "clear_global_request" in request.fixturenames, "clear_global_request fixture is not active"
