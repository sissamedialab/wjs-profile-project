"""Tests related to the communication system."""

import datetime
from io import BytesIO, StringIO
from typing import Callable, Optional

import pytest
from core import files as core_files
from core.middleware import GlobalRequestMiddleware
from core.models import Account
from django.contrib.auth import login
from django.contrib.contenttypes.models import ContentType
from django.core.files import File as DjangoFile
from django.core.handlers.base import BaseHandler
from django.http import HttpRequest
from django.test import Client
from django.urls import reverse
from django.utils.timezone import now
from hijack.middleware import HijackUserMiddleware
from review import models as review_models
from submission import models as submission_models
from utils import setting_handler

from wjs.jcom_profile.models import JCOMProfile
from wjs.jcom_profile.permissions import get_hijacker

from ..communication_utils import (
    get_eo_user,
    get_messages_related_to_me,
    should_notify_actor,
)
from ..logic import AssignToReviewer, HandleMessage
from ..models import Message, WjsEditorAssignment
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
def test_director_sees_all_journal_messages(
    article: submission_models.Article,
    create_jcom_user: Callable[[Optional[str]], JCOMProfile],
    director: JCOMProfile,
):
    """Test that a director sees all messages related to the journal they are director of."""
    chakotay = create_jcom_user("Chakotay")
    tuvok = create_jcom_user("Tuvok")
    assert Message.objects.count() == 0
    msg1 = Message.objects.create(
        actor=chakotay,
        subject="",
        body="CIAOOONE",
        content_type=ContentType.objects.get_for_model(article),
        object_id=article.id,
    )
    msg1.recipients.add(tuvok)
    msg2 = Message.objects.create(
        actor=tuvok,
        subject="",
        body="EHILAAAAA",
        content_type=ContentType.objects.get_for_model(article),
        object_id=article.id,
    )
    msg2.recipients.add(chakotay)
    assert msg1.recipients.count() == 1
    assert msg1.recipients.first() != chakotay
    assert msg1.actor != director
    assert msg1.recipients.first() != director

    assert msg2.recipients.count() == 1
    assert msg2.recipients.first() != tuvok
    assert msg2.actor != director
    assert msg2.recipients.first() != director
    messages = get_messages_related_to_me(director, article)
    assert messages.count() == 2


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
    url = reverse("wjs_message_write", kwargs={"article_id": article.id, "recipient_id": user.id})
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
            "message_type": Message.MessageTypes.USER,
            "recipientsFS-TOTAL_FORMS": "1",
            "recipientsFS-INITIAL_FORMS": "0",
            "recipientsFS-0-recipient": [user.id],
        },
    )
    assert response.status_code == 302


