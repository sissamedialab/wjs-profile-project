import random

import pytest
from core import models as core_models
from django.conf import settings
from django.core import mail
from django.test import Client
from django.test.client import RequestFactory
from django.urls import reverse
from django.utils.timezone import now
from submission import models as submission_models
from submission.models import Keyword, Section
from utils import setting_handler

from wjs.jcom_profile.models import (
    EditorAssignmentParameters,
    EditorKeyword,
    JCOMProfile,
)
from wjs.jcom_profile.tests.conftest import (
    ASSIGNMENT_PARAMETERS_SPAN,
    _create_published_articles,
    _journal_factory,
)
from wjs.jcom_profile.utils import generate_token


@pytest.mark.django_db
def test_filter_articles_by_author(editor, published_articles, press, admin, sections, keywords, journal):
    journal_2 = _journal_factory("JCOMAL", press, domain="jcomal.sissa.it")
    _create_published_articles(admin, editor, journal_2, sections, keywords, items=4)

    client = Client()
    active_authors = core_models.Account.objects.filter(pk__in=published_articles.values_list("authors", flat=True))
    author = random.choice(active_authors)
    url = reverse("articles_by_author", kwargs={"author": author.pk})
    response = client.get(url)

    articles_per_author = published_articles.filter(frozenauthor__author__in=[author], journal=journal)

    assert response.status_code == 200
    assert response.context["filter_by"]["title"] == "Filter by author"
    assert response.context["filter_by"]["paragraph"] == "All author's publications are listed below."
    assert response.context["filter_by"]["filtering_object"] == author.full_name()

    assert set(response.context["page_obj"].object_list) == set(articles_per_author)
    for article in response.context["articles"]:
        assert author.pk in list(article.frozenauthor_set.values_list("author_id", flat=True))
        assert article.journal == journal


@pytest.mark.django_db
def test_filter_articles_by_section(editor, published_articles, press, admin, sections, keywords, journal):
    journal_2 = _journal_factory("JCOMAL", press, domain="jcomal.sissa.it")
    _create_published_articles(admin, editor, journal_2, sections, keywords, items=4)

    client = Client()
    active_sections = Section.objects.filter(pk__in=published_articles.values_list("section", flat=True))
    section = random.choice(active_sections)
    url = reverse("articles_by_section", kwargs={"section": section.pk})
    response = client.get(url)

    assert response.status_code == 200
    assert response.context["filter_by"]["title"] == "Filter by section"
    assert response.context["filter_by"]["paragraph"] == "Publications included in this section."
    assert response.context["filter_by"]["filtering_object"] == section.name

    assert response.context["page_obj"].object_list
    for article in response.context["articles"]:
        assert article.section.pk == section.pk
        assert article.journal == journal


@pytest.mark.django_db
@pytest.mark.parametrize("filter_field", ("section", "keyword", "year"))
def test_search(editor, published_articles, press, admin, sections, keywords, journal, filter_field):
    articles = _create_published_articles(admin, editor, journal, sections, keywords, items=20)

    client = Client()
    kwargs = {"article_search": "Searchme", "sort": "relevance"}
    if filter_field == "section":
        active_sections = Section.objects.filter(pk__in=articles.values_list("section", flat=True))
        section = random.choice(active_sections)
        kwargs["sections"] = section.pk
        filtered = articles.filter(section=section)
        assert filtered.exists()
        matches_count = filtered.count()
        for f in filtered:
            f.title = "Searchme"
            f.index_full_text()
            f.save()
    elif filter_field == "keyword":
        active_keywords = Keyword.objects.filter(pk__in=articles.values_list("keywords", flat=True))
        keyword = random.choice(active_keywords)
        kwargs["keywords"] = keyword.pk
        filtered = articles.filter(keywords=keyword)
        assert filtered.exists()
        matches_count = filtered.count()
        for f in filtered:
            f.title = "Searchme"
            f.index_full_text()
            f.save()
    elif filter_field == "year":
        year = now().year
        kwargs["year"] = year
        filtered = articles.filter(date_published__year=year)
        assert filtered.exists()
        matches_count = filtered.count()
        for f in filtered:
            f.title = "Searchme"
            f.index_full_text()
            f.save()
    url = reverse("search")
    response = client.get(url, kwargs)

    assert response.status_code == 200
    assert response.context["paginator"].count == matches_count
    if matches_count > 10:
        assert response.context["paginator"].num_pages == 2
    else:
        assert response.context["articles"].count() == matches_count

    for article in response.context["articles"]:
        assert article.journal == journal
        if filter_field == "section":
            assert article.section == section
        elif filter_field == "keyword":
            assert article.keywords.filter(pk=keyword.pk).exists()
        else:
            assert article.date_published.year == year


