import logging
from typing import Callable

import pytest
from django.conf import settings
from django.contrib.contenttypes.models import ContentType
from django.contrib.messages import get_messages
from django.core import mail
from django.http import HttpRequest
from django.test.client import Client
from django.urls import reverse
from journal.models import Journal
from plugins.typesetting.models import GalleyProofing
from plugins.wjs_review.states import BaseState
from press.models import Press
from submission import models as submission_models
from submission.models import Article

from wjs.jcom_profile.models import JCOMProfile
from wjs.jcom_profile.tests.conftest import _journal_factory

from ..communication_utils import get_eo_user
from ..logic__production import TypesetterTestsGalleyGeneration
from ..models import ArticleWorkflow, Message, MessageThread, WjsEditorAssignment
from ..views__production import TypesetterPending, TypesetterWorkingOn
from .conftest import (
    _accept_article,
    _assign_article,
    _assigned_to_typesetter_article,
    _ready_for_typesetter_article,
    _stage_proofing_article,
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
    editor = WjsEditorAssignment.objects.get_current(article_1).editor
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
    ready_for_typesetter_article: Article,
    article_factory: Callable,
    fake_request: HttpRequest,
):
    """Test that the main page for a typesetter is showing active papers of the typesetter.

    In this simple scenario, we have one typesetter with
    - one active paper in typesetting
    - one active paper to the author for proofs
    - TODO: one done (in ready for publication); TODO after specs#692 or specs#778
    - one not assigned
    The view should list only the two active papers.

    """
    # NB: do _not_ use the fixtures
    # - ready_for_typesetter_article
    # - assigned_to_typesetter_article
    # - stage_proofing_article
    # as if they are different articles in different stages:
    # they all work on the _same_ article!

    author = ready_for_typesetter_article.correspondence_author
    editor = ready_for_typesetter_article.editorassignment_set.last().editor

    fake_request.user = editor

    assigned_to_typesetter_article = _assigned_to_typesetter_article(
        typesetter=typesetter,
        fake_request=fake_request,
        article=_ready_for_typesetter_article(
            article=_accept_article(
                fake_request=fake_request,
                article=_assign_article(
                    fake_request=fake_request,
                    section_editor=editor,
                    article=article_factory(
                        journal=journal,
                        correspondence_author=author,
                    ),
                ),
            ),
        ),
    )

    stage_proofing_article = _stage_proofing_article(
        typesetter=typesetter,
        fake_request=fake_request,
        article=_assigned_to_typesetter_article(
            typesetter=typesetter,
            fake_request=fake_request,
            article=_ready_for_typesetter_article(
                article=_accept_article(
                    fake_request=fake_request,
                    article=_assign_article(
                        fake_request=fake_request,
                        section_editor=editor,
                        article=article_factory(
                            journal=journal,
                            correspondence_author=author,
                        ),
                    ),
                ),
            ),
        ),
    )

    view = TypesetterWorkingOn()
    fake_request.user = typesetter.janeway_account
    view.request = fake_request
    view.kwargs = {}
    article_ids_of_views_qs = view.get_queryset().values_list("id", flat=True)
    assert ready_for_typesetter_article.id not in article_ids_of_views_qs
    assert assigned_to_typesetter_article.id in article_ids_of_views_qs
    assert stage_proofing_article.id in article_ids_of_views_qs


@pytest.mark.django_db
def test_au_writes_to_typ(
    assigned_to_typesetter_article: Article,
    client: Client,
):
    """Author can write to typesetter without explicitly setting the recipient."""
    content_type = ContentType.objects.get_for_model(assigned_to_typesetter_article)
    object_id = assigned_to_typesetter_article.pk
    assert not Message.objects.filter(
        content_type=content_type,
        object_id=object_id,
    ).exists()

    url = reverse("wjs_message_write_to_typ", kwargs={"pk": assigned_to_typesetter_article.pk})
    data = {
        "subject": "A subject",
        "body": "A body",
    }
    author = assigned_to_typesetter_article.correspondence_author
    client.force_login(author)
    response = client.post(url, data=data)
    assert response.status_code == 302  # POST redirects to "details" page

    messages = Message.objects.filter(content_type=content_type, object_id=object_id)
    assert messages.count() == 1

    typesetter = assigned_to_typesetter_article.typesettinground_set.first().typesettingassignment.typesetter
    assert messages.filter(recipients__in=[typesetter]).count() == 1


@pytest.mark.django_db
def test_typ_writes_to_au(
    assigned_to_typesetter_article: Article,
    client: Client,
):
    """When typ writes to author, a message is created that goes to EO and that should be forwarded to the author."""
    content_type = ContentType.objects.get_for_model(assigned_to_typesetter_article)
    object_id = assigned_to_typesetter_article.pk
    assert not Message.objects.filter(
        content_type=content_type,
        object_id=object_id,
    ).exists()

    url = reverse("wjs_message_write_to_auwm", kwargs={"pk": assigned_to_typesetter_article.pk})
    data = {
        "subject": "A subject",
        "body": "A body",
    }
    typesetter = assigned_to_typesetter_article.typesettinground_set.first().typesettingassignment.typesetter
    client.force_login(typesetter)
    response = client.post(url, data=data)
    assert response.status_code == 302  # POST redirects to "details" page

    messages = Message.objects.filter(content_type=content_type, object_id=object_id)
    assert messages.count() == 1
    message = messages.first()

    assert set(message.recipients.all()) == {get_eo_user(assigned_to_typesetter_article)}
    author = assigned_to_typesetter_article.correspondence_author
    assert message.to_be_forwarded_to == author


