"""Check that the global-request isolation fixture also applies to wjs_review tests (wjs-profile-project#302)."""


def test_clear_global_request_fixture_is_active_in_wjs_review(request):
    """The autouse fixture from the jcom_profile conftest is inherited by wjs_review tests."""
    assert "clear_global_request" in request.fixturenames, "clear_global_request fixture is not active"
