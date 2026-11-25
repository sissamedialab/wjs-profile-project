import shutil
import subprocess

import pytest
from identifiers.models import Identifier
from journal.models import Journal
from plugins.wjs_submission.models import (
    ArticleCollaboration,
    Collaboration,
    CollaborationRelation,
)
from submission.models import Article

from ..logic__production import HandleDownloadRevisionFiles
from ..templatetags.wjs_tex import collaborations, identifier, latex_safe


@pytest.mark.django_db
def test_collaborations_filter_returns_only_by_relation(journal: Journal):
    """
    Test that collaborations() returns only names of collabs linked with the "by" relation.

    We create these articles/relations:
    - a1 - no relation to any collab
    - a2 - related to collab c1 with collaborationrelation "by"
    - a3 - related to collab c2 with "on-behalf"
    - a4 - related to collab c3 and c4 with "by" and to c5 with "on-behalf"
    """
    c1 = Collaboration.objects.create(name="c1", creator=None)
    c2 = Collaboration.objects.create(name="c2", creator=None)
    c3 = Collaboration.objects.create(name="c3", creator=None)
    c4 = Collaboration.objects.create(name="c4", creator=None)
    c5 = Collaboration.objects.create(name="c5", creator=None)

    a1 = Article.objects.create(title="a1", journal=journal)
    a2 = Article.objects.create(title="a2", journal=journal)
    a3 = Article.objects.create(title="a3", journal=journal)
    a4 = Article.objects.create(title="a4", journal=journal)

    ArticleCollaboration.objects.create(article=a2, collaboration=c1, relation=CollaborationRelation.BY)
    ArticleCollaboration.objects.create(article=a3, collaboration=c2, relation=CollaborationRelation.ON_BEHALF_OF)
    ArticleCollaboration.objects.create(article=a4, collaboration=c3, relation=CollaborationRelation.BY)
    ArticleCollaboration.objects.create(article=a4, collaboration=c4, relation=CollaborationRelation.BY)
    ArticleCollaboration.objects.create(article=a4, collaboration=c5, relation=CollaborationRelation.ON_BEHALF_OF)

    assert set(collaborations(a1)) == set()
    assert set(collaborations(a2)) == {"c1"}
    assert set(collaborations(a3)) == set()
    assert set(collaborations(a4)) == {"c3", "c4"}


@pytest.mark.django_db
def test_identifier_filter_returns_identifier_value(journal: Journal):
    """Test that identifier() returns the value of the identifier of the requested type, or an empty string."""
    article = Article.objects.create(title="a1", journal=journal)
    Identifier.objects.create(article=article, id_type="protonid", identifier="JCAP_123P_0525")
    Identifier.objects.create(article=article, id_type="doi", identifier="10.1088/1475-7516/2025/05/001")

    assert identifier(article, "protonid") == "JCAP_123P_0525", "Wrong proton id"
    assert identifier(article, "doi") == "10.1088/1475-7516/2025/05/001", "Wrong DOI"
    assert identifier(article, "arxiv") == "", "Missing identifier should render as empty string"

    rendered = HandleDownloadRevisionFiles.render_latexpreamble(
        "{% load wjs_tex %}\\IOPprotonid{{{ article|identifier:'protonid' }}}",
        {"article": article},
    )
    assert rendered == "\\IOPprotonid{JCAP_123P_0525}", f"Unexpected rendering: {rendered}"


# Titles taken from real JCAP papers whose reports failed to compile (wjs/specs#3298)
LATEX_UNSAFE_TITLES = [
    # unicode Greek, in and out of math mode
    ("Quintessential $α$-attractors fit DESI", r"Quintessential ${\ensuremath{\alpha}}$-attractors fit DESI"),
    ("Beyond ΛCDM", r"Beyond {\ensuremath{\Lambda}}CDM"),
    # unicode Greek as a subscript or as a macro argument: needs the braces
    ("Bias $b_φ$", r"Bias $b_{\ensuremath{\varphi}}$"),
    ("a ${\\overlineν}_{e}$ detector", r"a ${\overline{\ensuremath{\nu}}}_{e}$ detector"),
    # HTML entities, and an unescaped % that would comment out the rest of the title
    ("beyond $w&#060;-1/3$", "beyond $w<-1/3$"),
    ("A &lt; 3% accurate approximation", r"A < 3\% accurate approximation"),
    ("Dark &amp; light #1", r"Dark \& light \#1"),
]

LATEX_SAFE_TITLES = [
    "A plain title",
    r"Exact Integrable $\Lambda$CDM-Mimicking $f(Q)$ Cosmology",
    r"Already escaped: 3\% \& \#1",
    r"SN1987A Constraints of Light $\boldsymbol{Z'}$",
]


@pytest.mark.parametrize("title,expected", LATEX_UNSAFE_TITLES)
def test_latex_safe_fixes_pesky_chars(title: str, expected: str):
    """Test that latex_safe() decodes HTML entities, escapes bare specials and converts unicode chars."""
    assert latex_safe(title) == expected, f"Unexpected conversion of {title!r}"


@pytest.mark.parametrize("title", LATEX_SAFE_TITLES)
def test_latex_safe_keeps_latex_markup(title: str):
    """Test that latex_safe() leaves alone ASCII titles that already are valid LaTeX."""
    assert latex_safe(title) == title, f"latex_safe() should not change {title!r}"


@pytest.mark.parametrize("title", [None, ""])
def test_latex_safe_empty(title: str | None):
    """Test that latex_safe() renders a missing title as an empty string, not as "None"."""
    assert latex_safe(title) == "", "Empty title should render as empty string"


def test_latex_safe_is_not_autoescaped():
    """Test that the output of latex_safe is not HTML-escaped by Django, e.g. ' must not become &#x27;."""
    rendered = HandleDownloadRevisionFiles.render_latexpreamble(
        "{% load wjs_tex %}Title: {{{ title|latex_safe }}}",
        {"title": "Light $\\boldsymbol{Z'}$ & <more>"},
    )
    assert rendered == "Title: {Light $\\boldsymbol{Z'}$ \\& <more>}", f"Unexpected rendering: {rendered}"


@pytest.mark.skipif(not shutil.which("pdflatex"), reason="pdflatex not available")
@pytest.mark.parametrize("title", [title for title, _ in LATEX_UNSAFE_TITLES] + LATEX_SAFE_TITLES)
def test_latex_safe_compiles(title: str, tmp_path):
    """Test that titles processed by latex_safe() compile with pdflatex and a minimal preamble."""
    tex = (
        "\\documentclass{article}\n\\usepackage{amsmath}\n\\usepackage{amssymb}\n\\usepackage{bm}\n"
        "\\begin{document}\n" + latex_safe(title) + "\n\\end{document}\n"
    )
    (tmp_path / "t.tex").write_text(tex)
    result = subprocess.run(
        ["pdflatex", "-interaction=nonstopmode", "-halt-on-error", "t.tex"],
        cwd=tmp_path,
        capture_output=True,
        text=True,
        errors="replace",
    )
    assert result.returncode == 0, f"{title!r} does not compile:\n{result.stdout[-1000:]}"
