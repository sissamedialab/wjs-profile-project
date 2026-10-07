"""Tests related to wjs_review API."""

import datetime
import io
import json
import zipfile
from pathlib import Path
from unittest import mock

import pytest
import requests
from core.models import File, Galley
from django.core.cache import cache as django_cache
from django.http import HttpRequest
from django.test import override_settings
from django.test.client import Client
from django.urls import reverse
from django.utils import timezone
from identifiers.models import Identifier
from plugins.wjs_review.api.const import (
    COLLABORATIONS_EXPORT_KEYS,
    GALLEY_DOWNLOAD_MEDIA_TYPES,
    GALLEY_UPLOAD_MEDIA_TYPES,
    SOURCE_ZIP_MEDIA_TYPES,
    ZIP_MEDIA_TYPE,
)
from plugins.wjs_submission.helpers.collaborations import TABELLONE_FIELDS
from plugins.wjs_submission.models import Collaboration
from rest_framework.authtoken.models import Token
from submission.models import STAGE_PUBLISHED, Article, Section
from typesetting.models import TypesettingAssignment, TypesettingRound

from wjs.jcom_profile.models import JCOMProfile

from ..logic__production import BeginPublication, FinishPublication
from ..models import ArticleWorkflow
from .test_helpers import ThreadedHTTPServer


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


def _create_article_for_api_tests(author, journal, sections, title="Title"):
    """Create a plain article in the given journal (see conftest._article, minus unneeded files)."""
    return Article.objects.create(
        abstract="Abstract",
        journal=journal,
        title=title,
        correspondence_author=author,
        owner=author,
        section=sections[0],
        language="eng",
    )


