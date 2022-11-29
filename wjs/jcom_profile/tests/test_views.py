import pytest
from core import models as core_models
from django.conf import settings
from django.core import mail
from django.test import Client
from django.test.client import RequestFactory
from django.urls import reverse
from submission import models as submission_models
from submission.models import Keyword
from utils import setting_handler

from wjs.jcom_profile.models import EditorAssignmentParameters, JCOMProfile
from wjs.jcom_profile.tests.conftest import ASSIGNMENT_PARAMETERS_SPAN, INVITE_BUTTON
from wjs.jcom_profile.utils import generate_token


@pytest.mark.django_db
def test_invite_button_is_in_account_admin_interface(admin, journal):
    client = Client()
    client.force_login(admin)
    url = reverse("admin:core_account_changelist")
    response = client.get(url)
    assert response.status_code == 200

    assert INVITE_BUTTON in response.content.decode()


@pytest.mark.django_db
def test_invite_function_creates_inactive_user(admin, journal):
    client = Client()
    client.force_login(admin)
    url = reverse("admin:invite")
    data = {
        "first_name": "Name",
        "last_name": "Surname",
        "email": "email@email.it",
        "institution": "Institution",
        "department": "Department",
        "message": "Message",
    }
    response = client.post(url, data=data)
    assert response.status_code == 302

    invited_user = JCOMProfile.objects.get(email=data["email"])
    request = RequestFactory().get(url)
    invitation_token = generate_token(data["email"])
    gdpr_acceptance_url = request.build_absolute_uri(reverse("accept_gdpr", kwargs={"token": invitation_token}))

    assert invited_user
    assert not invited_user.is_active
    assert not invited_user.gdpr_checkbox
    for field, _ in data.items():
        if field != "message":
            assert getattr(invited_user, field) == data[field]
    assert invited_user.invitation_token == invitation_token

    assert len(mail.outbox) == 1
    invitation_mail = mail.outbox[0]

    assert invitation_mail.from_email == settings.DEFAULT_FROM_EMAIL
    assert invitation_mail.to == [invited_user.email]
    assert invitation_mail.subject == settings.JOIN_JOURNAL_SUBJECT
    assert invitation_mail.body == settings.JOIN_JOURNAL_BODY.format(
        invited_user.first_name,
        invited_user.last_name,
        data["message"],
        gdpr_acceptance_url,
    )


@pytest.mark.django_db
def test_invite_existing_email_user(admin, user, journal):
    existing_users_count = JCOMProfile.objects.all().count()
    client = Client()
    client.force_login(admin)
    url = reverse("admin:invite")
    data = {
        "first_name": user.first_name,
        "last_name": user.last_name,
        "email": user.email,
        "institution": user.institution,
        "department": user.department,
        "message": "Message",
    }
    response = client.post(url, data=data)
    assert response.status_code == 200

    assert existing_users_count == JCOMProfile.objects.all().count()
    assert len(mail.outbox) == 0


@pytest.mark.django_db
def test_gdpr_acceptance(admin, invited_user, journal):
    client = Client()
    token = generate_token(invited_user.email)
    url = reverse("accept_gdpr", kwargs={"token": token})

    request = RequestFactory().get(url)
    response = client.post(url, data={"gdpr_checkbox": True})
    invited_user.refresh_from_db()

    reset_token = core_models.PasswordResetToken.objects.get(account=invited_user)

    assert response.status_code == 200
    assert invited_user.gdpr_checkbox
    assert invited_user.is_active
    assert not invited_user.invitation_token
    assert response.context.get("activated")
    assert len(mail.outbox) == 1

    invitation_mail = mail.outbox[0]
    reset_psw_url = request.build_absolute_uri(reverse("core_reset_password", kwargs={"token": reset_token.token}))

    assert invitation_mail.from_email == settings.DEFAULT_FROM_EMAIL
    assert invitation_mail.to == [invited_user.email]
    assert invitation_mail.subject == settings.RESET_PASSWORD_SUBJECT
    assert invitation_mail.body == settings.RESET_PASSWORD_BODY.format(
        invited_user.first_name,
        invited_user.last_name,
        reset_psw_url,
    )