@pytest.mark.django_db
def test_eo_forwards_msg(
    assigned_to_typesetter_article: Article,
    client: Client,
    eo_user: JCOMProfile,
):
    """When EO forwards a message, the original message is not changed and a new message is created."""
    content_type = ContentType.objects.get_for_model(assigned_to_typesetter_article)
    object_id = assigned_to_typesetter_article.pk
    assert not Message.objects.filter(
        content_type=content_type,
        object_id=object_id,
    ).exists()

    # Simulate a message that should be forwarded.
    typesetter = assigned_to_typesetter_article.typesettinground_set.first().typesettingassignment.typesetter
    author = assigned_to_typesetter_article.correspondence_author
    m1 = Message.objects.create(
        content_type=content_type,
        object_id=object_id,
        subject="A subject",
        body="A body",
        actor=typesetter,
        to_be_forwarded_to=author,
    )
    m1.recipients.add(eo_user.janeway_account)

    url = reverse("wjs_message_forward", kwargs={"original_message_pk": m1.pk})
    data = {
        "subject": "A subject EDITED",
        "body": "A body EDITED",
    }
    client.force_login(eo_user.janeway_account)
    response = client.post(url, data=data)
    assert response.status_code == 302  # POST redirects to "details" page
    assert "/login" not in response.url

    messages = Message.objects.filter(content_type=content_type, object_id=object_id).order_by("created")
    assert messages.count() == 2
    assert m1 == messages.first()
    m2 = messages.last()

    assert set(m2.recipients.all()) == {author}
    assert m2.to_be_forwarded_to is None

    m1m2_relation = MessageThread.objects.get(parent_message=m1, child_message=m2)
    assert m1m2_relation.relation_type == MessageThread.MessageRelation.FORWARD


@pytest.mark.django_db
def test_author_sends_corrections(
    stage_proofing_article: Article,
    client: Client,
):
    typesetting_assignment = stage_proofing_article.typesettinground_set.first().typesettingassignment
    url = reverse("wjs_author_sends_corrections", kwargs={"pk": typesetting_assignment.pk})
    client.force_login(stage_proofing_article.correspondence_author)
    galleyproofing = (
        GalleyProofing.objects.filter(
            round__article=stage_proofing_article,
        )
        .order_by("round__round_number")
        .last()
    )
    response = client.get(url)
    assert response.status_code == 302
    messages = list(get_messages(response.wsgi_request))
    assert any("Data not provided" in message.message for message in messages)

    galleyproofing.notes = "Some notes"
    galleyproofing.save()
    galleyproofing.refresh_from_db()
    response = client.get(url)
    assert response.status_code == 302
    messages = list(get_messages(response.wsgi_request))
    assert any("Corrections have been dispatched" in message.message for message in messages)

    stage_proofing_article.refresh_from_db()
    assert stage_proofing_article.articleworkflow.state == ArticleWorkflow.ReviewStates.TYPESETTER_SELECTED


@pytest.mark.django_db
def test_typ_marks_unpublishable(
    assigned_to_typesetter_article: Article,
    client: Client,
):
    url = reverse("wjs_toggle_publishable", kwargs={"pk": assigned_to_typesetter_article.articleworkflow.pk})
    typesetter = assigned_to_typesetter_article.typesettinground_set.first().typesettingassignment.typesetter
    client.force_login(typesetter)
    assert assigned_to_typesetter_article.articleworkflow.production_flag_no_checks_needed
    client.post(url)
    assigned_to_typesetter_article.refresh_from_db()
    assert not assigned_to_typesetter_article.articleworkflow.production_flag_no_checks_needed
    client.post(url)
    assigned_to_typesetter_article.refresh_from_db()
    assert assigned_to_typesetter_article.articleworkflow.production_flag_no_checks_needed


