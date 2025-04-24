import io
import random
import tarfile
import urllib.error
import urllib.request
from pathlib import Path

import pytest
import requests
from core import files
from django.core.files import File as DjangoFile
from identifiers.models import Identifier
from submission.models import Article

from ..utils import fetch_arxiv_metadata

random.seed(42)


class DummyResponse:
    def __init__(self, content: bytes, status_code: int = 200):
        self.content = content
        self.status_code = status_code


@pytest.fixture()
def fixtures_data():
    """Return a dictionary of files suitable to simulate several arXiv response scenarios."""
    base = Path(__file__).parent / "files"
    return {
        "xml": (base / "query.atom").open("rb").read(),
        "pdf": (base / "arxiv_pdf_sample.pdf").open("rb").read(),
        "src": (base / "arxiv_tex_sample.tar.gz").open("rb").read(),
    }


@pytest.fixture(autouse=True)
def chdir_tmp(monkeypatch, tmp_path):
    """Change the context of the current working directory during a test."""
    monkeypatch.chdir(tmp_path)


def mock_urlopen(monkeypatch, xml: bytes):
    monkeypatch.setattr(urllib.request, "urlopen", lambda url: io.BytesIO(xml))


def mock_requests_get(monkeypatch, responses: dict = None, raise_exc=None):
    def fake_get(url, *args, **kwargs):
        if raise_exc:
            raise raise_exc
        for pattern, response in responses.items():
            if pattern in url:
                return response
        return DummyResponse(b"", 404)

    monkeypatch.setattr(requests, "get", fake_get)


@pytest.mark.django_db
def test_fetch_arxiv_metadata_all_success(fixtures_data, monkeypatch, tmp_path):
    mock_urlopen(monkeypatch, fixtures_data["xml"])
    mock_requests_get(
        monkeypatch,
        {
            "/pdf/": DummyResponse(fixtures_data["pdf"]),
            "/src/": DummyResponse(fixtures_data["src"]),
        },
    )

    result = fetch_arxiv_metadata("1234.5678")

    assert result["title"].strip()
    assert result["abstract"].strip()
    assert result["category_term"]

    # Reminder: fetch_arxiv_metadata saves the files in the cwd with a name composed of {file_name}-{arxiv_id}{suffix}
    expected_pdf = tmp_path / "pdf_file-1234.5678.pdf"
    expected_src = tmp_path / "source_file-1234.5678.tar.gz"

    assert result["pdf_file"] == expected_pdf.name
    assert result["source_file"] == expected_src.name

    assert expected_pdf.read_bytes() == fixtures_data["pdf"]
    assert expected_src.read_bytes() == fixtures_data["src"]
    assert result["errors"] == {}


@pytest.mark.django_db
def test_article_creation(fixtures_data, monkeypatch, tmp_path, journal, author, sections):
    """
    Document how to use arXiv to setup an Article.

    Interesting parts in the code are marked with 🌟
    """
    arxiv_id = "1234.5678"
    mock_urlopen(monkeypatch, fixtures_data["xml"])
    mock_requests_get(
        monkeypatch,
        {
            "/pdf/": DummyResponse(fixtures_data["pdf"]),
            "/src/": DummyResponse(fixtures_data["src"]),
        },
    )

    # 🌟 Fetch data
    result = fetch_arxiv_metadata(arxiv_id)

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

    # 🌟 Save/attach PDF as manuscript
    file_data = io.BytesIO(fixtures_data["pdf"])
    django_file = DjangoFile(file_data, f"Manuscript-{new_article.pk}.pdf")
    file_instance = files.save_file_to_article(
        django_file,
        new_article,
        author,
    )
    new_article.manuscript_files.add(file_instance)

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

    assert result["title"] == new_article.title
    assert result["abstract"] == new_article.abstract
    assert arxiv_id == new_article.get_identifier(identifier_type="arxiv")
    assert result["category_term"] == new_article.articleworkflow.arxiv_category

    source_file = new_article.source_files.first()
    with open(source_file.self_article_path(), "rb") as f:
        assert f.read() == tex_bytes

    pdf_file = new_article.manuscript_files.first()
    with open(pdf_file.self_article_path(), "rb") as f:
        assert f.read() == fixtures_data["pdf"]

    # 🌟 Clean-up: remember that arXiv files have been saved initially in the cwd. Clean-up if necessary.
    cwd = Path.cwd()
    (cwd / f"pdf_file-{arxiv_id}.pdf").unlink()
    (cwd / f"source_file-{arxiv_id}.tar.gz").unlink()


