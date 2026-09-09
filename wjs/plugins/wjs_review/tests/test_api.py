"""Tests related to wjs_review API."""

import json

import pytest
from django.test.client import Client
from django.urls import reverse
from django.utils import timezone
from plugins.wjs_review.api.const import COLLABORATIONS_EXPORT_KEYS
from plugins.wjs_submission.helpers.collaborations import TABELLONE_FIELDS
from plugins.wjs_submission.models import Collaboration
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


@pytest.fixture()
def collaborations() -> list[Collaboration]:
    """Create three collaborations, on purpose not in alphabetical order."""
    return [
        Collaboration.objects.create(
            name="CMS collaboration",
            short_name="CMS",
            institutional_email="cms@cern.ch",
            logo_name="CMS-collaboration-logo",
            logo_size="width=1.8cm",
            moretex="\\paperNote{Note}",
            author_list_mode=Collaboration.AuthorListMode.EMPTY,
            collaboration_list_mode=Collaboration.CollaborationListMode.FORTHE,
            notes="Some note with a ⚠",
            cluster=True,
            sample_papers="JCAP_114P_0326",
            public_listing=True,
        ),
        Collaboration.objects.create(name="ATLAS collaboration", short_name="ATLAS", public_listing=False),
        Collaboration.objects.create(name="Belle II collaboration", short_name="Belle II", public_listing=True),
    ]


def _download(client: Client, token: str, **params) -> dict:
    """Call the collaborations entry point and return the JSON it serves."""
    response = client.get(reverse("collaborations"), data=params, HTTP_AUTHORIZATION=f"Token {token}")
    assert response.status_code == 200, response.content
    return json.loads(response.content)


@pytest.mark.skipif("not config.getoption('--run-collaborations-api')", reason="overkill")
@pytest.mark.django_db
def test_api_collaborations_is_protected(client: Client, eo_user: JCOMProfile, typesetter, normal_user):
    """Only EO members and typesetters with a valid token can download the collaborations."""
    url = reverse("collaborations")
    Token.objects.create(user=eo_user.janeway_account, key="EOTOKEN")
    Token.objects.create(user=typesetter.janeway_account, key="TYPESETTERTOKEN")
    Token.objects.create(user=normal_user.janeway_account, key="NORMALTOKEN")

    assert client.get(url).status_code == 401
    assert client.get(url, HTTP_AUTHORIZATION="Token WRONGTOKEN").status_code == 401
    assert client.get(url, HTTP_AUTHORIZATION="Token NORMALTOKEN").status_code == 403
    assert client.get(url, HTTP_AUTHORIZATION="Token EOTOKEN").status_code == 200
    assert client.get(url, HTTP_AUTHORIZATION="Token TYPESETTERTOKEN").status_code == 200


@pytest.mark.skipif("not config.getoption('--run-collaborations-api')", reason="overkill")
@pytest.mark.django_db
def test_api_collaborations_is_logged(client: Client, caplog, eo_user: JCOMProfile):
    """Calls to the collaborations entry point are logged like the other ones."""
    Token.objects.create(user=eo_user.janeway_account, key="EOTOKEN")
    url = reverse("collaborations")

    client.get(url, HTTP_AUTHORIZATION="Token EOTOKEN", REMOTE_ADDR="127.0.0.1")

    log_line = _api_request_log_line(caplog)
    assert "GET" in log_line
    assert url in log_line
    assert "127.0.0.1" in log_line
    assert str(eo_user.janeway_account.pk) in log_line


@pytest.mark.skipif("not config.getoption('--run-collaborations-api')", reason="overkill")
@pytest.mark.django_db
def test_api_collaborations_is_served_as_json(client: Client, eo_user: JCOMProfile, collaborations):
    """The entry point serves a JSON response; the client chooses how it is rendered."""
    Token.objects.create(user=eo_user.janeway_account, key="EOTOKEN")

    response = client.get(reverse("collaborations"), HTTP_AUTHORIZATION="Token EOTOKEN")

    assert response.status_code == 200
    assert response["Content-Type"] == "application/json"
    assert "Content-Disposition" not in response
    content = response.content.decode()
    # non-ascii is written as-is, as in tabellone.json
    assert "⚠" in content


@pytest.mark.skipif("not config.getoption('--run-collaborations-api')", reason="overkill")
@pytest.mark.django_db
def test_api_collaborations_can_be_pretty_printed_on_request(client: Client, eo_user: JCOMProfile, collaborations):
    """A client that asks for an indented rendering gets a "tabellone.json"-like file."""
    Token.objects.create(user=eo_user.janeway_account, key="EOTOKEN")

    response = client.get(
        reverse("collaborations"),
        HTTP_AUTHORIZATION="Token EOTOKEN",
        HTTP_ACCEPT="application/json; indent=4",
    )

    assert response.status_code == 200
    content = response.content.decode()
    assert content.startswith("{\n    ")
    assert '\n            "short_name":' in content


