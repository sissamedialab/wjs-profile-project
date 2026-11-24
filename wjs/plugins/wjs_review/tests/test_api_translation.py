"""Tests for the translation-galleys API entry point and the guard on PUT /zip/ (specs#990)."""

import configparser
import datetime
import io
import json
import zipfile
from pathlib import Path
from unittest import mock
from urllib.parse import parse_qs, urlparse

import pytest
import requests
from core import files as core_files
from core.models import File, Galley
from django.core.exceptions import ValidationError
from django.core.files import File as DjangoFile
from django.test import override_settings
from django.test.client import Client
from django.urls import reverse
from django.utils import timezone
from identifiers.models import Identifier
from journal.models import Issue
from rest_framework.authtoken.models import Token
from submission.models import STAGE_PUBLISHED, Article, Section

from wjs.jcom_profile.models import JCOMProfile

from ..api.const import NOT_COMPILED_HEADER, ZIP_MEDIA_TYPE
from ..api.logic import INVALID_REQUEST_CODE
from ..api.logic__translation import find_translation
from ..models import ArticleWorkflow

JA_URL = "http://jcomassistant.invalid/api/v2/"
DOI = "10.22323/2.25010299"

MAIN_TEX = b"""\\documentclass{article}
\\usepackage[journal=jcom]{jcom2}
\\published{???}
\\publicationData{00}{00}{A}{00}
\\publicationDoi{10.22323/0.00000000}
\\publicationLanguage{por}
"""
TRANSLATION_TEX = MAIN_TEX.replace(b"{por}", b"{eng}") + b"\\translator{Someone}\n"
INJECTED_MARK = b"% injected by the fake jcomassistant\n"


def _stem(article: Article) -> str:
    return f"{article.journal.code}_{article.pk}"


def _sources(article: Article, extra: dict = None, without: tuple = (), spawn_ini: str = None) -> bytes:
    """Build an archive that looks like the sources of a paper with a translation."""
    stem = _stem(article)
    entries = {
        f"{stem}.tex": MAIN_TEX,
        f"{stem}_eng.tex": TRANSLATION_TEX,
        "spawn.ini": spawn_ini if spawn_ini is not None else f"[tex]\nmain = {stem}.tex\n\n[pdf]\nengine = xelatex\n",
        f"{stem}.bib": b"@article{a, title={A}}",
        "figures/image1.png": b"not really a png",
    }
    entries.update(extra or {})
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        for name, content in entries.items():
            if name not in without:
                archive.writestr(name, content)
    return buffer.getvalue()


def _galleys_zip() -> bytes:
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        archive.writestr("galley_eng.pdf", b"%PDF-1.5 translation")
        archive.writestr("galley_eng.epub", b"PK translation")
        archive.writestr("galleys-output.srvc_log", "INFO     - all good\n")
    return buffer.getvalue()


def _response(status_code: int, content: bytes) -> requests.models.Response:
    response = requests.models.Response()
    response.status_code = status_code
    response._content = content
    return response


class FakeJcomassistant:
    """Stand-in for `requests.post` that answers as jcomassistant's texdata, pubdata and galleys would."""

    def __init__(self, publication_data: dict = None, texdata_status: int = 200, galleys_status: int = 200):
        self.publication_data = publication_data
        self.texdata_status = texdata_status
        self.galleys_status = galleys_status
        self.calls = {}

    def __call__(self, url, files=None, data=None, timeout=None, **kwargs):
        endpoint = urlparse(url).path.rstrip("/").rsplit("/", 1)[-1]
        self.calls[endpoint] = {"url": url, "files": files, "data": data, "timeout": timeout}
        if endpoint == "texdata":
            return _response(self.texdata_status, json.dumps({"publication_data": self.publication_data}).encode())
        if endpoint == "pubdata":
            _name, content, _mime = files["file"]
            return _response(200, content + INJECTED_MARK)
        if endpoint == "galleys":
            return _response(self.galleys_status, _galleys_zip())
        raise AssertionError(f"Unexpected call to {url}")

    def sent_archive(self) -> zipfile.ZipFile:
        """Return the archive that was sent to the galleys endpoint."""
        _name, content, _mime = self.calls["galleys"]["files"]["file"]
        return zipfile.ZipFile(io.BytesIO(content))


