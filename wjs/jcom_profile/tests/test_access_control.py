"""Access-control regression tests for views fixed in MR !1479.

Covers the anonymous-access gaps recorded in
``docs/superpowers/specs/2026-08-28-anonymous-view-access-design.md``:

- IMU (Insert Many Users) views and experimental force-graph views must
  reject anonymous requests (``LoginRequiredMixin``).
- ``PublishedArticlesListView`` must honour a journal's ``disable_front_end``
  flag (``frontend_enabled`` decorator) while remaining anonymous-accessible
  otherwise.
"""

import pytest
from django.urls import reverse


@pytest.mark.django_db
@pytest.mark.parametrize("url_name", ("si-imu-1", "si-imu-2", "si-imu-3"))
def test_imu_views_reject_anonymous(client, journal, special_issue, url_name):
    """An anonymous request to any IMU step must redirect to login, not proceed."""
    url = reverse(url_name, kwargs={"pk": special_issue.id})
    response = client.get(url)

    assert response.status_code == 302
    assert response.url.startswith(reverse("core_login"))


@pytest.mark.django_db
def test_imu_step1_accessible_when_logged_in(client, journal, special_issue, admin):
    """A logged-in user can still reach IMUStep1 (regression check for the added mixin)."""
    client.force_login(admin)
    url = reverse("si-imu-1", kwargs={"pk": special_issue.id})
    response = client.get(url)

    assert response.status_code == 200


@pytest.mark.django_db
@pytest.mark.parametrize(
    "path_suffix",
    (
        "experimental/issues",
        "experimental/authors_by_coa",
        "experimental/authors_by_kwd",
        "experimental/articles_by_kwd",
    ),
)
def test_experimental_views_reject_anonymous(client, journal, path_suffix):
    """An anonymous request to any experimental force-graph view must redirect to login.

    Hit the paths directly rather than via `reverse()`: "authors_forcegraph" is
    registered twice in `experimental_urls` (AuthorsForceGraph and
    AuthorsKeywordsForceGraph share the name), so `reverse()` can't
    disambiguate between them.
    """
    url = f"/{journal.code}/{path_suffix}"
    response = client.get(url)

    assert response.status_code == 302
    assert response.url.startswith(reverse("core_login"))


@pytest.mark.django_db
@pytest.mark.parametrize(
    "path_suffix",
    (
        "experimental/issues",
        "experimental/authors_by_coa",
        "experimental/authors_by_kwd",
        "experimental/articles_by_kwd",
    ),
)
def test_experimental_views_accessible_when_logged_in(client, journal, admin, path_suffix):
    """A logged-in user can still reach the experimental views (regression check for the added mixin)."""
    client.force_login(admin)
    url = f"/{journal.code}/{path_suffix}"
    response = client.get(url)

    assert response.status_code == 200


@pytest.mark.django_db
@pytest.mark.parametrize("url_name", ("search", "journal_articles"))
def test_published_articles_list_view_redirects_when_frontend_disabled(client, journal, url_name):
    """PublishedArticlesListView must honour `journal.disable_front_end` like Janeway's own version does."""
    journal.disable_front_end = True
    journal.save()

    url = reverse(url_name)
    response = client.get(url)

    assert response.status_code == 302
    assert response.url == reverse("website_index")


@pytest.mark.django_db
@pytest.mark.parametrize("url_name", ("search", "journal_articles"))
def test_published_articles_list_view_accessible_anonymously_when_frontend_enabled(client, journal, url_name):
    """Baseline: with the front end enabled (the default), anonymous access still works."""
    assert journal.disable_front_end is False

    url = reverse(url_name)
    response = client.get(url)

    assert response.status_code == 200