@pytest.mark.django_db
class TestG8TypesetterPapersMonitoring:
    """G8 - GET /journal/<code>/typesetter/<typesetter_pk>/papers/ (Specifications.md §3.7)."""

    # Reference window; timestamps are midnight UTC on each day, i.e. exactly at
    # the edges of the inclusive datetime range the server builds out of
    # [start_date 00:00 UTC, end_date 23:59:59 UTC].
    START = "2026-08-02"
    END = "2026-08-10"
    START_DT = datetime.datetime(2026, 8, 2, 0, 0, tzinfo=datetime.timezone.utc)
    END_DT = datetime.datetime(2026, 8, 10, 0, 0, tzinfo=datetime.timezone.utc)
    MIDDLE_DT = datetime.datetime(2026, 8, 5, 0, 0, tzinfo=datetime.timezone.utc)
    BEFORE_DT = datetime.datetime(2026, 8, 1, 0, 0, tzinfo=datetime.timezone.utc)
    AFTER_DT = datetime.datetime(2026, 8, 11, 0, 0, tzinfo=datetime.timezone.utc)
    OTHER_JOURNAL_DT = datetime.datetime(2026, 8, 3, 0, 0, tzinfo=datetime.timezone.utc)
    GOOD_TOKEN = "GOODTOKEN"  # same convention as test_api_articlegalleys_logged above

    def _url(self, journal, typesetter_pk):
        return reverse("typesetter-papers", args=(journal.code, typesetter_pk))

    def _auth(self, user, key=None):
        """Create an auth token for the user and return the HTTP_AUTHORIZATION value.

        Pass an explicit key only when the same test creates tokens for several
        users (Token.key is unique).
        """
        account = user.janeway_account if isinstance(user, JCOMProfile) else user
        token, _ = Token.objects.get_or_create(user=account, defaults={"key": key or self.GOOD_TOKEN})
        return f"Token {token.key}"

    def _get(self, client, journal, typesetter_pk, auth, params=None):
        return client.get(
            self._url(journal, typesetter_pk),
            data=params if params is not None else {"start_date": self.START, "end_date": self.END},
            HTTP_AUTHORIZATION=auth,
        )

    def _assign_to_typesetter(self, article, typesetter_account, state, assigned, date_accepted=None):
        """Create a typesetting assignment and force the workflow state.

        assigned: TypesettingAssignment.assigned drives the G8 date window, so it is set
        explicitly here. date_accepted exists for the response field: plain DateTimeField
        set with a direct update so that auto_now fields (ArticleWorkflow.modified/created)
        do not interfere.
        """
        if date_accepted is not None:
            Article.objects.filter(pk=article.pk).update(date_accepted=date_accepted)
        typesetting_round = TypesettingRound.objects.create(article=article, round_number=1)
        TypesettingAssignment.objects.create(round=typesetting_round, typesetter=typesetter_account, assigned=assigned)
        workflow = article.articleworkflow
        workflow.state = state
        workflow.save()
        article.refresh_from_db()
        return typesetting_round

    @pytest.fixture
    def monitoring_scenario(self, journal, author, sections, typesetter):
        """One typesetter with papers inside/outside the window (Specifications.md §3.7)."""
        typesetter_account = typesetter.janeway_account
        other_typesetter = JCOMProfile.objects.create(
            username="typesetter2",
            email="typesetter2@example.com",
            first_name="T2",
            last_name="T2",
            is_active=True,
            gdpr_checkbox=True,
        )
        states = ArticleWorkflow.ReviewStates
        by_title = {}

        def _make(
            title, assigned, state, typesetter_acct=typesetter_account, doi=None, published=False, accepted=None
        ):
            article = _create_article_for_api_tests(author, journal, sections, title=title)
            if published:
                article.stage = STAGE_PUBLISHED
                article.save()
            if doi is not None:
                Identifier.objects.create(article=article, id_type="doi", identifier=doi)
            if published:
                Identifier.objects.create(
                    article=article, id_type="pubid", identifier=f"{journal.code}XX(2025)YY{article.pk}", enabled=True
                )
            self._assign_to_typesetter(
                article, typesetter_acct, state=state, assigned=assigned, date_accepted=accepted or self.MIDDLE_DT
            )
            by_title[title] = article

        # happy path + inclusive boundaries: assigned 8/2, 8/5, 8/10 → all listed
        _make("W0", self.START_DT, states.PROOFREADING, accepted=self.START_DT)
        _make("W1", self.MIDDLE_DT, states.TYPESETTER_SELECTED)
        _make("W2", self.END_DT, states.PROOFREADING, accepted=self.END_DT)
        # assigned in the window but accepted before it → still listed (the window
        # tracks when the typesetter was given the job, not when it was accepted)
        _make("WA", self.MIDDLE_DT, states.TYPESETTER_SELECTED, accepted=self.BEFORE_DT)
        # published paper (with DOI) in the window → listed
        _make("WP", self.MIDDLE_DT, states.PUBLISHED, doi="10.1234/g8-test", published=True)

        # Excluded: assigned outside the window (before/after), assigned to another
        # typesetter, never assigned to this typesetter, not in a monitored state.
        _make("XB", self.BEFORE_DT, states.TYPESETTER_SELECTED)
        _make("XA", self.AFTER_DT, states.TYPESETTER_SELECTED)
        _make(
            "XO", self.OTHER_JOURNAL_DT, states.TYPESETTER_SELECTED, typesetter_acct=other_typesetter.janeway_account
        )
        _make("XN", self.OTHER_JOURNAL_DT, states.TYPESETTER_SELECTED)  # assignment deleted right away
        by_title["XN"].typesettinground_set.first().delete()  # no TypesettingAssignment left → excluded
        _make("XR", self.OTHER_JOURNAL_DT, states.READY_FOR_TYPESETTER)  # not yet taken in charge
        _make("XC", self.OTHER_JOURNAL_DT, states.ACCEPTED)  # pre-assignment state

        return {
            "typesetter_account": typesetter_account,
            "paper": by_title["W1"],
            "published": by_title["WP"],
            "by_title": by_title,
        }

    def test_happy_path(self, client: Client, journal, eo_user, monitoring_scenario, typesetter):
        """Papers assigned to the typesetter in the window are listed, boundaries included (on the assignment date)."""
        # any typesetter can read the stats (§3.7 - reuse of the existing auth)
        for user, key in ((eo_user, None), (typesetter, f"{self.GOOD_TOKEN}-typesetter")):
            response = self._get(
                client, journal, monitoring_scenario["typesetter_account"].pk, auth=self._auth(user, key=key)
            )

            assert response.status_code == 200
            items = response.json()
            # ordered by -date_accepted (the response field); the 8/5 group is unordered.
            # W2(8/10) > W1,WP (8/5) > W0(8/2) > WA(8/1, listed: assigned in the window).
            got = [item["preprint_id"] for item in items]
            expected = [
                f"{journal.code}_{monitoring_scenario['by_title'][t].pk}" for t in ("W2", "W1", "WA", "WP", "W0")
            ]
            assert got[0] == expected[0]  # W2
            assert set(got[1:3]) == {expected[1], expected[3]}  # W1 / WP
            assert got[3] == expected[4]  # W0
            assert got[4] == expected[2]  # WA

    @pytest.mark.parametrize("title", ["XB", "XA", "XO", "XN", "XR", "XC"])
    def test_excluded_papers(self, client: Client, journal, eo_user, monitoring_scenario, title):
        """Out-of-window or not-managed-by-this-typesetter papers are not listed."""
        response = self._get(client, journal, monitoring_scenario["typesetter_account"].pk, auth=self._auth(eo_user))

        assert response.status_code == 200
        preprint_ids = [item["preprint_id"] for item in response.json()]
        article = monitoring_scenario["by_title"][title]
        assert f"{journal.code}_{article.pk}" not in preprint_ids

    def test_response_fields(self, client: Client, journal, eo_user, monitoring_scenario):
        """Each item carries paper identifiers, acceptance date, status and last status change."""
        scenario = monitoring_scenario
        paper = scenario["paper"]

        response = self._get(client, journal, scenario["typesetter_account"].pk, auth=self._auth(eo_user))
        assert response.status_code == 200
        item = next(i for i in response.json() if i["preprint_id"] == f"{journal.code}_{paper.pk}")

        assert set(item) == {
            "preprint_id",
            "published_id",
            "doi",
            "date_accepted",
            "status",
            "last_status_change",
            "date_taken_in_charge",
        }
        assert item["preprint_id"] == f"{journal.code}_{paper.pk}"
        assert item["published_id"] == ""  # empty until publication
        assert item["doi"] is None
        # Rendered in the server TIME_ZONE; compare the instant, not the string.
        assert datetime.datetime.fromisoformat(item["date_accepted"]) == paper.date_accepted
        # The "name" is the computed state label (ArticleWorkflow.state_label). With no
        # typesetting round uploaded yet, W1 still renders its raw label; the TiC vs
        # "Back to typesetter" distinction arrives automatically with specs#3120.
        # TODO(specs#3120): expect "Taken in charge" here once the computed state exists.
        assert item["status"] == {
            "code": ArticleWorkflow.ReviewStates.TYPESETTER_SELECTED,
            "name": str(ArticleWorkflow.ReviewStates.TYPESETTER_SELECTED.label),
        }
        assert datetime.datetime.fromisoformat(item["date_taken_in_charge"]) == self.MIDDLE_DT
        workflow = ArticleWorkflow.objects.get(pk=paper.pk)
        assert datetime.datetime.fromisoformat(item["last_status_change"]) == workflow.modified

        published_item = next(
            i for i in response.json() if i["preprint_id"] == f"{journal.code}_{scenario['published'].pk}"
        )
        assert published_item["published_id"] == f"{journal.code}XX(2025)YY{scenario['published'].pk}"
        assert published_item["doi"] == "10.1234/g8-test"
        assert published_item["status"]["code"] == ArticleWorkflow.ReviewStates.PUBLISHED

    def test_multiple_rounds_yield_a_single_row(self, client: Client, journal, eo_user, monitoring_scenario):
        """A paper sent back to the typesetter (new TypesettingRound) appears only once."""
        scenario = monitoring_scenario
        paper = scenario["paper"]
        second_round = TypesettingRound.objects.create(article=paper, round_number=2)
        TypesettingAssignment.objects.create(round=second_round, typesetter=scenario["typesetter_account"])

        response = self._get(client, journal, scenario["typesetter_account"].pk, auth=self._auth(eo_user))
        assert response.status_code == 200
        assert [i["preprint_id"] for i in response.json()].count(f"{journal.code}_{paper.pk}") == 1
        second_round.delete()  # leave the shared scenario pristine for other tests

    def test_date_taken_in_charge_is_scoped_to_the_url_typesetter(
        self, client: Client, journal, eo_user, monitoring_scenario
    ):
        """date_taken_in_charge reports the URL typesetter's earliest assignment, others excluded (thread 6)."""
        scenario = monitoring_scenario
        paper = scenario["paper"]  # W1: assigned to our typesetter in the fixture at MIDDLE_DT

        # Another typesetter touched this paper earlier (round 0), and our typesetter again later
        # (round 2): the earliest date for OUR typesetter is still the fixture's MIDDLE_DT.
        other_typesetter = JCOMProfile.objects.create(
            username="typesetter3",
            email="typesetter3@example.com",
            first_name="T3",
            last_name="T3",
            is_active=True,
            gdpr_checkbox=True,
        )
        round_0 = TypesettingRound.objects.create(article=paper, round_number=0)
        TypesettingAssignment.objects.create(
            round=round_0, typesetter=other_typesetter.janeway_account, assigned=self.BEFORE_DT
        )
        round_2 = TypesettingRound.objects.create(article=paper, round_number=2)
        TypesettingAssignment.objects.create(
            round=round_2, typesetter=scenario["typesetter_account"], assigned=self.END_DT
        )

        response = self._get(client, journal, scenario["typesetter_account"].pk, auth=self._auth(eo_user))
        assert response.status_code == 200
        item = next(i for i in response.json() if i["preprint_id"] == f"{journal.code}_{paper.pk}")
        assert datetime.datetime.fromisoformat(item["date_taken_in_charge"]) == self.MIDDLE_DT

    def test_missing_and_malformed_dates(self, client: Client, journal, eo_user, monitoring_scenario):
        """Missing or malformed start_date/end_date → 400."""
        auth = self._auth(eo_user)
        url = self._url(journal, monitoring_scenario["typesetter_account"].pk)
        for params in (
            {},
            {"start_date": self.START},
            {"end_date": self.END},
            {"start_date": "not-a-date", "end_date": self.END},
            {"start_date": self.START, "end_date": "10/08/2026"},
        ):
            response = client.get(url, data=params, HTTP_AUTHORIZATION=auth)
            assert response.status_code == 400, f"params {params} should give 400, got {response.status_code}"

    def test_unknown_typesetter_is_404(self, client: Client, journal, eo_user, monitoring_scenario):
        """Unknown typesetter pk → 404."""
        response = self._get(client, journal, 999999, auth=self._auth(eo_user))
        assert response.status_code == 404

    def test_account_without_assignments_gives_empty_list(
        self, client: Client, journal, eo_user, monitoring_scenario, author
    ):
        """A known account that never held a typesetting assignment → 200 and empty list."""
        response = self._get(client, journal, author.janeway_account.pk, auth=self._auth(eo_user))
        assert response.status_code == 200
        assert response.json() == []

    def test_authentication_reuses_existing_api(
        self, client: Client, journal, monitoring_scenario, typesetter, normal_user
    ):
        """Same stack as the other API endpoints (Specifications.md §3.7): 401 anonymous, EO and any
        typesetter in, other users out. A finer typesetter-self-only check is deferred to the planned
        permission refactor; this test pins the currently-reused behaviour.
        """
        url = self._url(journal, monitoring_scenario["typesetter_account"].pk)
        params = {"start_date": self.START, "end_date": self.END}

        # anonymous
        response = client.get(url, data=params)
        assert response.status_code == 401

        # normal user: no EO and no typesetter role → out
        response = client.get(
            url, data=params, HTTP_AUTHORIZATION=self._auth(normal_user, key=f"{self.GOOD_TOKEN}-{normal_user.pk}")
        )
        assert response.status_code == 403

        # a typesetter (different from the queried pk) → in
        response = client.get(
            url, data=params, HTTP_AUTHORIZATION=self._auth(typesetter, key=f"{self.GOOD_TOKEN}-typesetter")
        )
        assert response.status_code == 200

    def test_calls_are_logged(self, client: Client, journal, eo_user, monitoring_scenario, caplog):
        """Calls are logged (same convention as the other API entry points)."""
        response = self._get(client, journal, monitoring_scenario["typesetter_account"].pk, auth=self._auth(eo_user))
        assert response.status_code == 200
        log_line = _api_request_log_line(caplog)
        assert "GET" in log_line
        assert "GOODTOKEN" not in log_line
        assert str(eo_user.janeway_account.pk) in log_line


