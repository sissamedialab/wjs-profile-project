import io
import random
import tarfile
from pathlib import Path

import pytest
import requests
from core import files
from django.core.files import File as DjangoFile
from django.http import HttpResponse
from django.test import RequestFactory
from identifiers.models import Identifier
from submission.models import Article

from ..logic import ArXivConnectionError, ArXivQueryError, fetch_arxiv_metadata
from ..views import ArxivMicroservice

random.seed(42)


class DummyResponse:
    def __init__(self, content: bytes, status_code: int = 200, text: str = None):
        self.content = content
        self.status_code = status_code
        self.text = text if text is not None else content.decode(errors="ignore")

    def raise_for_status(self):
        if not (200 <= self.status_code < 300):
            raise requests.exceptions.HTTPError(f"{self.status_code}")


def mock_requests_get(monkeypatch, responses: dict = None):
    """Mock requests.get for metadata and file downloads without raising on metadata."""

    def fake_get(url, *args, **kwargs):
        for pattern, response in (responses or {}).items():
            if pattern in url:
                return response
        return DummyResponse(b"", 404)

    monkeypatch.setattr(requests, "get", fake_get)


@pytest.fixture()
def fixtures_data():
    """Return a dictionary of files suitable to simulate several arXiv response scenarios."""
    base = Path(__file__).parent / "files"
    return {
        "xml": (base / "query.atom").open("rb").read(),
        "src": (base / "arxiv_tex_sample.tar.gz").open("rb").read(),
        "xml_empty": b"""<feed xmlns="http://www.w3.org/2005/Atom"></feed>""",
    }


@pytest.mark.django_db
def test_fetch_arxiv_metadata_all_success(fixtures_data, monkeypatch):
    metadata_resp = DummyResponse(fixtures_data["xml"], status_code=200, text=fixtures_data["xml"].decode())
    src_resp = DummyResponse(fixtures_data["src"])
    mock_requests_get(
        monkeypatch,
        responses={"api/query": metadata_resp, "/src/": src_resp},
    )

    result, errors = fetch_arxiv_metadata("2504.10562v1")

    assert result["title"].strip()
    assert result["abstract"].strip()
    assert result["category_term"]

    assert result["arxiv_id"] == "2504.10562v1"

    assert result["source_file"] == fixtures_data["src"]
    assert errors == {}


@pytest.mark.django_db
def test_article_creation(fixtures_data, monkeypatch, tmp_path, journal, author, sections):
    """
    Document how to use arXiv to setup an Article.

    Interesting parts in the code are marked with 🌟
    """
    arxiv_id = "1234.5678v1"
    metadata_resp = DummyResponse(fixtures_data["xml"], status_code=200, text=fixtures_data["xml"].decode())
    src_resp = DummyResponse(fixtures_data["src"])
    mock_requests_get(
        monkeypatch,
        responses={"api/query": metadata_resp, "/src/": src_resp},
    )

    result, errors = fetch_arxiv_metadata(arxiv_id)

    date_started = date_submitted = None
    new_article = Article.objects.create(
        abstract=result["abstract"],  # 🌟 use metadata
        journal=journal,
        title=result["title"],  # 🌟
        correspondence_author=author,  # 🌟 ATM, authors are not treated!
        owner=author,
        date_submitted=date_submitted,
        date_started=date_started,
        section=random.choice(sections),
        language="eng",
    )
    new_article.authors.add(author)
    # 🌟 Save the arXiv id as an identifier of the article
    Identifier.objects.create(
        identifier=arxiv_id,
        article=new_article,
        id_type="arxiv",
    )

    new_article.articleworkflow.arxiv_category = result["category_term"]  # 🌟 use metadata
    new_article.articleworkflow.save()

    assert result["title"] == new_article.title
    assert result["abstract"] == new_article.abstract
    assert arxiv_id == new_article.get_identifier(identifier_type="arxiv")
    assert result["category_term"] == new_article.articleworkflow.arxiv_category

    # 🌟 Save/attach source files
    # for simplicity, suppose that the archive contains only one .tex file
    tar_gz = io.BytesIO(fixtures_data["src"])
    tar_gz.seek(0)
    with tarfile.open(fileobj=tar_gz, mode="r:gz") as tar:
        member = tar.getmembers()[0]
        tex_bytes = tar.extractfile(member).read()

    django_file = DjangoFile(io.BytesIO(tex_bytes), f"Source-{new_article.pk}.tex")

    file_instance = files.save_file_to_article(
        django_file,
        new_article,
        author,
    )
    new_article.source_files.add(file_instance)

    source_file = new_article.source_files.first()
    with open(source_file.self_article_path(), "rb") as f:
        assert f.read() == tex_bytes