def _ensure_doi(article: Article) -> str:
    if doi := article.get_doi():
        return doi
    Identifier.objects.create(id_type="doi", identifier=DOI, article=article)
    return DOI


@pytest.fixture
def published_translated_article(
    rfp_article: Article,
    review_sections: list[Section],  # noqa: ARG001
    eo_user: JCOMProfile,
) -> tuple[Article, dict]:
    """A published paper whose final sources contain a translation, and the data injected at publication.

    The data are those that BeginPublication would compute (while the paper is still ready-for-publication), with a
    counter that the DB cannot give back any more once the paper is published.
    """
    article = rfp_article
    workflow = article.articleworkflow
    doi = _ensure_doi(article)
    article.date_published = datetime.datetime(2026, 11, 10, 10, 0, tzinfo=datetime.timezone.utc)
    article.save()
    publication_data = workflow.collect_publication_data()
    article.page_numbers = publication_data["eid"]
    article.stage = STAGE_PUBLISHED
    article.save()
    workflow.state = ArticleWorkflow.ReviewStates.PUBLISHED
    workflow.save()
    publication_data = {key: publication_data[key] for key in ("date_published", "year", "volume", "issue", "eid")}
    publication_data.update({"section": workflow.collect_publication_data()["section"], "counter": "07", "doi": doi})

    final_sources = core_files.save_file_to_article(
        file_to_handle=DjangoFile(io.BytesIO(_sources(article)), name=f"{_stem(article)}.zip"),
        article=article,
        owner=eo_user.janeway_account,
        label="Final sources",
        description="Source files for final galleys",
        replace=None,
    )
    workflow.publication_galleys_source_file = final_sources
    workflow.save()
    # The rfp fixture attaches its (fake) production galleys to the article too: detach them, as the production
    # galleys of a real paper are; each test adds the published galleys it needs.
    Galley.objects.filter(article=article).update(article=None)
    return article, publication_data


@pytest.fixture
def production_translated_article(
    assigned_to_typesetter_article_with_files_to_typeset: Article,
    fb_issue: Issue,
    review_sections: list[Section],  # noqa: ARG001
) -> Article:
    """A paper in production, whose typesetter uploaded sources with a translation."""
    article = assigned_to_typesetter_article_with_files_to_typeset
    article.primary_issue = fb_issue
    article.save()
    article.issues.add(fb_issue)
    _ensure_doi(article)
    assignment = article.articleworkflow.get_latest_typesetting_assignment(only_completed=False)
    Path(assignment.files_to_typeset.get().self_article_path()).write_bytes(_sources(article))
    return article


def _set_sources(article: Article, content: bytes):
    """Replace the content of the paper's sources (final or typesetted) on the filesystem."""
    workflow = article.articleworkflow
    if article.stage == STAGE_PUBLISHED:
        source_file = workflow.publication_galleys_source_file
    else:
        source_file = workflow.get_latest_typesetting_assignment(only_completed=False).files_to_typeset.get()
    Path(source_file.self_article_path()).write_bytes(content)


def _get(client: Client, article: Article, token: str = "EOTOKEN", **params):
    return client.get(
        reverse("article-translation-galleys", args=(article.pk,)),
        data=params,
        HTTP_AUTHORIZATION=f"Token {token}",
    )


def _snapshot(article: Article) -> dict:
    """Return what the entry point must not change."""
    article.refresh_from_db()
    folder = Path(article.folder_path())
    return {
        "files": File.objects.count(),
        "galleys": Galley.objects.count(),
        "identifiers": Identifier.objects.filter(article=article).count(),
        "date_published": article.date_published,
        "page_numbers": article.page_numbers,
        "folder": sorted(p.name for p in folder.iterdir()) if folder.exists() else [],
    }


# find_translation