@pytest.mark.django_db
def test_api_journal_production_list_returns_articles_in_production(
    client: Client, eo_user: JCOMProfile, journal, author, sections
):
    """G7 - GET /journal/<code>/production/ (Specifications.md §3.6)."""
    Token.objects.create(user=eo_user.janeway_account, key="EOTOKEN")
    article = _create_article_for_api_tests(author, journal, sections)
    article.articleworkflow.state = ArticleWorkflow.ReviewStates.READY_FOR_TYPESETTER
    article.articleworkflow.save()

    response = client.get(
        reverse("journal-production", args=(journal.code,)),
        HTTP_AUTHORIZATION="Token EOTOKEN",
    )

    assert response.status_code == 200
    data = response.json()
    assert len(data) == 1
    record = data[0]
    assert record["preprint_id"] == article.articleworkflow.preprint_id
    assert record["status"]["code"] == "ReadyForTypesetter"
    # ProductionBaseSerializer fields (shared with G8) come along for free.
    assert "published_id" in record
    assert "doi" in record
    # G7-specific fields.
    assert "special_issue" in record
    assert "typesetter" in record


@pytest.mark.django_db
def test_api_journal_production_list_excludes_accepted_state(
    client: Client, eo_user: JCOMProfile, journal, author, sections
):
    """ACCEPTED articles are deliberately excluded (Specifications.md §3.6)."""
    Token.objects.create(user=eo_user.janeway_account, key="EOTOKEN")
    article = _create_article_for_api_tests(author, journal, sections)
    article.articleworkflow.state = ArticleWorkflow.ReviewStates.ACCEPTED
    article.articleworkflow.save()

    response = client.get(
        reverse("journal-production", args=(journal.code,)),
        HTTP_AUTHORIZATION="Token EOTOKEN",
    )

    assert response.status_code == 200
    assert response.json() == []