@pytest.mark.parametrize(
    "xml_content,expected_msg",
    [
        (b"<invalid><xml>", "XML parse error"),
        (
            b"<?xml version='1.0'?><feed xmlns='http://www.w3.org/2005/Atom'></feed>",
            "cannot be found on arxiv.org",
        ),
        (
            b"<?xml version='1.0'?><feed xmlns='http://www.w3.org/2005/Atom' "
            b"xmlns:arxiv='http://arxiv.org/schemas/atom'>"
            b"<entry><title>Sample</title></entry></feed>",
            "Missing expected element in arXiv response",
        ),
    ],
)
@pytest.mark.django_db
def test_fetch_arxiv_metadata_query_errors(monkeypatch, xml_content, expected_msg):
    def fake_get(url, *args, **kwargs):
        return DummyResponse(xml_content, status_code=200, text=xml_content.decode(errors="ignore"))

    monkeypatch.setattr(requests, "get", fake_get)

    with pytest.raises(ArXivQueryError) as excinfo:
        fetch_arxiv_metadata("0000.0000v1")
    assert expected_msg in str(excinfo.value)


@pytest.mark.django_db
def test_fetch_arxiv_metadata_urlerror(monkeypatch):
    def fake_get(url, *args, **kwargs):
        raise requests.exceptions.RequestException("network down")

    monkeypatch.setattr(requests, "get", fake_get)

    with pytest.raises(ArXivConnectionError) as excinfo:
        fetch_arxiv_metadata("0000.0000v1")
    assert "Connection to arXiv could not be established" in str(excinfo.value)


@pytest.mark.django_db
def test_fetch_arxiv_metadata_file_download_failure(fixtures_data, monkeypatch):
    metadata_resp = DummyResponse(fixtures_data["xml"], status_code=200, text=fixtures_data["xml"].decode())
    src_resp = DummyResponse(b"", status_code=403)
    mock_requests_get(
        monkeypatch,
        {
            "api/query": metadata_resp,
            "/src/": src_resp,
        },
    )

    result, errors = fetch_arxiv_metadata("0000.0000v1")

    assert "source_file" in errors
    assert errors["source_file"] == "HTTP 403"


@pytest.mark.django_db
def test_fetch_arxiv_metadata_connection_request_exception(monkeypatch):
    monkeypatch.setattr(
        requests,
        "get",
        lambda url, *a, **k: (_ for _ in ()).throw(requests.exceptions.RequestException("network down")),
    )
    with pytest.raises(ArXivConnectionError) as excinfo:
        fetch_arxiv_metadata("0000.0000v1")
    assert "Connection to arXiv could not be established" in str(excinfo.value)


@pytest.mark.django_db
def test_fetch_arxiv_metadata_connection_http_error(monkeypatch):  # FIXME
    resp_404 = DummyResponse(b"", status_code=404)
    monkeypatch.setattr(
        requests,
        "get",
        lambda url, *args, **kwargs: resp_404,
    )

    with pytest.raises(ArXivConnectionError) as excinfo:
        fetch_arxiv_metadata("0000.0000v1")
    assert "Connection to arXiv could not be established" in str(excinfo.value)


