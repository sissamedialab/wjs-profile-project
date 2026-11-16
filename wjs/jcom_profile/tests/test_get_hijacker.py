"""Tests for :py:func:`wjs.jcom_profile.permissions.get_hijacker`."""

import logging

import pytest
from core.middleware import GlobalRequestMiddleware
from core.models import Account
from django.contrib.auth import login
from django.core.handlers.base import BaseHandler
from hijack.middleware import HijackUserMiddleware

from wjs.jcom_profile.permissions import get_hijacker


@pytest.mark.django_db
def test_get_hijacker_missing_hijacker_account(fake_request, normal_user, caplog):
    """A hijacked request whose hijacker account no longer exists gives None and a warning (see #302)."""
    missing_pk = (Account.objects.order_by("-pk").values_list("pk", flat=True).first() or 0) + 1000
    fake_request.session["hijack_history"] = [str(missing_pk)]
    login(fake_request, normal_user)
    GlobalRequestMiddleware.process_request(fake_request)
    HijackUserMiddleware(BaseHandler.get_response).process_request(fake_request)
    assert fake_request.user.is_hijacked, "Precondition: request is not hijacked"

    with caplog.at_level(logging.WARNING, logger="wjs.jcom_profile.permissions"):
        hijacker = get_hijacker()

    assert hijacker is None, "A missing hijacker account must give None"
    warnings = [
        record
        for record in caplog.records
        if record.name == "wjs.jcom_profile.permissions" and record.levelno == logging.WARNING
    ]
    assert len(warnings) == 1, "Expected exactly one warning about the missing hijacker account"
    assert str(missing_pk) in warnings[0].getMessage(), "The warning must name the missing hijacker pk"


@pytest.mark.django_db
def test_get_hijacker_existing_hijacker_account_logs_no_warning(fake_request, normal_user, eo_user, caplog):
    """A hijacked request whose hijacker exists returns that account and logs no warning."""
    fake_request.session["hijack_history"] = [str(eo_user.pk)]
    login(fake_request, normal_user)
    GlobalRequestMiddleware.process_request(fake_request)
    HijackUserMiddleware(BaseHandler.get_response).process_request(fake_request)
    assert fake_request.user.is_hijacked, "Precondition: request is not hijacked"

    with caplog.at_level(logging.WARNING, logger="wjs.jcom_profile.permissions"):
        hijacker = get_hijacker()

    assert hijacker is not None and hijacker.pk == eo_user.pk, "The existing hijacker account must be returned"
    assert not [
        record
        for record in caplog.records
        if record.name == "wjs.jcom_profile.permissions" and record.levelno == logging.WARNING
    ], "No warning expected when the hijacker account exists"