@pytest.mark.django_db
def test_api_schema_is_protected(client: Client, eo_user: JCOMProfile, typesetter, normal_user):
    """Only EO members and typesetters with a valid token can fetch the OpenAPI schema."""
    url = reverse("schema")
    Token.objects.create(user=eo_user.janeway_account, key="EOTOKEN")
    Token.objects.create(user=typesetter.janeway_account, key="TYPESETTERTOKEN")
    Token.objects.create(user=normal_user.janeway_account, key="NORMALTOKEN")

    assert client.get(url).status_code == 401
    assert client.get(url, HTTP_AUTHORIZATION="Token WRONGTOKEN").status_code == 401
    assert client.get(url, HTTP_AUTHORIZATION="Token NORMALTOKEN").status_code == 403
    assert client.get(url, HTTP_AUTHORIZATION="Token EOTOKEN").status_code == 200
    assert client.get(url, HTTP_AUTHORIZATION="Token TYPESETTERTOKEN").status_code == 200


@pytest.mark.django_db
def test_api_schema_is_valid_openapi(client: Client, eo_user: JCOMProfile):
    """The schema entry point serves a parsable OpenAPI 3 document."""
    Token.objects.create(user=eo_user.janeway_account, key="EOTOKEN")

    response = client.get(reverse("schema"), {"format": "json"}, HTTP_AUTHORIZATION="Token EOTOKEN")

    assert response.status_code == 200
    schema = response.json()
    assert schema["openapi"].startswith("3.")
    assert "/plugins/wjs-review-articles/api/v1/collaborations/" in schema["paths"]


@pytest.mark.django_db
def test_api_docs_ui_is_protected(client: Client, eo_user: JCOMProfile, normal_user):
    """The schema, Swagger UI and Redoc are gated exactly like the rest of the API.

    As everywhere else here, they accept both an API token and a logged-in Janeway session (with
    no token at all): a browser navigating directly to their URLs cannot set an ``Authorization``
    header, so a human already logged into a Janeway session needs another way in. The permission
    check (EO or typesetter) still applies regardless of which authentication method got the
    request past authentication.
    """
    Token.objects.create(user=eo_user.janeway_account, key="EOTOKEN")
    Token.objects.create(user=normal_user.janeway_account, key="NORMALTOKEN")

    for view_name in ("schema", "swagger-ui", "redoc"):
        url = reverse(view_name)

        # No authentication at all (no token, no session): unauthenticated.
        assert client.get(url).status_code == 401

        # Token authentication, wrong role: authenticated but forbidden.
        assert client.get(url, HTTP_AUTHORIZATION="Token NORMALTOKEN").status_code == 403

        # Token authentication, EO: allowed, as before.
        assert client.get(url, HTTP_AUTHORIZATION="Token EOTOKEN").status_code == 200

        # Session authentication (no token header at all), wrong role: still forbidden -- the
        # permission check applies regardless of the authentication method.
        client.force_login(normal_user.janeway_account)
        assert client.get(url).status_code == 403
        client.logout()

        # Session authentication (no token header at all), EO: now allowed too.
        client.force_login(eo_user.janeway_account)
        assert client.get(url).status_code == 200
        client.logout()


