"""Deactivated keywords are hidden from keyword lists and selection pages (specs#3279)."""

import pytest
from django.test import Client
from django.utils import timezone
from journal.models import Journal
from submission.models import Keyword, KeywordGroup

from wjs.jcom_profile.forms import UpdateAssignmentParametersForm
from wjs.jcom_profile.models import Recipient, StaffKeyword, StaffWorkloadParameters
from wjs.jcom_profile.newsletter.forms import NewsletterTopicForm
from wjs.jcom_profile.profile.forms import WjsInterestsForm
from wjs.jcom_profile.utils import get_active_keywords


def _deactivate(*words: str) -> None:
    Keyword.objects.filter(word__in=words).update(deactivated=timezone.now())


@pytest.fixture
def hierarchical_journal(journal: Journal, keywords) -> Journal:
    """Turn ``journal`` hierarchical: "Group A" > "Sub A1" (keywords 0-1), "Sub A2" (keywords 2-3); 4-9 ungrouped."""
    journal.submissionconfiguration.hierarchical_keywords = True
    journal.submissionconfiguration.save()
    group = KeywordGroup.objects.create(name="Group A", order=1)
    sub_a1 = KeywordGroup.objects.create(name="Sub A1", parent_group=group, order=1)
    sub_a2 = KeywordGroup.objects.create(name="Sub A2", parent_group=group, order=2)
    Keyword.objects.filter(word__in=["0-keyword", "1-keyword"]).update(group=sub_a1)
    Keyword.objects.filter(word__in=["2-keyword", "3-keyword"]).update(group=sub_a2)
    return journal


@pytest.mark.django_db
def test_get_active_keywords_excludes_deactivated(journal, keywords):
    _deactivate("0-keyword")
    words = set(get_active_keywords(journal).values_list("word", flat=True))
    assert "0-keyword" not in words, "deactivated keyword must be excluded"
    assert words == {f"{i}-keyword" for i in range(1, 10)}, "all active keywords must be returned"

    Keyword.objects.filter(word="0-keyword").update(deactivated=None)
    assert get_active_keywords(journal).filter(word="0-keyword").exists(), "reactivated keyword must be back"


@pytest.mark.django_db
def test_keywords_list_page_hides_deactivated_flat(journal, keywords):
    _deactivate("0-keyword")
    response = Client().get(f"/{journal.code}/keywords/")
    assert response.status_code == 200
    words = [kw.word for kw in response.context["keywords"]]
    assert "0-keyword" not in words, "deactivated keyword must not be listed"
    assert "1-keyword" in words, "active keyword must be listed"


@pytest.mark.django_db
def test_keywords_list_page_hides_deactivated_hierarchical(hierarchical_journal, keywords):
    _deactivate("2-keyword", "3-keyword", "4-keyword")
    response = Client().get(f"/{hierarchical_journal.code}/keywords/")
    assert response.status_code == 200
    subgroups = {
        subgroup.name: sorted(kw.word for kw in subgroup.keywords.all())
        for group in response.context["keywords_list"]
        for subgroup in group.keywordgroup_set.all()
    }
    assert subgroups == {"Sub A1": ["0-keyword", "1-keyword"]}, "subgroup with only deactivated keywords is hidden"
    ungrouped = [kw.word for kw in response.context["ungrouped_keywords"]]
    assert "4-keyword" not in ungrouped, "deactivated ungrouped keyword must not be listed"
    assert "5-keyword" in ungrouped, "active ungrouped keyword must be listed"


ACTIVE_WORDS = {f"{i}-keyword" for i in range(1, 10)}


def _topic_words(recipient: Recipient) -> set[str]:
    return set(recipient.topics.values_list("word", flat=True))


@pytest.mark.django_db
def test_newsletter_form_offers_only_active_keywords(journal, keywords, jcom_user):
    _deactivate("0-keyword")
    recipient = Recipient.objects.create(user=jcom_user, journal=journal)
    form = NewsletterTopicForm(instance=recipient, initial={})
    words = set(form.fields["keywords"].queryset.values_list("word", flat=True))
    assert words == ACTIVE_WORDS, "only active keywords must be offered as topics"