@pytest.mark.django_db
def test_filter_articles_by_keyword(editor, published_articles, press, admin, sections, keywords, journal):
    journal_2 = _journal_factory("JCOMAL", press, domain="jcomal.sissa.it")
    _create_published_articles(admin, editor, journal_2, sections, keywords, items=4)

    client = Client()
    active_keywords = Keyword.objects.filter(pk__in=published_articles.values_list("keywords", flat=True))
    keyword = random.choice(active_keywords)
    url = reverse("articles_by_keyword", kwargs={"keyword": keyword.pk})
    response = client.get(url)

    assert response.status_code == 200
    assert response.context["filter_by"]["title"] == "Filter by keyword"
    assert response.context["filter_by"]["paragraph"] == "Publications including this keyword are listed below."
    assert response.context["filter_by"]["filtering_object"] == keyword.word

    assert response.context["page_obj"].object_list
    for article in response.context["articles"]:
        assert keyword.pk in article.keywords.values_list("pk", flat=True)
        assert article.journal == journal


@pytest.mark.django_db
def test_gdpr_acceptance(admin, invited_user, journal):
    client = Client()
    token = generate_token(invited_user.email, journal.code)
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
    token = generate_token(non_existing_email, journal.code)
    url = reverse("accept_gdpr", kwargs={"token": token})

    response = client.get(url)
    assert response.status_code == 404
    assert response.context.get("error")