@pytest.mark.django_db
def test_api_schema_documents_binary_responses(client: Client, eo_user: JCOMProfile):
    """The zip and galley download/upload entry points document their binary and error responses."""
    Token.objects.create(user=eo_user.janeway_account, key="EOTOKEN")

    response = client.get(reverse("schema"), {"format": "json"}, HTTP_AUTHORIZATION="Token EOTOKEN")
    schema = response.json()

    zip_get = schema["paths"]["/plugins/wjs-review-articles/api/v1/article/{pk}/zip/"]["get"]
    assert set(zip_get["responses"]) == {"200", "404"}
    # The binary bodies are documented under their real media types, not under the JSON one that
    # the view's renderers (needed by the error envelopes) would otherwise imply.
    assert set(zip_get["responses"]["200"]["content"]) == {ZIP_MEDIA_TYPE}
    assert set(zip_get["responses"]["404"]["content"]) == {"application/json"}

    zip_put = schema["paths"]["/plugins/wjs-review-articles/api/v1/article/{pk}/zip/"]["put"]
    assert set(zip_put["responses"]) == {"200", "400", "404", "415", "502"}
    # The uploaded archive is the raw request body: the schema must advertise the zip media types
    # the entry point accepts, not the form/multipart ones DRF's default parsers would imply.
    assert set(zip_put["requestBody"]["content"]) == set(SOURCE_ZIP_MEDIA_TYPES)
    assert set(zip_put["responses"]["200"]["content"]) == {"application/json"}
    assert zip_put["responses"]["502"]["content"]["application/json"]["schema"]["$ref"].endswith("/Error")

    galley_get = schema["paths"]["/plugins/wjs-review-articles/api/v1/article/{pk}/galley/{file_type}/"]["get"]
    assert set(galley_get["responses"]) == {"200", "400", "404", "409"}
    assert set(galley_get["responses"]["200"]["content"]) == set(GALLEY_DOWNLOAD_MEDIA_TYPES)
    assert set(galley_get["responses"]["404"]["content"]) == {"application/json"}

    galley_post = schema["paths"]["/plugins/wjs-review-articles/api/v1/article/{pk}/galley/{file_type}/"]["post"]
    assert set(galley_post["responses"]) == {"201", "400", "404", "409"}
    # The upload rejects anything but the media types matching the galley type: the schema must not
    # advertise the form/multipart ones that DRF's default parsers would imply.
    assert set(galley_post["requestBody"]["content"]) == set(GALLEY_UPLOAD_MEDIA_TYPES)

    error_component = schema["components"]["schemas"]["Error"]
    assert error_component["properties"]["error"]["$ref"].endswith("/ErrorDetail")
    detail_component = schema["components"]["schemas"]["ErrorDetail"]
    assert set(detail_component["properties"]) == {"code", "message", "details"}


@pytest.mark.django_db
def test_api_articlegalleys_response_shape(client: Client, article: Article, eo_user: JCOMProfile):
    """The article-galleys entry point serves the same JSON shape before and after the refactor."""
    article.date_published = timezone.now()
    article.stage = STAGE_PUBLISHED
    article.save()

    pdf_corefile = File.objects.create(
        mime_type="application/pdf",
        original_filename="of.pdf",
        uuid_filename="uf.pdf",
        is_galley=True,
    )
    Galley.objects.create(file=pdf_corefile, label="PDF", type="pdf", article=article, sequence=1)

    account = eo_user.janeway_account
    Token.objects.create(user=account, key="GOODTOKEN")

    url = reverse("article-galleys", args=(article.pk,))
    response = client.get(url, HTTP_AUTHORIZATION="Token GOODTOKEN")

    assert response.status_code == 200
    assert response.json() == {
        "article_id": article.pk,
        "items": [
            {
                "type": "pdf",
                "sequence": 1,
                "filename": "of.pdf",
                "contentType": "application/pdf",
                "download_url": f"/plugins/wjs-review-articles/api/v1/article/{article.pk}/galley/pdf/",
            }
        ],
    }


@pytest.mark.django_db
def test_api_schema_infers_article_galleys_response(client: Client, eo_user: JCOMProfile):
    """ArticleGalleyListView's response schema is inferred from its serializer, with no manual override."""
    Token.objects.create(user=eo_user.janeway_account, key="EOTOKEN")

    response = client.get(reverse("schema"), {"format": "json"}, HTTP_AUTHORIZATION="Token EOTOKEN")
    schema = response.json()

    component = schema["components"]["schemas"]["ArticleGalleyList"]
    assert set(component["properties"]) == {"article_id", "items"}
    item_ref = component["properties"]["items"]["items"]["$ref"]
    item_component = schema["components"]["schemas"][item_ref.rsplit("/", 1)[-1]]
    assert set(item_component["properties"]) == {"type", "sequence", "filename", "contentType", "download_url"}


@pytest.mark.django_db
def test_api_schema_documents_collaborations_response(client: Client, eo_user: JCOMProfile):
    """The collaborations entry point documents its envelope and its 400 error response."""
    Token.objects.create(user=eo_user.janeway_account, key="EOTOKEN")

    response = client.get(reverse("schema"), {"format": "json"}, HTTP_AUTHORIZATION="Token EOTOKEN")
    schema = response.json()

    collaborations_get = schema["paths"]["/plugins/wjs-review-articles/api/v1/collaborations/"]["get"]
    assert set(collaborations_get["responses"]) == {"200", "400"}

    # The entry point serves a single envelope object, not a bare list: the documented 200 response
    # must be a direct reference to the envelope component, never wrapped in an array.
    collaborations_200 = collaborations_get["responses"]["200"]["content"]["application/json"]["schema"]
    assert collaborations_200.get("type") != "array", collaborations_200
    assert collaborations_200["$ref"].endswith("/CollaborationsExport"), collaborations_200

    component = schema["components"]["schemas"]["CollaborationsExport"]
    assert set(component["properties"]) == {"version", "collaborations"}