@pytest.mark.skipif("not config.getoption('--run-collaborations-api')", reason="overkill")
@pytest.mark.django_db
def test_api_collaborations_are_ordered_by_short_name(client: Client, eo_user: JCOMProfile, collaborations):
    Token.objects.create(user=eo_user.janeway_account, key="EOTOKEN")

    data = _download(client, "EOTOKEN")

    assert [record["short_name"] for record in data["collaborations"]] == ["ATLAS", "Belle II", "CMS"]


@pytest.mark.skipif("not config.getoption('--run-collaborations-api')", reason="overkill")
@pytest.mark.django_db
def test_api_collaborations_are_ordered_case_insensitively(client: Client, eo_user: JCOMProfile):
    """
    Collaborations whose short name starts with a lowercase letter are not pushed to the end.

    The database collation is byte-ordered, so a plain ORDER BY would sort "nEXO" after "ZEUS".
    """
    Token.objects.create(user=eo_user.janeway_account, key="EOTOKEN")
    for short_name in ("ZEUS", "nEXO", "ATLAS", "sPHENIX"):
        Collaboration.objects.create(name=f"{short_name} collaboration", short_name=short_name)

    data = _download(client, "EOTOKEN")

    assert [record["short_name"] for record in data["collaborations"]] == ["ATLAS", "nEXO", "sPHENIX", "ZEUS"]


@pytest.mark.skipif("not config.getoption('--run-collaborations-api')", reason="overkill")
@pytest.mark.django_db
def test_api_collaborations_record_looks_like_tabellone(client: Client, eo_user: JCOMProfile, collaborations):
    """Every field of the import map is exported, with tabellone.json's keys and order."""
    Token.objects.create(user=eo_user.janeway_account, key="EOTOKEN")

    data = _download(client, "EOTOKEN")

    assert data["version"] == 1.0
    record = next(r for r in data["collaborations"] if r["short_name"] == "CMS")
    assert list(record) == list(COLLABORATIONS_EXPORT_KEYS)
    assert set(TABELLONE_FIELDS).issubset(record), "every imported key must be exported too"
    assert "public_listing" not in record, "the public listing flag is a filter, not exported data"
    assert record == {
        "short_name": "CMS",
        "full_name": "CMS collaboration",
        "authorList": "empty",
        "collaborationList": "forthe",
        "moretex": "\\paperNote{Note}",
        "email": "cms@cern.ch",
        "logo": "CMS-collaboration-logo",
        "logoSize": "width=1.8cm",
        "notes": "Some note with a ⚠",
        "cluster": True,
        "sample_papers": "JCAP_114P_0326",
    }


@pytest.mark.skipif("not config.getoption('--run-collaborations-api')", reason="overkill")
@pytest.mark.django_db
@pytest.mark.parametrize(
    ("params", "expected"),
    [
        ({}, ["ATLAS collaboration", "Belle II collaboration", "CMS collaboration"]),
        ({"public_listing": "all"}, ["ATLAS collaboration", "Belle II collaboration", "CMS collaboration"]),
        ({"public_listing": "true"}, ["Belle II collaboration", "CMS collaboration"]),
        ({"public_listing": "false"}, ["ATLAS collaboration"]),
        ({"public_listing": "TRUE"}, ["Belle II collaboration", "CMS collaboration"]),
    ],
)
def test_api_collaborations_public_listing_filter(
    client: Client, eo_user: JCOMProfile, collaborations, params, expected
):
    Token.objects.create(user=eo_user.janeway_account, key="EOTOKEN")

    data = _download(client, "EOTOKEN", **params)

    assert [record["full_name"] for record in data["collaborations"]] == expected


@pytest.mark.skipif("not config.getoption('--run-collaborations-api')", reason="overkill")
@pytest.mark.django_db
def test_api_collaborations_rejects_an_unknown_public_listing(client: Client, eo_user: JCOMProfile, collaborations):
    Token.objects.create(user=eo_user.janeway_account, key="EOTOKEN")

    response = client.get(
        reverse("collaborations"), data={"public_listing": "maybe"}, HTTP_AUTHORIZATION="Token EOTOKEN"
    )

    assert response.status_code == 400
    error = response.json()["error"]
    assert error["code"] == "BAD_REQUEST"
    assert error["details"]["public_listing"]["expected"] == ["all", "false", "true"]
    assert error["details"]["public_listing"]["got"] == "maybe"