@pytest.mark.parametrize(
    "names,spawn_ini,error",
    (
        (["X_1.tex", "X_1_eng.tex"], None, "has no spawn.ini"),
        (["X_1.tex", "X_1_eng.tex", "spawn.ini"], "[pdf]\nengine=xelatex\n", "does not name the main TeX file"),
        (["X_1.tex", "spawn.ini"], "[tex]\nmain=X_1.tex\n", "exactly two TeX files"),
        (["X_1.tex", "X_1_eng.tex", "X_1_spa.tex"], "[tex]\nmain=X_1.tex\n", "exactly two TeX files"),
        (["X_1_por.tex", "X_1_eng.tex"], "[tex]\nmain=X_1.tex\n", "does not contain it"),
        (["X_1.tex", "X_1_por.tex"], "[tex]\nmain=X_1_por.tex\n", "the main TeX file must be X_1.tex"),
        (["X_1.tex", "X_1_eng.tex"], "not an ini file", "Cannot read spawn.ini"),
    ),
)
def test_find_translation_refuses_archives_that_do_not_follow_the_rules(names, spawn_ini, error):
    """The archive must have two TeX files at its root, and spawn.ini must name the one named after the paper."""
    with pytest.raises(ValidationError) as exc_info:
        find_translation(names, spawn_ini, "X_1.tex")
    assert exc_info.value.code == INVALID_REQUEST_CODE, exc_info.value
    assert error in exc_info.value.message, exc_info.value.message


def test_find_translation_returns_the_main_file_and_the_other_one():
    """The translation is the TeX file that is not the main one; files in subfolders do not count."""
    names = ["X_1.tex", "X_1_eng.tex", "spawn.ini", "sub/other.tex", "X_1.bib"]
    assert find_translation(names, "[tex]\nmain = X_1.tex\n", "X_1.tex") == ("X_1.tex", "X_1_eng.tex")


# The entry point on published papers


@pytest.mark.django_db
@override_settings(JCOMASSISTANT_URL=JA_URL)
def test_translation_galleys_of_a_published_paper(
    client: Client, published_translated_article: tuple[Article, dict], eo_user: JCOMProfile
):
    """The translation gets the publication data read back from the main TeX, and nothing is saved."""
    article, publication_data = published_translated_article
    Token.objects.create(user=eo_user.janeway_account, key="EOTOKEN")
    before = _snapshot(article)
    fake_ja = FakeJcomassistant(publication_data=publication_data)

    with mock.patch.object(requests, "post", side_effect=fake_ja):
        response = _get(client, article)

    assert response.status_code == 200, response.content
    assert response["Content-Type"] == ZIP_MEDIA_TYPE
    assert f'filename="{_stem(article)}_eng_galleys.zip"' in response["Content-Disposition"]
    assert NOT_COMPILED_HEADER not in response, "Nothing was pre-compiled in the archive"
    assert response.content == _galleys_zip(), "jcomassistant's answer must be returned as it is"

    # The publication data are read from the main TeX of the final sources...
    texdata_name, texdata_content, _mime = fake_ja.calls["texdata"]["files"]["file"]
    assert texdata_name == f"{_stem(article)}.tex"
    assert texdata_content == MAIN_TEX
    # ... and injected as they are (counter included, which the DB would compute differently) into the translation
    pubdata_name, pubdata_content, _mime = fake_ja.calls["pubdata"]["files"]["file"]
    assert pubdata_name == f"{_stem(article)}_eng.tex"
    assert pubdata_content == TRANSLATION_TEX
    assert json.loads(fake_ja.calls["pubdata"]["data"]["data"]) == {"publication_data": publication_data}

    # The galleys are requested from an archive where the injected translation is the main file
    assert parse_qs(urlparse(fake_ja.calls["galleys"]["url"]).query) == {"generate": ["pdf", "epub"]}
    with fake_ja.sent_archive() as sent:
        assert sorted(sent.namelist()) == sorted(
            [
                f"{_stem(article)}.tex",
                f"{_stem(article)}_eng.tex",
                f"{_stem(article)}.bib",
                "figures/image1.png",
                "spawn.ini",
            ]
        )
        assert sent.read(f"{_stem(article)}_eng.tex") == TRANSLATION_TEX + INJECTED_MARK
        assert sent.read(f"{_stem(article)}.tex") == MAIN_TEX
        config = configparser.ConfigParser()
        config.read_string(sent.read("spawn.ini").decode())
        assert config.get("tex", "main") == f"{_stem(article)}_eng.tex"
        assert config.get("pdf", "engine") == "xelatex", "The other settings of spawn.ini must be kept"

    assert _snapshot(article) == before, "Nothing must be saved in the DB nor in the filesystem"