@pytest.mark.django_db
def test_api_schema_covers_all_entry_points(client: Client, eo_user: JCOMProfile):
    """The generated schema covers every real API entry point and excludes its own docs infrastructure."""
    Token.objects.create(user=eo_user.janeway_account, key="EOTOKEN")

    response = client.get(reverse("schema"), {"format": "json"}, HTTP_AUTHORIZATION="Token EOTOKEN")

    assert response.status_code == 200
    schema = response.json()
    assert schema["paths"].keys() == {
        "/plugins/wjs-review-articles/api/v1/collaborations/",
        "/plugins/wjs-review-articles/api/v1/article/{pk}/zip/",
        "/plugins/wjs-review-articles/api/v1/article/{pk}/galleys/",
        "/plugins/wjs-review-articles/api/v1/article/{pk}/galley/{file_type}/",
        "/plugins/wjs-review-articles/api/v1/article/{pk}/galley/{file_type}/{sequence}/",
        "/plugins/wjs-review-articles/api/v1/journal/{code}/production/",
        "/plugins/wjs-review-articles/api/v1/journal/{code}/typesetter/{typesetter_pk}/papers/",
    }


def _jcomassistant_url(http_server: ThreadedHTTPServer, path: str = "") -> str:
    """Return the URL of the dummy jcomassistant server, optionally rooted at the given path."""
    server = http_server.server
    return f"http://{server.server_name}:{server.server_port}/{path}"


@pytest.fixture()
def published_article(
    rfp_article: Article,
    fake_request: HttpRequest,
    eo_user: JCOMProfile,
    http_server: ThreadedHTTPServer,
    review_sections: list[Section],  # noqa: ARG001
) -> Article:
    """Publish an article - galleys included - against the dummy jcomassistant server."""
    workflow = rfp_article.articleworkflow
    with override_settings(JCOMASSISTANT_URL=_jcomassistant_url(http_server)):
        BeginPublication(workflow=workflow, user=eo_user, request=fake_request).run()
        workflow.refresh_from_db()
        # BeginPublication hands the galley generation over to django-q; do it here when the
        # queue is not configured to run its tasks synchronously.
        if workflow.state == ArticleWorkflow.ReviewStates.PUBLICATION_IN_PROGRESS:
            FinishPublication(workflow=workflow, user=eo_user, request=fake_request).run()
            workflow.refresh_from_db()
    article = Article.objects.get(pk=workflow.article.pk)
    assert article.stage == STAGE_PUBLISHED, (workflow.state, article.stage)
    return article


def _sources_zip(article: Article, tex_name: str = None) -> bytes:
    """Build an archive that looks like the TeX sources of the given article.

    The entry point recognises an article's sources by the TeX file named after it, so a test that
    wants the upload accepted must build the archive around that name.
    """
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        archive.writestr(tex_name or f"{article.articleworkflow.preprint_id}.tex", "\\documentclass{article}")
        archive.writestr("figure.png", "not really a png")
    return buffer.getvalue()


def _put_zip(client: Client, article: Article, body: bytes, token: str = "GOODTOKEN", **kwargs):
    """PUT the given body onto the article's zip entry point."""
    return client.put(
        reverse("article-zip", args=(article.pk,)),
        data=body,
        content_type=kwargs.pop("content_type", "application/zip"),
        HTTP_AUTHORIZATION=f"Token {token}",
        **kwargs,
    )


@pytest.mark.django_db
def test_api_article_zip_put_replaces_sources_and_regenerates_galleys(
    client: Client,
    published_article: Article,
    eo_user: JCOMProfile,
    http_server: ThreadedHTTPServer,
):
    """A PUT on the zip entry point stores the new sources and rebuilds every galley from them."""
    article = published_article
    workflow = article.articleworkflow
    Token.objects.create(user=eo_user.janeway_account, key="GOODTOKEN")

    galleys_before = set(article.galley_set.values_list("pk", flat=True))
    assert len(galleys_before) == 3
    source_before = workflow.publication_galleys_source_file
    assert source_before is not None
    uuid_before = source_before.uuid_filename

    new_sources = _sources_zip(article)
    with override_settings(JCOMASSISTANT_URL=_jcomassistant_url(http_server)):
        response = _put_zip(client, article, new_sources)

    assert response.status_code == 200, response.content
    payload = json.loads(response.content)
    assert payload["article_id"] == article.pk
    assert len(payload["galleys"]) == 3

    # The sources are the ones we have just uploaded, stored in the same core.File as before.
    workflow.refresh_from_db()
    assert workflow.publication_galleys_source_file.pk == source_before.pk
    assert workflow.publication_galleys_source_file.uuid_filename != uuid_before
    assert Path(workflow.publication_galleys_source_file.self_article_path()).read_bytes() == new_sources

    # The galleys are new ones, and the article renders one of them.
    galleys_after = set(article.galley_set.values_list("pk", flat=True))
    assert len(galleys_after) == 3
    assert galleys_after.isdisjoint(galleys_before)
    assert all(article.galley_set.values_list("public", flat=True))
    article.refresh_from_db()
    assert article.render_galley.pk in galleys_after


@pytest.mark.django_db
def test_api_article_zip_put_rolls_back_when_generation_fails(
    client: Client,
    published_article: Article,
    eo_user: JCOMProfile,
):
    """When galley generation fails, the article keeps the sources and the galleys it had."""
    article = published_article
    workflow = article.articleworkflow
    Token.objects.create(user=eo_user.janeway_account, key="GOODTOKEN")

    galleys_before = set(article.galley_set.values_list("pk", flat=True))
    uuid_before = workflow.publication_galleys_source_file.uuid_filename

    jcomassistant_is_down = requests.models.Response()
    jcomassistant_is_down.status_code = 500
    jcomassistant_is_down._content = b"Internal Server Error"
    with mock.patch.object(requests, "post", return_value=jcomassistant_is_down):
        response = _put_zip(client, article, _sources_zip(article))

    assert response.status_code == 502, response.content
    assert json.loads(response.content)["error"]["code"] == "GALLEY_GENERATION_FAILED"

    # Neither the sources nor the galleys have been touched.
    workflow.refresh_from_db()
    assert workflow.publication_galleys_source_file.uuid_filename == uuid_before
    assert set(article.galley_set.values_list("pk", flat=True)) == galleys_before


