"""Tests related to wjs_review API."""

import datetime
import json

import pytest
from django.test.client import Client
from django.urls import reverse
from django.utils import timezone
from identifiers.models import Identifier
from plugins.wjs_review.api.const import COLLABORATIONS_EXPORT_KEYS
from plugins.wjs_submission.helpers.collaborations import TABELLONE_FIELDS
from plugins.wjs_submission.models import Collaboration
from rest_framework.authtoken.models import Token
from submission.models import STAGE_PUBLISHED, Article
from typesetting.models import TypesettingAssignment, TypesettingRound

from wjs.jcom_profile.models import JCOMProfile

from ..models import ArticleWorkflow


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