@pytest.mark.django_db
@override_settings(JCOMASSISTANT_URL=JA_URL)
def test_translation_galleys_refuses_a_main_tex_without_publication_data(
    client: Client, published_translated_article: tuple[Article, dict], eo_user: JCOMProfile
):
    """If the main TeX was never injected (e.g. wrong file names at publication), the paper needs a human."""
    article, publication_data = published_translated_article
    Token.objects.create(user=eo_user.janeway_account, key="EOTOKEN")
    fake_ja = FakeJcomassistant(publication_data={**publication_data, "date_published": "???"})

    with mock.patch.object(requests, "post", side_effect=fake_ja):
        response = _get(client, article)

    assert response.status_code == 409, response.content
    assert response.json()["error"]["code"] == "DATA_INTEGRITY_CONFLICT"
    assert "has no publication data" in response.json()["error"]["message"]
    assert "galleys" not in fake_ja.calls, "Nothing must be generated"


@pytest.mark.django_db
@override_settings(JCOMASSISTANT_URL=JA_URL)
@pytest.mark.parametrize(
    "key,value", (("doi", "10.22323/2.99999999"), ("eid", "Z99"), ("date_published", "2020-01-01"))
)
def test_translation_galleys_refuses_publication_data_that_disagree_with_the_db(
    client: Client, published_translated_article: tuple[Article, dict], eo_user: JCOMProfile, key, value
):
    """Publication data changed after publication: someone must decide which ones are right."""
    article, publication_data = published_translated_article
    Token.objects.create(user=eo_user.janeway_account, key="EOTOKEN")
    fake_ja = FakeJcomassistant(publication_data={**publication_data, key: value})

    with mock.patch.object(requests, "post", side_effect=fake_ja):
        response = _get(client, article)

    assert response.status_code == 409, response.content
    assert key in response.json()["error"]["message"]
    assert "galleys" not in fake_ja.calls


@pytest.mark.django_db
@override_settings(JCOMASSISTANT_URL=JA_URL)
def test_translation_galleys_refuses_incomplete_texdata(
    client: Client, published_translated_article: tuple[Article, dict], eo_user: JCOMProfile
):
    """A partial texdata answer is fine only if the publication data are complete."""
    article, publication_data = published_translated_article
    Token.objects.create(user=eo_user.janeway_account, key="EOTOKEN")

    with mock.patch.object(requests, "post", side_effect=FakeJcomassistant(publication_data, texdata_status=206)):
        assert _get(client, article).status_code == 200

    incomplete = {**publication_data, "counter": ""}
    with mock.patch.object(requests, "post", side_effect=FakeJcomassistant(incomplete, texdata_status=206)):
        response = _get(client, article)
    assert response.status_code == 502, response.content
    assert "missing: counter" in response.json()["error"]["message"]


# The entry point on papers in production


@pytest.mark.django_db
@override_settings(JCOMASSISTANT_URL=JA_URL)
def test_translation_galleys_of_a_paper_in_production(
    client: Client, production_translated_article: Article, eo_user: JCOMProfile
):
    """Before publication the data are simulated (as if the paper were published now), and nothing is saved."""
    article = production_translated_article
    Token.objects.create(user=eo_user.janeway_account, key="EOTOKEN")
    before = _snapshot(article)
    assert before["date_published"] is None
    fake_ja = FakeJcomassistant()

    with mock.patch.object(requests, "post", side_effect=fake_ja):
        response = _get(client, article, generate="pdf")

    assert response.status_code == 200, response.content
    assert "texdata" not in fake_ja.calls, "The main TeX has no publication data yet"
    injected = json.loads(fake_ja.calls["pubdata"]["data"]["data"])["publication_data"]
    assert injected["date_published"] == timezone.now().strftime("%Y-%m-%d")
    assert injected["doi"] == article.get_doi()
    assert injected["volume"] and injected["issue"] and injected["counter"], injected
    assert parse_qs(urlparse(fake_ja.calls["galleys"]["url"]).query) == {"generate": ["pdf"]}
    assert _snapshot(article) == before, "The simulation must not leave anything in the DB"


@pytest.mark.django_db
@override_settings(JCOMASSISTANT_URL=JA_URL)
@pytest.mark.parametrize(
    "precompiled,expected_header",
    (
        (".pdf", "pdf"),
        (".epub", "epub"),
        (".html", "epub"),
    ),
)
def test_translation_galleys_reports_precompiled_files(
    client: Client, production_translated_article: Article, eo_user: JCOMProfile, precompiled, expected_header
):
    """jcomassistant does not compile what it finds in the archive: the caller is told so."""
    article = production_translated_article
    Token.objects.create(user=eo_user.janeway_account, key="EOTOKEN")
    _set_sources(article, _sources(article, extra={f"{_stem(article)}_eng{precompiled}": b"stale"}))

    with mock.patch.object(requests, "post", side_effect=FakeJcomassistant()):
        response = _get(client, article)

    assert response.status_code == 200, response.content
    assert response[NOT_COMPILED_HEADER] == expected_header