@pytest.mark.django_db
def test_api_article_zip_put_refuses_malformed_requests(
    client: Client,
    published_article: Article,
    eo_user: JCOMProfile,
):
    """The uploaded body must be declared as - and be - a zip archive."""
    article = published_article
    Token.objects.create(user=eo_user.janeway_account, key="GOODTOKEN")

    response = _put_zip(client, article, _sources_zip(article), content_type="application/pdf")
    assert response.status_code == 415, response.content
    assert json.loads(response.content)["error"]["code"] == "UNSUPPORTED_MEDIA_TYPE"
    assert json.loads(response.content)["error"]["details"]["got"] == "application/pdf"

    response = _put_zip(client, article, b"I am not a zip archive")
    assert response.status_code == 400, response.content
    assert json.loads(response.content)["error"]["code"] == "BAD_REQUEST"

    # Django's test client drops the content type of an empty body, so it is set by hand here.
    response = _put_zip(client, article, b"", CONTENT_TYPE="application/zip")
    assert response.status_code == 400, response.content
    assert json.loads(response.content)["error"]["code"] == "BAD_REQUEST"


@pytest.mark.django_db
def test_api_article_zip_put_is_protected(
    client: Client,
    published_article: Article,
    normal_user: JCOMProfile,
):
    """Replacing an article's sources needs the token of an EO member or of a typesetter."""
    article = published_article
    Token.objects.create(user=normal_user.janeway_account, key="NORMALTOKEN")
    body = _sources_zip(article)

    assert _put_zip(client, article, body, token="WRONGTOKEN").status_code == 401
    assert _put_zip(client, article, body, token="NORMALTOKEN").status_code == 403


@pytest.mark.django_db
def test_api_file_entry_points_honour_the_accept_header(
    client: Client,
    published_article: Article,
    eo_user: JCOMProfile,
):
    """A client asking for the media type the schema promises is served, not refused with a 406.

    DRF negotiates the "Accept" header against the view's renderers (JSON, needed here only for
    the error envelopes) and would reject anything else - including the very media types this
    API's schema advertises for these files - before authentication even runs.
    """
    article = published_article
    Token.objects.create(user=eo_user.janeway_account, key="GOODTOKEN")

    zip_url = reverse("article-zip", args=(article.pk,))
    for accept in (*SOURCE_ZIP_MEDIA_TYPES, "*/*"):
        response = client.get(zip_url, HTTP_AUTHORIZATION="Token GOODTOKEN", HTTP_ACCEPT=accept)
        assert response.status_code == 200, (accept, response.status_code)

    galley = article.galley_set.first()
    galley_url = reverse("article-galley-seq", args=(article.pk, galley.type, galley.sequence))
    response = client.get(galley_url, HTTP_AUTHORIZATION="Token GOODTOKEN", HTTP_ACCEPT=galley.file.mime_type)
    assert response.status_code == 200, response.content

    # The "Accept" header must not shadow authentication either: no token is still a 401.
    assert client.get(zip_url, HTTP_ACCEPT=ZIP_MEDIA_TYPE).status_code == 401

    # Errors keep being served as JSON, whatever the client asked for.
    response = client.get(
        reverse("article-galley-seq", args=(article.pk, galley.type, 999)),
        HTTP_AUTHORIZATION="Token GOODTOKEN",
        HTTP_ACCEPT="application/epub+zip",
    )
    assert response.status_code == 404
    assert response["Content-Type"] == "application/json"
    assert response.json()["error"]["code"] == "NOT_FOUND"


@pytest.mark.django_db
def test_api_entry_points_accept_a_janeway_session(
    client: Client,
    eo_user: JCOMProfile,
    typesetter,
    normal_user,
):
    """The API entry points authenticate a logged-in Janeway session, not only an API token.

    This is what lets an EO member or typesetter try the API out from Swagger UI, in the browser
    they are already logged into, without minting a token for themselves first.
    """
    url = reverse("collaborations")

    # No credentials at all: unauthenticated, as before.
    assert client.get(url).status_code == 401

    # A session, but the wrong role: authenticated and forbidden - the permission check does not
    # care which credential got the request this far.
    client.force_login(normal_user.janeway_account)
    assert client.get(url).status_code == 403
    client.logout()

    for user in (eo_user, typesetter):
        client.force_login(user.janeway_account)
        assert client.get(url).status_code == 200, user
        client.logout()


@pytest.mark.django_db
def test_api_session_authenticated_writes_need_a_csrf_token(eo_user: JCOMProfile, article: Article):
    """A write authenticated by the session cookie alone is refused: it needs the CSRF token too.

    Without this, any other site could make a logged-in EO's browser replace an article's sources.
    Swagger UI sends the token on every non-GET same-origin request, so its "Try it out" works;
    this test walks the same path, taking the token from the cookie that the Swagger UI page sets.
    """
    csrf_client = Client(enforce_csrf_checks=True)
    csrf_client.force_login(eo_user.janeway_account)
    # An article that the entry point will not find, so that the request fails *after* having been
    # authenticated: what is under test here is how far it gets, not what it then does.
    url = reverse("article-zip", args=(article.pk,))

    response = csrf_client.put(url, data=b"irrelevant", content_type=ZIP_MEDIA_TYPE)
    assert response.status_code == 403
    assert "CSRF" in response.json()["detail"]

    # The Swagger UI page renders the CSRF token, which sets the cookie its JavaScript then sends
    # back as a header.
    csrf_client.get(reverse("swagger-ui"))
    csrf_token = csrf_client.cookies["csrftoken"].value

    response = csrf_client.put(url, data=b"irrelevant", content_type=ZIP_MEDIA_TYPE, HTTP_X_CSRFTOKEN=csrf_token)
    assert response.status_code == 404, response.content