@pytest.mark.parametrize("user_as_main_author", (True, False))
@pytest.mark.django_db
def test_submitting_user_is_main_author_when_setting_is_on(
    user_as_main_author_setting,
    admin,
    journal,
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
        "competing_interests": "",
        "comments_editor": "",
        "start_submission": "",
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


@pytest.mark.parametrize("user_role", ("staff", "editor", "section-editor", "other"))
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
    elif user_role != "other":
        user.add_account_role(user_role, journal)
    jcom_user.save()

    jcom_user.refresh_from_db()
    client = Client()
    client.force_login(jcom_user)
    url = f"/{journal.code}/profile/"
    response = client.get(url)
    assert response.status_code == 200
    if user_role in ["staff", "section-editor"]:
        assert ASSIGNMENT_PARAMETERS_SPAN in response.content.decode()


@pytest.mark.django_db
def test_assignment_parameters_button_is_not_present_without_journal(
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
@pytest.mark.parametrize("user_role", ("other", "editor", "section-editor"))
def test_editor_can_change_his_parameters(journal, roles, user_role, user):
    client = Client()
    current_user = JCOMProfile.objects.get(janeway_account=user)
    current_user.gdpr_checkbox = True
    current_user.is_active = True
    # User are staff or editor

    if user_role != "other":
        user.add_account_role(user_role, journal)
    current_user.save()

    client.force_login(current_user)

    url = f"/{journal.code}/update/parameters/"
    response = client.get(url)

    if user_role == "section-editor":
        assert response.status_code == 200
    else:
        assert response.status_code == 403


@pytest.mark.django_db
def test_update_editor_assignment_parameters(section_editor, roles, keywords, journal):
    keywords_id = Keyword.objects.all().values_list("id", flat=True)
    workload = 10

    client = Client()
    client.force_login(section_editor)
    url = f"/{journal.code}/update/parameters/"
    data = {"keywords": list(keywords_id), "workload": workload}
    response = client.post(url, data)
    assert response.status_code == 302

    assignment_parameters = EditorAssignmentParameters.objects.get(editor=section_editor, journal=journal)
    editor_keywords = assignment_parameters.editorkeyword_set.all()
    assert assignment_parameters.workload == workload
    for keyword in keywords:
        assert keyword.word in list(editor_keywords.values_list("keyword__word", flat=True))


@pytest.mark.django_db
@pytest.mark.skip(reason="We have to further investigate how to deal with director role.")
def test_assignment_parameter_button_is_present_in_editors_interface_if_the_user_has_director_role(
    director,
    editor,
    journal,
):
    # TODO: To be implemented the backend part, see https://gitlab.sissamedialab.it/wjs/specs/-/issues/84
    #  See also core.views.role and core.views.roles
    director.add_account_role("director", journal)
    director.save()
    client = Client()
    client.force_login(director)
    url = f"/{journal.code}/manager/roles/editor/"
    response = client.get(url)
    assert response.status_code == 200
    # TODO: This check must be better handled; moreover, I should check this behaviour when more editors exist.
    assert (
        f"""<a class="tiny primary button"
                                       href="/{journal.code}/update/parameters/{editor.janeway_account.pk}/">&nbsp;Assignment
                                        Parameters</a>"""  # noqa
        in response.content.decode()
    )


@pytest.mark.django_db
@pytest.mark.parametrize("user_role", ("staff", "editor"))
def test_director_can_change_editor_keywords(journal, roles, user_role, user):
    client = Client()
    jcom_user = JCOMProfile.objects.get(janeway_account=user)
    jcom_user.gdpr_checkbox = True
    jcom_user.is_active = True
    # User are staff or editor
    if user_role == "staff":
        jcom_user.is_staff = True

    elif user_role == "editor":
        user.add_account_role("editor", journal)
    jcom_user.save()

    client.force_login(jcom_user)

    url = f"/{journal.code}/update/parameters/{jcom_user.janeway_account.pk}/"
    response = client.get(url)

    if user_role == "staff":
        assert response.status_code == 200
    else:
        assert response.status_code == 403


@pytest.mark.django_db
def test_director_can_change_editor_parameters(journal, roles, admin, editor, keywords):
    editor_parameters = EditorAssignmentParameters.objects.create(editor=editor, journal=journal)
    for keyword in keywords:
        EditorKeyword.objects.create(keyword=keyword, editor_parameters=editor_parameters)
    brake_on = 10
    weight = 7
    client = Client()
    client.force_login(admin)
    url = f"/{journal.code}/update/parameters/{editor.janeway_account.pk}/"

    response = client.get(url)
    assert response.status_code == 200

    data = {"workload": editor_parameters.workload, "brake_on": brake_on, "csrf_token": response.context["csrf_token"]}

    formset = response.context["formset"]
    for field in "TOTAL_FORMS", "INITIAL_FORMS", "MIN_NUM_FORMS", "MAX_NUM_FORMS":
        data[f"{formset.management_form.prefix}-{field}"] = formset.management_form[field].value()

    for form_id in range(formset.total_form_count()):
        current_form = formset.forms[form_id]

        # retrieve all the fields
        for field_name in current_form.fields:
            value = current_form[field_name].value()
            data[f"{current_form.prefix}-{field_name}"] = value if value else ""
            if field_name == "weight":
                data[f"{current_form.prefix}-{field_name}"] = weight

    response_post = client.post(url, data)
    assert response_post.status_code == 302

    editor_parameters.refresh_from_db()

    assert editor_parameters.brake_on == brake_on
    for keyword in EditorKeyword.objects.filter(editor_parameters=editor_parameters):
        assert keyword.weight == weight
