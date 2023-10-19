"""Tests related to the communication system."""
from io import StringIO
from typing import Callable, Optional

import pytest
from django.contrib.contenttypes.models import ContentType
from django.test import Client
from django.urls import reverse
from submission import models as submission_models

from wjs.jcom_profile.models import JCOMProfile

from ..communication_utils import get_messages_related_to_me
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
    article: submission_models.Article,
    client: Client,
    cleanup_test_files_from_folder_files,
):
    """Test that when a user writes a message with an attachment, the attachment is saved in the article's folder."""
    # TODO: check me! can a user write to himself? maybe equivalent to a note on the paper?
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