@pytest.mark.django_db
def test_api_article_zip_put_refuses_another_article_sources(
    client: Client,
    published_article: Article,
    eo_user: JCOMProfile,
):
    """An archive that is not this article's is refused before anything is touched.

    Nothing but the TeX file's name ties the uploaded archive to the article in the URL, so a typo
    in the article id would otherwise rebuild one article's galleys out of another's sources.
    """
    article = published_article
    workflow = article.articleworkflow
    Token.objects.create(user=eo_user.janeway_account, key="GOODTOKEN")

    galleys_before = set(article.galley_set.values_list("pk", flat=True))
    uuid_before = workflow.publication_galleys_source_file.uuid_filename

    # The sources of some other article: right shape, wrong name.
    response = _put_zip(client, article, _sources_zip(article, tex_name="JCOM_9999.tex"))

    assert response.status_code == 400, response.content
    error = response.json()["error"]
    assert error["code"] == "BAD_REQUEST"
    assert f"{workflow.preprint_id}.tex" in error["message"]

    # An archive nesting this article's sources in a folder is refused too, and says so.
    response = _put_zip(
        client, article, _sources_zip(article, tex_name=f"{workflow.preprint_id}/{workflow.preprint_id}.tex")
    )
    assert response.status_code == 400, response.content
    assert "at its root" in response.json()["error"]["message"]

    # Neither attempt touched the article.
    workflow.refresh_from_db()
    assert workflow.publication_galleys_source_file.uuid_filename == uuid_before
    assert set(article.galley_set.values_list("pk", flat=True)) == galleys_before


@pytest.mark.django_db
def test_api_article_zip_put_deletes_the_galleys_it_replaces(
    client: Client,
    published_article: Article,
    eo_user: JCOMProfile,
    http_server: ThreadedHTTPServer,
    django_capture_on_commit_callbacks,
):
    """The replaced galleys leave behind no row and no file: they are deleted, not just detached."""
    article = published_article
    Token.objects.create(user=eo_user.janeway_account, key="GOODTOKEN")

    replaced = list(article.galley_set.all())
    replaced_galley_pks = [galley.pk for galley in replaced]
    replaced_file_pks = [galley.file_id for galley in replaced]
    replaced_paths = [Path(galley.file.self_article_path()) for galley in replaced]
    assert len(replaced) == 3
    assert all(path.exists() for path in replaced_paths), "the fixture's galley files should be on disk"

    with override_settings(JCOMASSISTANT_URL=_jcomassistant_url(http_server)):
        # The files are unlinked on commit, so that a rollback cannot orphan the rows.
        with django_capture_on_commit_callbacks(execute=True):
            response = _put_zip(client, article, _sources_zip(article))
    assert response.status_code == 200, response.content

    assert not Galley.objects.filter(pk__in=replaced_galley_pks).exists(), "stale core.Galley rows"
    assert not File.objects.filter(pk__in=replaced_file_pks).exists(), "stale core.File rows"
    assert not [path for path in replaced_paths if path.exists()], "orphan files left on the filesystem"

    # ...while the galleys that replaced them are whole.
    assert article.galley_set.count() == 3
    for galley in article.galley_set.all():
        assert Path(galley.file.self_article_path()).exists(), galley


@pytest.mark.django_db
def test_api_article_zip_put_keeps_a_galley_that_is_still_in_use(
    client: Client,
    published_article: Article,
    eo_user: JCOMProfile,
    http_server: ThreadedHTTPServer,
    django_capture_on_commit_callbacks,
):
    """A replaced galley that another record still refers to is only detached, never deleted.

    It belongs to that record's own history - here, to the round the typesetter created it in.
    """
    article = published_article
    Token.objects.create(user=eo_user.janeway_account, key="GOODTOKEN")

    in_use, *rest = list(article.galley_set.all())
    in_use_path = Path(in_use.file.self_article_path())
    article.articleworkflow.get_latest_typesetting_assignment(only_completed=False).galleys_created.add(in_use)

    with override_settings(JCOMASSISTANT_URL=_jcomassistant_url(http_server)):
        with django_capture_on_commit_callbacks(execute=True):
            response = _put_zip(client, article, _sources_zip(article))
    assert response.status_code == 200, response.content

    in_use.refresh_from_db()
    assert in_use.article_id is None, "it is no longer one of the article's galleys"
    assert in_use_path.exists(), "but its file is still there"
    # the others, referred to by nothing, are gone
    assert not Galley.objects.filter(pk__in=[galley.pk for galley in rest]).exists()


@pytest.mark.django_db
def test_api_article_zip_put_drops_the_cache(
    client: Client,
    published_article: Article,
    eo_user: JCOMProfile,
    http_server: ThreadedHTTPServer,
    django_capture_on_commit_callbacks,
):
    """The article's page is cached, galleys included: the cache goes once the new ones are in."""
    article = published_article
    Token.objects.create(user=eo_user.janeway_account, key="GOODTOKEN")
    django_cache.set("something-janeway-cached", "stale")

    with override_settings(JCOMASSISTANT_URL=_jcomassistant_url(http_server)):
        with django_capture_on_commit_callbacks(execute=True) as callbacks:
            response = _put_zip(client, article, _sources_zip(article))

    assert response.status_code == 200, response.content
    assert callbacks, "the cache is dropped on commit, not before"
    assert django_cache.get("something-janeway-cached") is None