@pytest.mark.django_db
def test_gdpr_acceptance_for_non_existing_user(admin, journal):
    client = Client()
    non_existing_email = "doesnotexist@email.it"
    token = generate_token(non_existing_email)
    url = reverse("accept_gdpr", kwargs={"token": token})

    response = client.get(url)
    assert response.status_code == 404
    assert response.context.get("error")


@pytest.mark.django_db
def test_email_are_sent_to_author_and_coauthors_after_article_submission_(admin, article, coauthors_setting):
    client = Client()
    client.force_login(admin)
    url = reverse("submit_review", args=(article.pk,))
    coauthors_email = list(
        article.authors.exclude(email=article.correspondence_author.email).values_list("email", flat=True),
    )

    response = client.post(url, data={"next_step": "next_step"})
    assert response.status_code == 302
    assert len(mail.outbox) == article.authors.count()

    for m in mail.outbox:
        if m.subject == f"[{article.journal.code}] Coauthor - Article Submission":
            assert m.to == coauthors_email
        else:
            assert m.to == [article.correspondence_author.email]


@pytest.mark.parametrize("user_as_main_author", (True, False))
@pytest.mark.django_db
def test_submitting_user_is_main_author_when_setting_is_on(
    user_as_main_author_setting,
    admin,
    article_journal,
    roles,
    user_as_main_author,
):
    setting_handler.save_setting("general", "user_automatically_author", None, "on")
    setting_handler.save_setting(
        "general",
        "user_automatically_main_author",
        None,
        "on" if user_as_main_author else "",
    )

    client = Client()
    client.force_login(admin)

    data = {
        "publication_fees": "on",
        "submission_requirements": "on",
        "copyright_notice": "on",
        "competing_interests": None,
        "comments_editor": None,
        "start_submission": None,
    }
    url = reverse("submission_start")
    response = client.post(url, data=data)
    assert response.status_code == 302
    assert submission_models.Article.objects.count() == 1

    article = submission_models.Article.objects.first()
    if user_as_main_author:
        assert article.correspondence_author == admin.janeway_account
    else:
        assert not article.correspondence_author


@pytest.mark.parametrize("user_role", ("staff", "editor", "other"))
@pytest.mark.django_db
def test_assignment_parameters_button_is_in_edit_profile_interface_if_user_is_staff_or_editor(
    user,
    roles,
    user_role,
    journal,
):
    jcom_user = JCOMProfile.objects.get(janeway_account=user)
    jcom_user.gdpr_checkbox = True
    jcom_user.is_active = True
    # User are staff or editor
    if user_role == "staff":
        jcom_user.is_staff = True

    elif user_role == "editor":
        user.add_account_role("editor", journal)
    jcom_user.save()

    jcom_user.refresh_from_db()
    client = Client()
    client.force_login(jcom_user)
    url = f"/{journal.code}/profile/"
    response = client.get(url)
    assert response.status_code == 200
    if user_role in ["staff", "editor"]:
        assert ASSIGNMENT_PARAMETERS_SPAN in response.content.decode()


@pytest.mark.django_db
def test_assignment_parameters_button_is_not_without_journal(
    admin,
    journal,
):
    client = Client()
    client.force_login(admin)
    url = "/profile/"
    response = client.get(url)
    assert response.status_code == 200
    assert ASSIGNMENT_PARAMETERS_SPAN not in response.content.decode()


@pytest.mark.django_db
def test_update_editor_assignment_parameters(user, roles, keywords, journal):
    user.add_account_role("editor", journal)
    jcom_user = JCOMProfile.objects.get(janeway_account=user)
    jcom_user.gdpr_checkbox = True
    jcom_user.is_active = True
    jcom_user.save()

    keywords_id = Keyword.objects.all().values_list("id", flat=True)
    workload = 10

    client = Client()
    client.force_login(jcom_user)
    url = reverse("assignment_parameters")
    data = {"keywords": list(keywords_id), "workload": workload}
    response = client.post(url, data)
    assert response.status_code == 302

    assignment_parameters = EditorAssignmentParameters.objects.get(editor=jcom_user, journal=journal)
    editor_keywords = assignment_parameters.editorkeyword_set.all()
    assert assignment_parameters.workload == workload
    for keyword in keywords:
        assert keyword.word in list(editor_keywords.values_list("keyword__word", flat=True))
