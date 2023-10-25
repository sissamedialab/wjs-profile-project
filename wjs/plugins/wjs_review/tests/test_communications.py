"""Tests related to the communication system."""
import datetime
from io import StringIO
from typing import Callable, Optional

import pytest
from core.models import Account
from django.contrib.contenttypes.models import ContentType
from django.http import HttpRequest
from django.test import Client
from django.urls import reverse
from django.utils.timezone import now
from review import models as review_models
from submission import models as submission_models
from utils import setting_handler

from wjs.jcom_profile.models import JCOMProfile

from ..communication_utils import get_messages_related_to_me
from ..logic import AssignToReviewer, HandleMessage
from ..models import Message
from . import conftest


@pytest.mark.django_db
def test_user_sees_article_generic_messages(
    article: submission_models.Article,
    create_jcom_user: Callable[[Optional[str]], JCOMProfile],
):
    """Test that a user sees a message that has no recipients, even if the user is not the actor."""
    chakotay = create_jcom_user("Chakotay")
    tuvok = create_jcom_user("Tuvok")
    msg = Message.objects.create(
        actor=chakotay,
        subject="",
        body="CIAOOONE",
        content_type=ContentType.objects.get_for_model(article),
        object_id=article.id,
    )
    assert msg.recipients.count() == 0
    messages = get_messages_related_to_me(tuvok, article)
    assert messages.count() == 1
    assert messages.first() == msg


@pytest.mark.django_db
def test_user_sees_authored_messages(
    article: submission_models.Article,
    create_jcom_user: Callable[[Optional[str]], JCOMProfile],
):
    """Test that a user sees messages authored by him (i.e. the user is the actor)."""
    chakotay = create_jcom_user("Chakotay")
    tuvok = create_jcom_user("Tuvok")
    msg = Message.objects.create(
        actor=chakotay,
        subject="",
        body="CIAOOONE",
        content_type=ContentType.objects.get_for_model(article),
        object_id=article.id,
    )
    msg.recipients.add(tuvok)
    assert msg.recipients.count() == 1
    assert msg.recipients.first() != chakotay
    messages = get_messages_related_to_me(chakotay, article)
    assert messages.count() == 1
    assert messages.first() == msg


@pytest.mark.django_db
def test_user_sees_recipientee_messages(
    article: submission_models.Article,
    create_jcom_user: Callable[[Optional[str]], JCOMProfile],
):
    """Test that a user sees messages destined to him (i.e. the user is one of the recipients)."""
    chakotay = create_jcom_user("Chakotay")
    tuvok = create_jcom_user("Tuvok")
    msg = Message.objects.create(
        actor=chakotay,
        subject="",
        body="CIAOOONE",
        content_type=ContentType.objects.get_for_model(article),
        object_id=article.id,
    )
    msg.recipients.add(tuvok)
    assert msg.recipients.count() == 1
    assert msg.recipients.first() != chakotay
    messages = get_messages_related_to_me(tuvok, article)
    assert messages.count() == 1
    assert messages.first() == msg


@pytest.mark.django_db
def test_post_message_form_with_attachment_creates_file(
    review_settings,
    article: submission_models.Article,
    client: Client,
    cleanup_test_files_from_folder_files,
):
    """Test that when a user writes a message with an attachment, the attachment is saved in the article's folder."""
    user = article.owner
    client.force_login(user)  # logged-in user will be the "actor"
    url = reverse("wjs_article_messages", kwargs={"article_id": article.id, "recipient_id": user.id})
    # Django doc: https://docs.djangoproject.com/en/dev/topics/testing/tools/#django.test.Client.post
    attachment = StringIO("Sono un file!")
    attachment.name = f"fake-file{conftest.TEST_FILES_EXTENSION}"
    # TODO: switch to  in-memory storage
    # Needs pip install dj-inmemorystorage
    # e.g.: with override_settings(DEFAULT_FILE_STORAGE="inmemorystorage.InMemoryStorage"):
    response = client.post(
        url,
        data={
            "subject": "subject",
            "body": "body",
            "attachment": attachment,
            "actor": user.id,
            "content_type": ContentType.objects.get_for_model(article).id,
            "object_id": article.id,
            "recipient": user.id,
            "message_type": Message.MessageTypes.STD,
        },
    )
    assert response.status_code == 302