@pytest.mark.django_db
@override_settings(JCOMASSISTANT_URL=JA_URL)
def test_translation_galleys_ignores_compiled_files_of_the_main_tex(
    client: Client, production_translated_article: Article, eo_user: JCOMProfile
):
    """A PDF of the main file in the archive does not concern the translation."""
    article = production_translated_article
    Token.objects.create(user=eo_user.janeway_account, key="EOTOKEN")
    _set_sources(article, _sources(article, extra={f"{_stem(article)}.pdf": b"main"}))

    with mock.patch.object(requests, "post", side_effect=FakeJcomassistant()):
        response = _get(client, article)

    assert response.status_code == 200, response.content
    assert NOT_COMPILED_HEADER not in response


# Errors


@pytest.mark.django_db
@override_settings(JCOMASSISTANT_URL=JA_URL)
def test_translation_galleys_refuses_bad_requests(
    client: Client, production_translated_article: Article, eo_user: JCOMProfile
):
    """HTML is the main galley only, and the archive must follow the naming convention."""
    article = production_translated_article
    Token.objects.create(user=eo_user.janeway_account, key="EOTOKEN")
    fake_ja = FakeJcomassistant()

    with mock.patch.object(requests, "post", side_effect=fake_ja):
        response = _get(client, article, generate="html")
        assert response.status_code == 400, response.content
        assert response.json()["error"]["details"] == {"allowed": ["pdf", "epub"]}

        _set_sources(article, _sources(article, spawn_ini=f"[tex]\nmain = {_stem(article)}_eng.tex\n"))
        response = _get(client, article)
        assert response.status_code == 400, response.content
        assert f"must be {_stem(article)}.tex" in response.json()["error"]["message"]

        _set_sources(article, _sources(article, without=("spawn.ini",)))
        response = _get(client, article)
        assert response.status_code == 400, response.content

        _set_sources(article, b"not a zip")
        response = _get(client, article)
        assert response.status_code == 400, response.content

    assert not fake_ja.calls, "jcomassistant must not be called for bad requests"


@pytest.mark.django_db
@override_settings(JCOMASSISTANT_URL=JA_URL)
def test_translation_galleys_reports_jcomassistant_failures(
    client: Client, production_translated_article: Article, eo_user: JCOMProfile
):
    """jcomassistant answering badly is a 502."""
    article = production_translated_article
    Token.objects.create(user=eo_user.janeway_account, key="EOTOKEN")

    with mock.patch.object(requests, "post", side_effect=FakeJcomassistant(galleys_status=500)):
        response = _get(client, article)

    assert response.status_code == 502, response.content
    assert response.json()["error"]["code"] == "GALLEY_GENERATION_FAILED"


@pytest.mark.django_db
@override_settings(JCOMASSISTANT_URL=JA_URL)
def test_translation_galleys_needs_the_sources(client: Client, published_translated_article, eo_user: JCOMProfile):
    """A published paper without final sources (e.g. imported from wjapp) cannot be processed."""
    article, _publication_data = published_translated_article
    Token.objects.create(user=eo_user.janeway_account, key="EOTOKEN")
    workflow = article.articleworkflow
    workflow.publication_galleys_source_file = None
    workflow.save()

    response = _get(client, article)

    assert response.status_code == 409, response.content
    assert "no final sources" in response.json()["error"]["message"]


