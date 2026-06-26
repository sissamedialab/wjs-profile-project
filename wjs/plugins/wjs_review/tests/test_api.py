"""Tests related to wjs_review API."""

import pytest
from django.test.client import Client
from django.urls import reverse
from django.utils import timezone
from rest_framework.authtoken.models import Token
from submission.models import STAGE_PUBLISHED, Article

from wjs.jcom_profile.models import JCOMProfile


def _api_request_log_line(caplog) -> str:
    """Return the single "API request" log line captured so far."""
    lines = [record.getMessage() for record in caplog.records if "API request" in record.getMessage()]
    assert len(lines) == 1, f"Expected exactly one API request log line, got {lines}"
    return lines[0]


@pytest.mark.django_db
def test_api_articlegalleys_logged(client: Client, caplog, article: Article, eo_user: JCOMProfile):
    """Test that calls to API article-galleys entry point are logged."""
    article.date_published = timezone.now()
    article.stage = STAGE_PUBLISHED
    article.save()

    account = eo_user.janeway_account
    Token.objects.create(user=account, key="GOODTOKEN")

    view_name = "article-galleys"
    url = reverse(view_name, args=(article.pk,))
    remote_addr = "127.0.0.1"

    # 1. No authentication token: 401, logged as anonymous.
    response = client.get(url, REMOTE_ADDR=remote_addr)
    assert response.status_code == 401
    log_line = _api_request_log_line(caplog)
    assert "GET" in log_line
    assert url in log_line
    assert remote_addr in log_line
    assert "GOODTOKEN" not in log_line
    assert "anonymous" in log_line.lower()
    caplog.clear()

    # 2. Wrong token: 401, logged as anonymous.
    response = client.get(url, HTTP_AUTHORIZATION="Token WRONGTOKEN", REMOTE_ADDR=remote_addr)
    assert response.status_code == 401
    log_line = _api_request_log_line(caplog)
    assert "GET" in log_line
    assert url in log_line
    assert remote_addr in log_line
    assert "WRONGTOKEN" not in log_line
    assert "anonymous" in log_line.lower()
    caplog.clear()

    # 3. Correct token: 200, logged with the authenticated user.
    response = client.get(url, HTTP_AUTHORIZATION="Token GOODTOKEN", REMOTE_ADDR=remote_addr)
    assert response.status_code == 200
    log_line = _api_request_log_line(caplog)
    assert "GET" in log_line
    assert url in log_line
    assert remote_addr in log_line
    assert "GOODTOKEN" not in log_line
    assert str(account.pk) in log_line
    caplog.clear()

    # 4. Correct token but wrong article pk: 404, still logged with the user.
    wrong_url = reverse(view_name, args=(article.pk + 1000,))
    response = client.get(wrong_url, HTTP_AUTHORIZATION="Token GOODTOKEN", REMOTE_ADDR=remote_addr)
    assert response.status_code == 404
    log_line = _api_request_log_line(caplog)
    assert "GET" in log_line
    assert wrong_url in log_line
    assert remote_addr in log_line
    assert "GOODTOKEN" not in log_line
    assert str(account.pk) in log_line
    caplog.clear()