@pytest.mark.parametrize(
    "xml,expected_error",
    [
        (b"<invalid><xml>", "XML parse error"),
        (b"<?xml version='1.0'?><feed xmlns='http://www.w3.org/2005/Atom'></feed>", "Attribute error"),
        (
            b"""<?xml version='1.0'?>
        <feed xmlns="http://www.w3.org/2005/Atom" xmlns:arxiv="http://arxiv.org/schemas/atom">
            <entry>
                <title>Sample Title</title>
                <arxiv:primary_category term="cs.AI"/>
            </entry>
        </feed>""",
            "Attribute error",
        ),
    ],
)
@pytest.mark.django_db
def test_fetch_arxiv_metadata_query_errors(monkeypatch, xml, expected_error):
    mock_urlopen(monkeypatch, xml)
    result = fetch_arxiv_metadata("0000.0000")
    assert expected_error in result["errors"]["query"]


@pytest.mark.django_db
def test_fetch_arxiv_metadata_urlerror(monkeypatch):
    def raise_urlerror(url):
        raise urllib.error.URLError("network down")

    monkeypatch.setattr(urllib.request, "urlopen", raise_urlerror)

    result = fetch_arxiv_metadata("0000.0000")
    assert result["errors"]["query"] == "URL error: network down"


@pytest.mark.django_db
def test_fetch_arxiv_metadata_file_download_failure(fixtures_data, monkeypatch):
    mock_urlopen(monkeypatch, fixtures_data["xml"])
    mock_requests_get(
        monkeypatch,
        {
            "/pdf/": DummyResponse(b"", 403),
            "/src/": DummyResponse(b"", 403),
        },
    )

    result = fetch_arxiv_metadata("0000.0000")
    assert result["errors"]["pdf_file"] == "HTTP 403"
    assert result["errors"]["source_file"] == "HTTP 403"


@pytest.mark.django_db
def test_fetch_arxiv_metadata_file_download_timeout(fixtures_data, monkeypatch):
    mock_urlopen(monkeypatch, fixtures_data["xml"])
    mock_requests_get(monkeypatch, raise_exc=requests.exceptions.Timeout("Request timed out"))

    result = fetch_arxiv_metadata("0000.0000")
    assert result["errors"]["pdf_file"] == "Request timed out"
    assert result["errors"]["source_file"] == "Request timed out"


@pytest.mark.django_db
def test_fetch_arxiv_metadata_unexpected_metadata_exception(monkeypatch):
    def broken_urlopen(url):
        raise RuntimeError("unexpected parsing failure")

    monkeypatch.setattr(urllib.request, "urlopen", broken_urlopen)

    result = fetch_arxiv_metadata("9999.9999")
    assert result["errors"]["query"] == "unexpected parsing failure"


@pytest.mark.django_db
def test_fetch_arxiv_metadata_unexpected_download_exception(fixtures_data, monkeypatch):
    mock_urlopen(monkeypatch, fixtures_data["xml"])

    class CustomError(Exception):
        pass

    mock_requests_get(monkeypatch, raise_exc=CustomError("unexpected download crash"))

    result = fetch_arxiv_metadata("9999.9999")
    assert result["errors"]["pdf_file"] == "unexpected download crash"
    assert result["errors"]["source_file"] == "unexpected download crash"