@pytest.mark.django_db
def test_fetch_arxiv_metadata_doi_extraction(monkeypatch):
    doi_xml = (
        b"<?xml version='1.0'?><feed xmlns='http://www.w3.org/2005/Atom'>"
        b"<entry>"
        b"<id>http://arxiv.org/abs/0000.0000v1</id>"
        b"<title>Test</title>"
        b"<summary>Abstr</summary>"
        b"<arxiv:primary_category xmlns:arxiv='http://arxiv.org/schemas/atom' term='cs.AI'/>"
        b"<link title='doi' href='https://doi.org/10.1000/test'/></entry></feed>"
    )
    meta_resp = DummyResponse(doi_xml, status_code=200, text=doi_xml.decode())
    src_resp = DummyResponse(b"", status_code=403)
    mock_requests_get(monkeypatch, {"api/query": meta_resp, "/src/": src_resp})

    result, errors = fetch_arxiv_metadata("0000.0000v1")
    assert 'href="https://doi.org/10.1000/test"' in result["doi_link"]
    assert errors["source_file"] == "HTTP 403"


@pytest.mark.django_db
def test_fetch_arxiv_metadata_file_download_timeout(fixtures_data, monkeypatch):
    metadata_resp = DummyResponse(fixtures_data["xml"], status_code=200, text=fixtures_data["xml"].decode())

    def fake_get(url, *args, **kwargs):
        if "api/query" in url:
            return metadata_resp
        raise requests.exceptions.Timeout("Request timed out")

    monkeypatch.setattr(requests, "get", fake_get)

    result, errors = fetch_arxiv_metadata("0000.0000")
    assert errors["source_file"] == "Request timed out"


@pytest.mark.django_db
def test_fetch_arxiv_metadata_unexpected_metadata_exception(monkeypatch):
    def fake_get(url, *args, **kwargs):
        raise RuntimeError("unexpected parsing failure")

    monkeypatch.setattr(requests, "get", fake_get)

    with pytest.raises(ArXivQueryError) as excinfo:
        fetch_arxiv_metadata("9999.9999v1")
    assert "unexpected parsing failure" in str(excinfo.value)


@pytest.mark.django_db
def test_fetch_arxiv_metadata_unexpected_download_exception(monkeypatch, fixtures_data):
    class CustomError(Exception):
        pass

    metadata_resp = DummyResponse(fixtures_data["xml"], status_code=200, text=fixtures_data["xml"].decode())

    def fake_get(url, *args, **kwargs):
        if "api/query" in url:
            return metadata_resp
        raise CustomError("unexpected download crash")

    monkeypatch.setattr(requests, "get", fake_get)

    result, errors = fetch_arxiv_metadata("9999.9999")
    assert errors["source_file"] == "unexpected download crash"


@pytest.fixture
def rf():
    return RequestFactory()


def make_request(rf, user, journal, arxiv_id):
    """
    Build a POST request with arxiv_id and attach user/journal.
    """
    req = rf.post("/fake-url/", data={"arxiv_id": arxiv_id})
    req.user = user
    req.journal = journal
    return req