@pytest.mark.django_db
def test_typesetter_galley_generation(
    assigned_to_typesetter_article_with_files_to_typeset: Article,
    client: Client,
    mock_jcomassistant_post,
    fake_request: HttpRequest,
    caplog,
):
    """Test della vista di generazione dei galleys con mock di JcomAssistantClient."""
    typesetting_assignment = (
        assigned_to_typesetter_article_with_files_to_typeset.typesettinground_set.first().typesettingassignment
    )
    url = reverse("wjs_typesetter_galley_generation", kwargs={"pk": typesetting_assignment.pk})
    client.force_login(typesetting_assignment.typesetter)
    response = client.get(url)

    assert mock_jcomassistant_post.call_args.kwargs["url"] == settings.JCOMASSISTANT_URL
    assert response.status_code == 200
    assert "article" in response.context
    assert response.context["article"] == assigned_to_typesetter_article_with_files_to_typeset

    galleys_created = typesetting_assignment.galleys_created.all()
    assert galleys_created.count() == 2
    assert any(galley.file.original_filename.endswith(".html") for galley in galleys_created)
    assert any(galley.file.original_filename.endswith(".epub") for galley in galleys_created)

    typesetting_assignment.files_to_typeset.all().delete()
    fake_request.user = typesetting_assignment.typesetter
    mail.outbox = []
    caplog.set_level(logging.ERROR)
    TypesetterTestsGalleyGeneration(typesetting_assignment, fake_request).run()
    assert len(mail.outbox) == 1
    assert "galley generation failed to start" in mail.outbox[0].subject
    assert "Galley generation failed to start" in caplog.text


@pytest.mark.django_db
def test_record_of_state_change(
    assigned_to_typesetter_article: Article,
):
    """On state change, the date of change is recorded."""
    # We test this on a random state. Any would do.
    workflow = assigned_to_typesetter_article.articleworkflow
    record_me = workflow.latest_state_change

    workflow.state = ArticleWorkflow.ReviewStates.PROOFREADING
    workflow.refresh_from_db()
    assert workflow.latest_state_change == record_me

    workflow.typesetter_submits()
    workflow.refresh_from_db()
    assert workflow.latest_state_change > record_me


@pytest.mark.parametrize("user_is_author", (True, False))
@pytest.mark.django_db
def test_author_deems_paper_rfp(stage_proofing_article: Article, client, user_is_author: bool):
    """The author can deem rft only articles that have all production flags in the expected state."""
    workflow = stage_proofing_article.articleworkflow

    if user_is_author:
        operator = stage_proofing_article.correspondence_author
        # ugly hack (?) author and typ can do the same action with the same conditions,
        # but from different states, so I have to force the state
        initial_state = ArticleWorkflow.ReviewStates.PROOFREADING
    else:
        operator = stage_proofing_article.typesettinground_set.first().typesettingassignment.typesetter
        initial_state = ArticleWorkflow.ReviewStates.TYPESETTER_SELECTED

    client.force_login(operator)
    workflow.state = initial_state
    workflow.save()

    # article is not "ready"
    assert workflow.production_flag_no_queries is False
    assert workflow.production_flag_galleys_ok is False
    assert workflow.production_flag_no_checks_needed is True
    assert workflow.can_be_set_rfp() is False

    # the rfp action should not be visible to the author in the status page
    # TODO: do we have any preference for the typesetter?
    if user_is_author:
        url = reverse("wjs_article_details", kwargs={"pk": stage_proofing_article.articleworkflow.pk})
        response = client.get(url)
        assert response.status_code == 200
        state_class = BaseState.get_state_class(workflow)
        action = state_class.get_action_by_name("author_deems_paper_ready_for_publication")
        assert action.label not in response.content.decode()

        # TODO: drop che client.get + response.content stuff and just do
        actions_available_to_the_user_in_this_state = [
            action for action in state_class.article_actions if action.is_available(workflow, operator)
        ]
        assert action not in actions_available_to_the_user_in_this_state

    # even if the author manages to run the action, the process ends in a well-behaved error
    url = reverse("wjs_review_rfp", kwargs={"pk": stage_proofing_article.articleworkflow.pk})
    response = client.get(url)
    assert response.status_code == 302

    messages = list(get_messages(response.wsgi_request))
    assert any("Paper not yet ready for publication" in message.message for message in messages)
    assert workflow.state == initial_state

    # not, let's make the paper ready
    workflow.production_flag_no_queries = True
    workflow.production_flag_galleys_ok = True
    workflow.save()
    assert workflow.can_be_set_rfp() is True

    response = client.get(url)
    assert response.status_code == 302

    workflow.refresh_from_db()
    assert workflow.state == ArticleWorkflow.ReviewStates.READY_FOR_PUBLICATION


@pytest.mark.django_db
def test_eo_sends_back_to_typesetter(
    stage_proofing_article: Article,
    client: Client,
    eo_user: JCOMProfile,
):
    url = reverse("wjs_send_back_to_typ", kwargs={"pk": stage_proofing_article.articleworkflow.pk})
    client.force_login(eo_user.janeway_account)
    stage_proofing_article.articleworkflow.state = ArticleWorkflow.ReviewStates.READY_FOR_PUBLICATION
    stage_proofing_article.articleworkflow.save()
    form_data = {
        "subject": f"Article {stage_proofing_article.articleworkflow.article.id} back to typesetter",
        "body": "This is a test message body.",
    }
    response = client.post(url, data=form_data)
    stage_proofing_article.articleworkflow.refresh_from_db()
    assert response.status_code == 302
    assert stage_proofing_article.articleworkflow.state == ArticleWorkflow.ReviewStates.TYPESETTER_SELECTED