@pytest.mark.django_db
def test_newsletter_form_rejects_deactivated_keyword(journal, keywords, jcom_user):
    _deactivate("0-keyword")
    recipient = Recipient.objects.create(user=jcom_user, journal=journal)
    deactivated = Keyword.objects.get(word="0-keyword")
    form = NewsletterTopicForm(
        data={"keywords": [deactivated.pk], "news": True, "language": "en"}, instance=recipient, initial={}
    )
    assert not form.is_valid(), "a deactivated keyword must not be accepted as topic"
    assert "keywords" in form.errors, "the error must be on the keywords field"


@pytest.mark.django_db
def test_new_logged_in_recipient_gets_only_active_topics(journal, keywords, jcom_user):
    _deactivate("0-keyword")
    client = Client()
    client.force_login(jcom_user)
    response = client.get(f"/{journal.code}/update/newsletters/")
    assert response.status_code == 200
    recipient = Recipient.objects.get(user=jcom_user, journal=journal)
    assert _topic_words(recipient) == ACTIVE_WORDS, "default topics must be the active keywords"


@pytest.mark.django_db
def test_token_recipient_without_topics_gets_only_active_topics(journal, keywords):
    _deactivate("0-keyword")
    recipient = Recipient.objects.create(email="anon@example.com", journal=journal, newsletter_token="tok", news=False)
    response = Client().get(f"/{journal.code}/update/newsletters/?token=tok")
    assert response.status_code == 200
    recipient.refresh_from_db()
    assert _topic_words(recipient) == ACTIVE_WORDS, "default topics must be the active keywords"


@pytest.mark.django_db
def test_logged_in_registration_gets_only_active_topics(journal, keywords, jcom_user):
    _deactivate("0-keyword")
    jcom_user.email = "jcom_user@email.com"
    jcom_user.save()
    client = Client()
    client.force_login(jcom_user)
    client.post(
        f"/{journal.code}/register/newsletters/", {"email": "any@example.com"}, SERVER_NAME="testserver", follow=True
    )
    recipient = Recipient.objects.get(user=jcom_user, journal=journal)
    assert _topic_words(recipient) == ACTIVE_WORDS, "default topics must be the active keywords"


@pytest.mark.django_db
def test_interests_form_offers_only_active_keywords(journal, keywords, jcom_user):
    _deactivate("0-keyword")
    form = WjsInterestsForm(instance=jcom_user, journal=journal, initial={})
    words = set(form.fields["keywords"].queryset.values_list("word", flat=True))
    assert words == ACTIVE_WORDS, "only active keywords must be offered as interests"


def _editor_parameters(editor, journal: Journal) -> StaffWorkloadParameters:
    parameters, __ = StaffWorkloadParameters.objects.get_or_create(user=editor.janeway_account, journal=journal)
    return parameters


@pytest.mark.django_db
def test_assignment_parameters_form_offers_only_active_keywords(journal, keywords, section_editor):
    _deactivate("0-keyword")
    form = UpdateAssignmentParametersForm(instance=_editor_parameters(section_editor, journal))
    words = set(form.fields["keywords"].queryset.values_list("word", flat=True))
    assert words == ACTIVE_WORDS, "only active keywords must be offered as editor keywords"


@pytest.mark.django_db
def test_assignment_parameters_save_keeps_deactivated_staff_keyword(
    journal, keywords, section_editor, roles, eo_group
):
    parameters = _editor_parameters(section_editor, journal)
    StaffKeyword.objects.create(parameters=parameters, keyword=Keyword.objects.get(word="0-keyword"), weight=7)
    StaffKeyword.objects.create(parameters=parameters, keyword=Keyword.objects.get(word="1-keyword"))
    _deactivate("0-keyword")
    client = Client()
    client.force_login(section_editor)
    response = client.post(
        f"/{journal.code}/update/parameters/",
        {"keywords": [Keyword.objects.get(word="2-keyword").pk], "workload": 10},
    )
    assert response.status_code == 302
    weights = dict(StaffKeyword.objects.filter(parameters=parameters).values_list("keyword__word", "weight"))
    assert weights == {"0-keyword": 7, "2-keyword": 0}, "deactivated keyword and its weight survive the save"