@pytest.mark.parametrize("author_can_contact_director", (True, False))
@pytest.mark.django_db
def test_message_addressing(
    review_settings,
    assigned_article: submission_models.Article,
    create_jcom_user: Callable[[Optional[str]], JCOMProfile],
    reviewer: JCOMProfile,
    director: JCOMProfile,
    admin: JCOMProfile,
    fake_request: HttpRequest,
    review_form: review_models.ReviewForm,
    author_can_contact_director: bool,
):
    """Verify which sender can write to which recipient."""
    # TODO: the author of the "assigned_article" is an admin user
    # Let's set it to a normal user (no staff and no admin)
    author: Account = create_jcom_user("simple_author").janeway_account
    assigned_article.correspondence_author = author
    assigned_article.save()
    assigned_article.authors.clear()
    assigned_article.authors.add(author)

    # Let's make all actors point directly to the Janeway's account (i.e. not to the JCOMProfile), because it's easier
    # to use.
    reviewer: Account = reviewer.janeway_account

    editor: Account = assigned_article.editorassignment_set.first().editor

    director: Account = director.janeway_account

    # TODO: EO role is not yet well defined. For now, it is anyone with is_staff=True
    admin: Account = admin.janeway_account

    # The fixture `review_settings` ensures that all needed (journal) settings exist, but we still need to set the
    # desired value
    setting_handler.save_setting(
        setting_group_name="wjs_review",
        setting_name="author_can_contact_director",
        journal=assigned_article.journal,
        value=author_can_contact_director,
    )

    # Need to have a reviewer already assigned, so we can test a richer scenario
    fake_request.user = editor  # NB: quick_assign expects request.user to be the editor... sigh...
    service = AssignToReviewer(
        workflow=assigned_article.articleworkflow,
        # we must pass the Account object linked to the JCOMProfile instance, to ensure it
        # can be used in janeway core
        reviewer=reviewer,
        editor=editor,
        form_data={
            "acceptance_due_date": now().date() + datetime.timedelta(days=7),
            "message": "random message",
        },
        request=fake_request,
    )
    service.run()

    # Let's ensure that our main actors are not "special" in some way
    assert editor.is_staff is False
    assert reviewer.is_staff is False
    assert author.is_staff is False

    # Editor
    # ======
    assert HandleMessage.can_write_to(editor, assigned_article, editor) is True
    assert HandleMessage.can_write_to(editor, assigned_article, reviewer) is True
    assert HandleMessage.can_write_to(editor, assigned_article, author) is True
    assert HandleMessage.can_write_to(editor, assigned_article, director) is True
    assert HandleMessage.can_write_to(editor, assigned_article, admin) is True

    # Reviewer
    # ======
    assert HandleMessage.can_write_to(reviewer, assigned_article, editor) is True
    assert HandleMessage.can_write_to(reviewer, assigned_article, reviewer) is True
    assert HandleMessage.can_write_to(reviewer, assigned_article, author) is False
    assert HandleMessage.can_write_to(reviewer, assigned_article, director) is True
    assert HandleMessage.can_write_to(reviewer, assigned_article, admin) is True

    # Author
    # ======
    assert HandleMessage.can_write_to(author, assigned_article, editor) is True
    assert HandleMessage.can_write_to(author, assigned_article, reviewer) is False
    assert HandleMessage.can_write_to(author, assigned_article, author) is True
    assert HandleMessage.can_write_to(author, assigned_article, director) is author_can_contact_director
    assert HandleMessage.can_write_to(author, assigned_article, admin) is True


@pytest.mark.parametrize("author_can_contact_director", (True, False))
@pytest.mark.django_db
def test_allowed_recipients_for_actor(
    review_settings,
    assigned_article: submission_models.Article,
    create_jcom_user: Callable[[Optional[str]], JCOMProfile],
    director: JCOMProfile,
    admin: JCOMProfile,
    fake_request: HttpRequest,
    review_form: review_models.ReviewForm,
    author_can_contact_director: bool,
):
    """Test the generation of the list of allowed message recipients for each actor."""
    # TODO: the author of the "assigned_article" is an admin user
    # Let's set it to a normal user (no staff and no admin)
    author: Account = create_jcom_user("simple_author").janeway_account
    assigned_article.correspondence_author = author
    assigned_article.save()
    assigned_article.authors.clear()
    assigned_article.authors.add(author)

    reviewer_1: Account = create_jcom_user("reviewer_1").janeway_account
    reviewer_2: Account = create_jcom_user("reviewer_2").janeway_account

    # Let's make all actors point directly to the Janeway's account (i.e. not to the JCOMProfile), because it's easier
    # to use.
    editor: Account = assigned_article.editorassignment_set.first().editor

    director: Account = director.janeway_account

    # TODO: EO role is not yet well defined. For now, it is anyone with is_staff=True
    admin: Account = admin.janeway_account

    # The fixture `review_settings` ensures that all needed (journal) settings exist, but we still need to set the
    # desired value
    setting_handler.save_setting(
        setting_group_name="wjs_review",
        setting_name="author_can_contact_director",
        journal=assigned_article.journal,
        value=author_can_contact_director,
    )

    # Need to have a couple of reviewers already assigned, so we can test a richer scenario
    fake_request.user = editor  # NB: quick_assign expects request.user to be the editor... sigh...
    for reviewer in (reviewer_1, reviewer_2):
        service = AssignToReviewer(
            workflow=assigned_article.articleworkflow,
            # we must pass the Account object linked to the JCOMProfile instance, to ensure it
            # can be used in janeway core
            reviewer=reviewer,
            editor=editor,
            form_data={
                "acceptance_due_date": now().date() + datetime.timedelta(days=7),
                "message": "random message",
            },
            request=fake_request,
        )
        service.run()

    # Let's ensure that our main actors are not "special" in some way
    assert editor.is_staff is False
    assert reviewer_1.is_staff is False
    assert reviewer_2.is_staff is False
    assert author.is_staff is False

    # Editor
    # ======
    allowed_recipients = HandleMessage.allowed_recipients_for_actor(actor=editor, article=assigned_article)
    assert author in allowed_recipients
    assert reviewer_1 in allowed_recipients
    assert reviewer_2 in allowed_recipients
    assert editor in allowed_recipients
    assert director in allowed_recipients
    assert admin in allowed_recipients

    # Reviewer
    # ======
    allowed_recipients = HandleMessage.allowed_recipients_for_actor(actor=reviewer_1, article=assigned_article)
    assert author not in allowed_recipients
    assert reviewer_1 in allowed_recipients
    assert reviewer_2 not in allowed_recipients
    assert editor in allowed_recipients
    assert director in allowed_recipients
    assert admin in allowed_recipients

    # Author
    # ======
    allowed_recipients = HandleMessage.allowed_recipients_for_actor(actor=author, article=assigned_article)
    assert author in allowed_recipients
    assert reviewer_1 not in allowed_recipients
    assert reviewer_2 not in allowed_recipients
    assert editor in allowed_recipients
    assert (director in allowed_recipients) is author_can_contact_director
    assert admin in allowed_recipients