@pytest.mark.django_db
def test_article_creation_and_endpoint(rf, author, journal, fixtures_data, monkeypatch):
    xml_bytes = fixtures_data["xml"]
    meta_resp = DummyResponse(content=xml_bytes, status_code=200, text=xml_bytes.decode("utf-8"))
    src_resp = DummyResponse(content=fixtures_data["src"], status_code=200)

    mock_requests_get(
        monkeypatch,
        responses={
            "export.arxiv.org/api/query": meta_resp,
            "arxiv.org/src/": src_resp,
        },
    )

    request = make_request(rf, author, journal, "1234.5678v1")
    response: HttpResponse = ArxivMicroservice.as_view()(request)

    assert response.status_code == 200
    assert 'Validated for "' in response.content.decode()

    article = Article.objects.get()

    assert article.title.strip() == "Notes on the double Wick rotated BTZ black hole"
    assert article.abstract.strip().startswith("We analyze the double Wick rotated BTZ")

    arxiv_id_obj = Identifier.objects.get(article=article, id_type="arxiv")
    assert arxiv_id_obj.identifier.endswith("v1")

    if "doi.org" in fixtures_data["xml"].decode():
        doi_obj = Identifier.objects.get(article=article, id_type="doi")
        assert doi_obj.identifier.startswith("https://doi.org/")

    assert article.articleworkflow.arxiv_category == "hep-th"

    assert article.source_files.count() == 1
    saved = article.source_files.first().get_file(article, as_bytes=True)
    assert saved == fixtures_data["src"]

    assert article.current_step == 0


@pytest.mark.django_db
def test_not_found_error_bubbles_up_via_empty_feed(rf, author, journal, fixtures_data, monkeypatch):
    empty_meta = DummyResponse(
        content=fixtures_data["xml_empty"], status_code=200, text=fixtures_data["xml_empty"].decode()
    )
    mock_requests_get(monkeypatch, {"export.arxiv.org/api/query": empty_meta})

    request = make_request(rf, author, journal, "9999.99999")
    response: HttpResponse = ArxivMicroservice.as_view()(request)
    body = response.content.decode()

    assert response.status_code == 200
    assert body.startswith("Error: ArXiv query error:")
    assert "cannot be found on arxiv.org" in body


@pytest.mark.django_db
def test_already_used_error_bubbles_up_when_article_exists(rf, author, journal, fixtures_data, monkeypatch):

    good_meta = DummyResponse(content=fixtures_data["xml"], status_code=200, text=fixtures_data["xml"].decode())
    good_src = DummyResponse(content=fixtures_data["src"], status_code=200)
    mock_requests_get(
        monkeypatch,
        {
            "export.arxiv.org/api/query": good_meta,
            "arxiv.org/src/": good_src,
        },
    )
    req1 = make_request(rf, author, journal, "1234.5678v1")
    _ = ArxivMicroservice.as_view()(req1)
    assert Article.objects.count() == 1

    mock_requests_get(
        monkeypatch,
        {
            "export.arxiv.org/api/query": good_meta,
        },
    )
    req2 = make_request(rf, author, journal, "1234.5678v1")
    resp2: HttpResponse = ArxivMicroservice.as_view()(req2)
    body2 = resp2.content.decode()

    assert resp2.status_code == 200
    assert "Error: ArXiv query error: The arXiv ID must not already be in use" in body2


@pytest.mark.django_db
def test_connection_error_bubbles_up_on_requests_timeout(rf, author, journal, fixtures_data, monkeypatch):
    def fail_get(url, *args, **kwargs):
        raise requests.exceptions.Timeout("timed out")

    monkeypatch.setattr(requests, "get", fail_get)

    request = make_request(rf, author, journal, "1234.5678v1")
    response: HttpResponse = ArxivMicroservice.as_view()(request)
    body = response.content.decode()

    assert response.status_code == 200
    assert body.startswith("Error: ArXiv query error:")
    assert "Connection to arXiv could not be established" in body


@pytest.mark.django_db
def test_blank_arxiv_id_still_invokes_fetch_and_import(rf, author, journal, fixtures_data, monkeypatch):
    empty_meta = DummyResponse(
        content=fixtures_data["xml_empty"], status_code=200, text=fixtures_data["xml_empty"].decode()
    )
    mock_requests_get(monkeypatch, {"export.arxiv.org/api/query": empty_meta})

    request = make_request(rf, author, journal, "")
    response: HttpResponse = ArxivMicroservice.as_view()(request)
    body = response.content.decode()

    assert response.status_code == 200
    assert "cannot be found on arxiv.org" in body