@pytest.mark.parametrize("author_can_contact_director", (True, False))
@pytest.mark.django_db
def test_message_addressing(
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

    editor: Account = WjsEditorAssignment.objects.get_current(assigned_article).editor

    director: Account = director.janeway_account

    eo_system_user: Account = get_eo_user(assigned_article)

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
    assert HandleMessage.can_write_to(editor, assigned_article, eo_system_user) is True

    # Reviewer
    # ======
    assert HandleMessage.can_write_to(reviewer, assigned_article, editor) is True
    assert HandleMessage.can_write_to(reviewer, assigned_article, reviewer) is True
    assert HandleMessage.can_write_to(reviewer, assigned_article, author) is False
    assert HandleMessage.can_write_to(reviewer, assigned_article, director) is True
    assert HandleMessage.can_write_to(reviewer, assigned_article, eo_system_user) is True

    # Author
    # ======
    assert HandleMessage.can_write_to(author, assigned_article, editor) is True
    assert HandleMessage.can_write_to(author, assigned_article, reviewer) is False
    assert HandleMessage.can_write_to(author, assigned_article, author) is True
    assert HandleMessage.can_write_to(author, assigned_article, director) is author_can_contact_director
    assert HandleMessage.can_write_to(author, assigned_article, eo_system_user) is True


@pytest.mark.parametrize("author_can_contact_director", (True, False))
@pytest.mark.django_db
def test_allowed_recipients_for_actor(
    assigned_article: submission_models.Article,
    create_jcom_user: Callable[[Optional[str]], JCOMProfile],
    director: JCOMProfile,
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
    editor: Account = WjsEditorAssignment.objects.get_current(assigned_article).editor

    director: Account = director.janeway_account

    eo_system_user: Account = get_eo_user(assigned_article)

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
    assert eo_system_user in allowed_recipients

    # Reviewer
    # ======
    allowed_recipients = HandleMessage.allowed_recipients_for_actor(actor=reviewer_1, article=assigned_article)
    assert author not in allowed_recipients
    assert reviewer_1 in allowed_recipients
    assert reviewer_2 not in allowed_recipients
    assert editor in allowed_recipients
    assert director in allowed_recipients
    assert eo_system_user in allowed_recipients

    # Author
    # ======
    allowed_recipients = HandleMessage.allowed_recipients_for_actor(actor=author, article=assigned_article)
    assert author in allowed_recipients
    assert reviewer_1 not in allowed_recipients
    assert reviewer_2 not in allowed_recipients
    assert editor in allowed_recipients
    assert (director in allowed_recipients) is author_can_contact_director
    assert eo_system_user in allowed_recipients


@pytest.mark.django_db
def test_recipient_can_toggle_read(
    article: submission_models.Article,
    create_jcom_user: Callable[[Optional[str]], JCOMProfile],
    eo_user: JCOMProfile,
    client,
):
    """Test that the read flag can be toggled only by the recipient or staff or EO."""
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
    assert msg.messagerecipients_set.count() == 1
    mr = msg.messagerecipients_set.first()
    assert mr.recipient_id == tuvok.id

    url = reverse("wjs_message_toggle_read", kwargs={"message_id": msg.id, "recipient_id": tuvok.id})
    client.force_login(chakotay)
    response = client.post(url, data={"read": True})
    assert response.status_code == 403

    client.force_login(tuvok)
    response = client.post(url, data={"read": True})
    assert response.status_code == 200
    mr.refresh_from_db()
    assert mr.read is True
    response = client.post(url, data={"read": False})
    assert response.status_code == 200
    mr.refresh_from_db()
    assert mr.read is False

    client.force_login(eo_user)
    response = client.post(url, data={"read": True})
    assert response.status_code == 403
    mr.refresh_from_db()
    assert mr.read is False


@pytest.mark.django_db
def test_message_attachment_access(
    assigned_article: submission_models.Article,
    create_jcom_user: Callable[[Optional[str]], JCOMProfile],
    fake_request: HttpRequest,
    eo_user: JCOMProfile,
    review_form: review_models.ReviewForm,
    client,
    review_settings,
):
    """Test that only actor, recipient and EO can download an attachment."""
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
    editor: Account = WjsEditorAssignment.objects.get_current(assigned_article).editor

    eo_user: Account = eo_user.janeway_account

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

    # Create a message for the given article.
    # (see also wjs-utils-project scenario_review)
    # The actor is the reviewer and the recipient is the editor
    actor = reviewer_1
    recipient = editor
    message = Message.objects.create(
        actor=actor,
        subject="A random subject",
        body="A random body",
        content_type=ContentType.objects.get_for_model(assigned_article),
        object_id=assigned_article.id,
    )
    message.recipients.add(recipient)

    attachment_dj = DjangoFile(BytesIO(b"ciao"), "Msg attachment.txt")
    attachment_file = core_files.save_file_to_article(
        attachment_dj,
        assigned_article,
        actor,
    )
    attachment_file.label = "Attachment LABEL"
    attachment_file.description = "Long and useless attachment file description"
    attachment_file.save()
    message.attachments.add(attachment_file)

    url = reverse(
        "wjs_message_download_attachment",
        kwargs={"message_id": message.id, "attachment_id": attachment_file.id},
    )
    # Actor
    client.force_login(reviewer_1)
    response = client.get(url)
    assert response.status_code == 200

    # Recipient
    client.force_login(editor)
    response = client.get(url)
    assert response.status_code == 200

    # EO
    client.force_login(eo_user)
    response = client.get(url)
    assert response.status_code == 200

    # Another reviewer from the same paper, but he's not the recipient
    client.force_login(reviewer_2)
    response = client.get(url)
    assert response.status_code == 403


@pytest.mark.parametrize(
    "hijacked, notify_flag, is_notified",
    ((True, False, False), (True, True, True), (False, False, False), (False, True, False)),
)
@pytest.mark.django_db
def test_hijack_notifications(
    eo_user: Account,
    normal_user: Account,
    fake_request: HttpRequest,
    hijacked: bool,
    notify_flag: bool,
    is_notified: bool,
):
    """
    hijack notifications are sent only if user is hijacked and notify flag is set to True.
    """
    fake_request.user = eo_user
    if hijacked:
        hijack_history = fake_request.session.get("hijack_history", [])
        hijack_history.append(fake_request.user._meta.pk.value_to_string(eo_user))
        login(fake_request, normal_user)

        fake_request.session["silent_hijack"] = not notify_flag
        fake_request.session["hijack_history"] = hijack_history
    GlobalRequestMiddleware.process_request(fake_request)
    HijackUserMiddleware(BaseHandler.get_response).process_request(fake_request)

    hijacker = get_hijacker()
    notify = should_notify_actor()
    if hijacked:
        assert fake_request.user == normal_user
    else:
        assert fake_request.user == eo_user
    send_notification = hijacker and notify
    if notify_flag and hijacked:
        assert send_notification
    else:
        assert not send_notification
