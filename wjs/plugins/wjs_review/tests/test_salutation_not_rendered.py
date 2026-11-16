import importlib
import re

import pytest
from core.models import Account
from django.apps import apps as django_apps
from django.template.loader import render_to_string
from submission.models import Article

from wjs.jcom_profile.templatetags.wjs_tags import authors_fullname_comma_and

from ..logic__production import HandleDownloadRevisionFiles

migration = importlib.import_module("wjs.jcom_profile.migrations.0045_clear_salutation")

SALUTATION = "Dr"


@pytest.fixture
def cleaned_article(assigned_article: Article) -> Article:
    """
    Return an article whose correspondence author had a salutation before the cleanup migration.

    The salutation is set in the DB as legacy data had it, the migration runs, and then the author is
    snapshotted again, as the workflow does on every submission, to check that nothing comes back.
    """
    account = assigned_article.correspondence_author
    Account.objects.filter(pk=account.pk).update(salutation=SALUTATION)
    migration.clear_salutations(django_apps, None)
    account.refresh_from_db()
    account.snapshot_as_author(assigned_article, force_update=True)
    return assigned_article


@pytest.mark.django_db
def test_frozen_author_full_name_has_no_salutation(cleaned_article: Article):
    """The frozen author has no prefix and its full name does not start with the old salutation."""
    frozen_author = cleaned_article.frozen_authors().get(author=cleaned_article.correspondence_author)

    assert frozen_author.name_prefix == "", "No salutation must be copied into the frozen author prefix"
    assert SALUTATION not in frozen_author.full_name().split(), f"Unexpected salutation in {frozen_author.full_name()}"


@pytest.mark.django_db
def test_authors_listing_has_no_salutation(cleaned_article: Article):
    """The "by A, B and C" author listing has no salutation."""
    listing = authors_fullname_comma_and(cleaned_article)

    assert SALUTATION not in listing.split(), f"Unexpected salutation in {listing}"


@pytest.mark.django_db
def test_latex_preamble_has_no_salutation(cleaned_article: Article):
    """A JCOM-style TeX preamble does not render the salutation in the author names."""
    preamble = r"""{% load wjs_tex %}
{% for au in article.frozen_authors %}
\author[{{ au.last_name }},~{{ au.first_name|first }}.]
 {{{ au.full_name|space_to_tilde }}}
{% endfor %}
"""

    rendered = HandleDownloadRevisionFiles.render_latexpreamble(preamble, {"article": cleaned_article})

    assert not re.search(rf"\b{SALUTATION}\b", rendered), f"Unexpected salutation in preamble: {rendered}"


@pytest.mark.django_db
def test_status_page_author_block_has_no_salutation(cleaned_article: Article):
    """The author block of the article status page does not show the salutation."""
    frozen_author = cleaned_article.frozen_authors().get(author=cleaned_article.correspondence_author)

    html = render_to_string(
        "wjs_review/base/elements/author.html",
        {
            "workflow": cleaned_article.articleworkflow,
            "journal": cleaned_article.journal,
            "frozen_author": frozen_author,
            "author": frozen_author.author,
            "corresponding": True,
        },
    )

    assert not re.search(rf"\b{SALUTATION}\b", html), "Unexpected salutation in the author block"
