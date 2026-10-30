"""Test that WJS url overrides do not shadow Janeway's urls."""

import pytest
from django.urls import resolve


@pytest.mark.parametrize(
    "url,url_name",
    (
        ("/article/id/4174/file/42348/", "article_file_download"),
        ("/article/id/4174/file/42348/replace", "article_file_replace"),
        ("/article/id/4174/file_history/42348/", "article_file_history_download"),
        ("/article/id/4174/download/pdf/", "serve_article_pdf"),
        ("/article/id/4174/print/", "article_print_article"),
        ("/article/pubid/JCOM_2401_2025_A01/table/t1", "article_table"),
        ("/article/pubid/JCOM_2401_2025_A01/fig1.png", "article_figure"),
        ("/article/pubid/JCOM_2401_2025_A01(it)/fig1.png", "article_figure"),
        ("/article/pubid/JCOM_2401_2025_A01(it)/", "article_view"),
    ),
)
def test_article_figure_does_not_shadow_janeway_urls(url, url_name):
    """The "article_figure" override catches only figure file names, not other article sub-paths."""
    match = resolve(url)
    assert match.url_name == url_name, f"{url} resolved to {match.url_name} ({match.func}) instead of {url_name}"