@pytest.mark.django_db
@override_settings(JCOMASSISTANT_URL=JA_URL)
def test_translation_galleys_is_protected(
    client: Client,
    production_translated_article: Article,
    eo_user: JCOMProfile,
    typesetter: JCOMProfile,
    normal_user: JCOMProfile,
):
    """Only EO members and the paper's typesetter can build the galleys of the translation."""
    article = production_translated_article
    Token.objects.create(user=eo_user.janeway_account, key="EOTOKEN")
    Token.objects.create(user=typesetter.janeway_account, key="TYPESETTERTOKEN")
    Token.objects.create(user=normal_user.janeway_account, key="NORMALTOKEN")

    with mock.patch.object(requests, "post", side_effect=FakeJcomassistant()):
        assert client.get(reverse("article-translation-galleys", args=(article.pk,))).status_code == 401
        assert _get(client, article, token="WRONGTOKEN").status_code == 401
        assert _get(client, article, token="NORMALTOKEN").status_code == 403
        assert _get(client, article, token="TYPESETTERTOKEN").status_code == 200
        assert _get(client, article, token="EOTOKEN").status_code == 200
        assert _get(client, Article(pk=article.pk + 1000)).status_code == 404


# The guard on PUT /zip/


def _add_galley(article: Article, label: str, original_filename: str) -> Galley:
    extension = Path(original_filename).suffix.lstrip(".")
    core_file = File.objects.create(
        article_id=article.pk,
        original_filename=original_filename,
        uuid_filename=f"{label}-{original_filename}",
        is_galley=True,
    )
    return Galley.objects.create(file=core_file, label=label, type=extension, article=article)


def _put_zip(client: Client, article: Article, body: bytes):
    return client.put(
        reverse("article-zip", args=(article.pk,)),
        data=body,
        content_type="application/zip",
        HTTP_AUTHORIZATION="Token EOTOKEN",
    )


@pytest.mark.django_db
def test_put_zip_refuses_papers_with_translation_galleys(
    client: Client, published_translated_article: tuple[Article, dict], eo_user: JCOMProfile
):
    """Regenerating the galleys would delete the translation ones: the request is refused, nothing changes."""
    article, _publication_data = published_translated_article
    Token.objects.create(user=eo_user.janeway_account, key="EOTOKEN")
    for label, name in (("PDF (pt)", "a.pdf"), ("PDF (en)", "b.PDF"), ("EPUB (pt)", "a.epub"), ("HTML", "a.html")):
        _add_galley(article, label, name)
    sources_before = article.articleworkflow.publication_galleys_source_file.uuid_filename
    galleys_before = set(article.galley_set.values_list("pk", flat=True))

    with mock.patch.object(requests, "post") as mocked_post:
        response = _put_zip(client, article, _sources(article))

    assert response.status_code == 409, response.content
    error = response.json()["error"]
    assert error["code"] == "TRANSLATION_GALLEYS_PRESENT"
    assert error["details"] == {"duplicated_types": ["pdf"]}
    assert "article manager" in error["message"]
    mocked_post.assert_not_called()
    article.articleworkflow.refresh_from_db()
    assert article.articleworkflow.publication_galleys_source_file.uuid_filename == sources_before
    assert set(article.galley_set.values_list("pk", flat=True)) == galleys_before


@pytest.mark.django_db
def test_put_zip_accepts_papers_with_one_galley_per_type(
    client: Client, published_translated_article: tuple[Article, dict], eo_user: JCOMProfile
):
    """Without translation galleys the guard lets the request through (here, to the archive validation)."""
    article, _publication_data = published_translated_article
    Token.objects.create(user=eo_user.janeway_account, key="EOTOKEN")
    for label, name in (("PDF", "a.pdf"), ("EPUB", "a.epub"), ("HTML", "a.html")):
        _add_galley(article, label, name)

    response = _put_zip(client, article, b"not a zip")

    assert response.status_code == 400, response.content
    assert response.json()["error"]["code"] == "BAD_REQUEST"


@pytest.mark.django_db
def test_translation_galleys_schema(client: Client, eo_user: JCOMProfile):
    """The schema documents the zip answer, the errors and the "generate" parameter."""
    Token.objects.create(user=eo_user.janeway_account, key="EOTOKEN")

    response = client.get(reverse("schema"), {"format": "json"}, HTTP_AUTHORIZATION="Token EOTOKEN")

    operation = response.json()["paths"]["/plugins/wjs-review-articles/api/v1/article/{pk}/translation-galleys/"][
        "get"
    ]
    assert set(operation["responses"]) == {"200", "400", "404", "409", "502"}
    assert set(operation["responses"]["200"]["content"]) == {ZIP_MEDIA_TYPE}
    generate = next(parameter for parameter in operation["parameters"] if parameter["name"] == "generate")
    assert set(generate["schema"]["items"]["enum"]) == {"pdf", "epub"}
