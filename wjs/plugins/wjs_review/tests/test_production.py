from typing import Callable

import pytest
from django.http import HttpRequest
from django.test.client import Client
from django.urls import reverse
from django.utils import timezone
from journal.models import Journal
from press.models import Press
from submission import models as submission_models
from submission.models import Article

from wjs.jcom_profile.models import JCOMProfile
from wjs.jcom_profile.tests.conftest import _journal_factory

from ..views import TypesetterPending
from ..views__production import TypesetterWorkingOn
from .conftest import (
    _accept_article,
    _assign_article,
    _assigned_to_typesetter_article,
    _ready_for_typesetter_article,
)


@pytest.mark.django_db
def test_typesetters_can_access_codone(
    client: Client,
    normal_user: JCOMProfile,
    eo_user: JCOMProfile,
    typesetter: JCOMProfile,
    assigned_article: submission_models.Article,
):
    """Test that typesetters and EO can access the pile of papers ready for typesetters."""
    url = reverse("wjs_review_typesetter_pending")
    client.force_login(normal_user.janeway_account)
    response = client.get(url)
    assert response.status_code == 403

    client.force_login(eo_user.janeway_account)
    response = client.get(url)
    assert response.status_code == 200

    client.force_login(typesetter.janeway_account)
    response = client.get(url)
    assert response.status_code == 200


@pytest.mark.django_db
def test_codone_lists_papers_from_all_journals(
    client: Client,
    press: Press,
    journal: Journal,
    director: JCOMProfile,  # only needed to silence the JCOM-has-no-director error message
    eo_user: JCOMProfile,
    typesetter: JCOMProfile,
    create_jcom_user: Callable,
    ready_for_typesetter_article: Article,
    article_factory: Callable,
    fake_request: HttpRequest,
):
    """Test that the pile has all papers ready for typesetter for all journals that a user is typesetter of."""
    # We need to setup a scenario with:
    # - 2 journals
    # - 2 ready-for-typ articles (one for each journal)
    # - 2 typesetters:
    #   - typ_1 is typesetter both on journal_1 and journal_2
    #   - typ_2 is typesetter only on journal_2
    # We can thus test that, visiting the codone on journal_1
    # - typ_1 sees both papers
    # - typ_2 sees only the paper from journal_2
    #
    # NB: the facts that
    # - typ_2 is related only to journal_2
    # - we will visit urls always from journal_1 (e.g. http://jcom..., not http://jcomal...)
    # should ensure that the url/journal from which we are listing the articles does invalidate the test

    # By using the fixtures, we have a paper ready for typesetter and a typesetter in JCOM.
    article_1 = ready_for_typesetter_article  # just an alias

    typesetter_role_slug = "typesetter"
    journal_2 = _journal_factory("JCOMAL", press, domain="jcomal.sissa.it")
    # Add a director to the second journal
    # only needed to silence the JCOMAL-has-no-director error message
    director_role_slug = "director"
    director.add_account_role(director_role_slug, journal_2)
    typesetter_2 = create_jcom_user("typesetter 2")
    typesetter_2.add_account_role(typesetter_role_slug, journal_2)
    typesetter.add_account_role(typesetter_role_slug, journal_2)

    # Use the same editor for both articles
    # NB: the user must have editor or section-editor role in both journals!
    editor = article_1.editorassignment_set.last().editor
    editor_role_slug = "section-editor"
    editor.add_account_role(editor_role_slug, journal_2)
    fake_request.user = editor

    title_2 = "Uncommon title 2222 that I can scrape"
    article_2 = article_factory(
        journal=journal_2,
        correspondence_author=article_1.correspondence_author,
        title=title_2,
    )
    _assign_article(fake_request, article_2, editor)
    article_2.refresh_from_db()
    _accept_article(fake_request, article_2)
    article_2.refresh_from_db()
    _ready_for_typesetter_article(article_2)
    article_2.refresh_from_db()

    # Let's test:
    # the user that is typesetter in all journals sees all papers
    view = TypesetterPending()
    fake_request.user = typesetter.janeway_account
    view.request = fake_request
    view.kwargs = {}
    article_ids_of_views_qs = view.get_queryset().values_list("id", flat=True)
    assert article_1.id in article_ids_of_views_qs
    assert article_2.id in article_ids_of_views_qs

    # the user that is typesetter only in journal_2 sees only article_2
    view.request.user = typesetter_2.janeway_account
    article_ids_of_views_qs = view.get_queryset().values_list("id", flat=True)
    assert article_1.id not in article_ids_of_views_qs
    assert article_2.id in article_ids_of_views_qs

    # EO sees all
    view.request.user = eo_user.janeway_account
    article_ids_of_views_qs = view.get_queryset().values_list("id", flat=True)
    assert article_1.id in article_ids_of_views_qs
    assert article_2.id in article_ids_of_views_qs


@pytest.mark.django_db
def test_typesetter_workingon_lists_active_papers(
    client: Client,
    press: Press,
    journal: Journal,
    director: JCOMProfile,  # only needed to silence the JCOM-has-no-director error message
    eo_user: JCOMProfile,
    typesetter: JCOMProfile,
    create_jcom_user: Callable,
    assigned_to_typesetter_article: Article,
    article_factory: Callable,
    fake_request: HttpRequest,
):
    """Test that the main page for a typesetter is showing active papers of the typesetter.

    In this simple scenario, we have one typesetter with one active paper, one done and one not assigned. The view
    should list only the assigned paper.

    """
    # By using the fixtures, we have a paper assigned to the typesetter.
    # The other two, we manually create.
    article_1 = assigned_to_typesetter_article  # just an alias

    # Use the same author and editor for all articles (we don't care here)
    # article_2 is the one ready_for_typesetter
    article_2 = article_factory(
        journal=journal,
        correspondence_author=article_1.correspondence_author,
    )
    editor = article_1.editorassignment_set.last().editor
    _assign_article(fake_request, article_2, editor)
    article_2.refresh_from_db()
    fake_request.user = editor
    _accept_article(fake_request, article_2)
    article_2.refresh_from_db()
    _ready_for_typesetter_article(article_2)
    article_2.refresh_from_db()

    # article_3 is the one assigned to the typesetter and completed
    article_3 = article_factory(
        journal=journal,
        correspondence_author=article_1.correspondence_author,
    )
    editor = article_1.editorassignment_set.last().editor
    _assign_article(fake_request, article_3, editor)
    article_3.refresh_from_db()
    fake_request.user = editor
    _accept_article(fake_request, article_3)
    article_3.refresh_from_db()
    _ready_for_typesetter_article(article_3)
    article_3.refresh_from_db()
    _assigned_to_typesetter_article(article_3, typesetter, fake_request)
    article_3.refresh_from_db()
    typesetting_assignment = article_3.typesettinground_set.first().typesettingassignment
    typesetting_assignment.completed = timezone.now()
    typesetting_assignment.save()

    # Let's test:
    # the user that is typesetter in all journals sees all papers
    view = TypesetterWorkingOn()
    fake_request.user = typesetter.janeway_account
    view.request = fake_request
    view.kwargs = {}
    article_ids_of_views_qs = view.get_queryset().values_list("id", flat=True)
    assert article_1.id in article_ids_of_views_qs
    assert article_2.id not in article_ids_of_views_qs
    assert article_3.id not in article_ids_of_views_qs
