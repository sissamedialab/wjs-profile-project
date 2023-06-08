import datetime
import random
from urllib.parse import urlencode
from zoneinfo import ZoneInfo

import lxml.html
import pytest
from cms.models import Page
from comms.models import NewsItem
from conftest import set_jcom_settings, set_jcom_theme
from django.conf import settings
from django.contrib.contenttypes.models import ContentType
from django.core import mail, management
from django.db.models import Q
from django.test import Client
from django.test.client import RequestFactory
from django.urls import reverse
from django.utils import timezone
from submission.models import Article, Keyword
from utils import setting_handler
from utils.install import update_issue_types
from utils.setting_handler import get_setting

from wjs.jcom_profile.models import Recipient
from wjs.jcom_profile.newsletter.service import NewsletterMailerService
from wjs.jcom_profile.utils import generate_token


def select_random_keywords(keywords):
    """
    Return a sampled set of keywords from the given ones.
    :param keywords: Keyword list fixture
    :return: A sampled list of Keyword
    """
    return random.sample(list(keywords), random.randint(1, len(keywords)))


def check_email_regarding_language(outbox, language, kind):
    """
    Check that the email is rendered in the expected language
    """
    for email in outbox:
        assert email.subject == f"{language} publication alert {kind.replace('_', ' ')} subject"


def check_email_body(outbox, journal):
    """
    Check that expected news and articles are correctly rendered in newsletter for each user.
    :param outbox: Django mail.outbox containing email that are sent after send_newsletter_notifications call.
    """
    from_email = get_setting(
        "general",
        "from_address",
        journal,
        create=False,
        default=True,
    )

    for email in outbox:
        user_email = email.to[0]
        assert email.from_email == from_email.value
        try:
            recipient = Recipient.objects.get(user__email=user_email)
        except Recipient.DoesNotExist:
            recipient = Recipient.objects.get(email=user_email)
        user_keywords = recipient.topics.all()
        for topic in user_keywords:
            articles = Article.objects.filter(keywords__in=[topic], date_published__date__gt=timezone.now())
            for article in articles:
                assert article.title in email.body
        news_items = NewsItem.objects.filter(posted__date__gt=timezone.now())
        if recipient.news:
            for item in news_items:
                assert reverse("core_news_item", args=(item.pk,)) in email.body
        else:
            for item in news_items:
                assert reverse("core_news_item", args=(item.pk,)) not in email.body
        if getattr(settings, "NEWSLETTER_URL", ""):
            assert f"{settings.NEWSLETTER_URL}/page-privacy" in email.body
        else:
            assert "http://testserver.org/page-privacy" in email.body


@pytest.mark.django_db
def test_no_newsletters_must_be_sent_when_no_new_articles_with_interesting_keywords_and_news_exist(
    account_factory,
    article_factory,
    news_item_factory,
    recipient_factory,
    section_factory,
    newsletter_factory,
    keyword_factory,
    custom_newsletter_setting,
    keywords,
    journal,
    mock_premailer_load_url,
):
    newsletter = newsletter_factory()
    content_type = ContentType.objects.get_for_model(journal)
    users = []
    correspondence_author = account_factory()
    for _ in range(10):
        users.append(account_factory())
    for _ in range(10):
        news_item_factory(
            posted=timezone.now() + datetime.timedelta(days=-2),
            content_type=content_type,
            object_id=journal.pk,
        )
    for user in users:
        recipient = recipient_factory(
            user=user,
        )
        selected_keywords = select_random_keywords(keywords)
        for keyword in selected_keywords:
            recipient.topics.add(keyword)
        recipient.save()
    for i in range(5):
        article = article_factory(
            journal=journal,
            date_published=timezone.now() + datetime.timedelta(days=1),
            stage="Published",
            correspondence_author=correspondence_author,
            section=section_factory(),
        )
        article.keywords.add(keyword_factory(word=f"{i}-no"))
        article.keywords.add(keyword_factory(word=f"{i}-interesting"))
        article.save()

    management.call_command("send_newsletter_notifications", journal.code)

    newsletter.refresh_from_db()
    assert newsletter.last_sent.date() == timezone.now().date()
    assert len(mail.outbox) == 0


@pytest.mark.django_db
def test_newsletters_with_news_items_only_must_be_sent(
    account_factory,
    recipient_factory,
    newsletter_factory,
    news_item_factory,
    custom_newsletter_setting,
    keywords,
    journal,
    mock_premailer_load_url,
):
    newsletter = newsletter_factory()
    news_user, no_news_user = account_factory(email="news@news.it"), account_factory(email="nonews@nonews.it")
    content_type = ContentType.objects.get_for_model(journal)

    news_recipient = recipient_factory(user=news_user, news=True)
    news_item_factory(
        posted=timezone.now() + datetime.timedelta(days=1),
        content_type=content_type,
        object_id=journal.pk,
    )
    recipient_factory(user=no_news_user, news=False)

    management.call_command("send_newsletter_notifications", journal.code)

    assert newsletter.last_sent.date() == timezone.now().date()
    assert len(mail.outbox) == 1
    assert mail.outbox[0].to == [news_recipient.newsletter_destination_email]

    check_email_body(mail.outbox, journal)


@pytest.mark.parametrize("language", ("en", "es", "pt"))
@pytest.mark.django_db
def test_newsletters_with_articles_only_must_be_sent(
    account_factory,
    recipient_factory,
    newsletter_factory,
    article_factory,
    section_factory,
    keyword_factory,
    custom_newsletter_setting,
    journal,
    mock_premailer_load_url,
    language,
):
    newsletter = newsletter_factory()
    correspondence_author = account_factory()
    newsletter_user_keyword = keyword_factory()
    newsletter_article_user, no_newsletter_article_user = account_factory(email="article@article.it"), account_factory(
        email="noarticle@article.it",
    )
    newsletter_article = article_factory(
        journal=journal,
        date_published=timezone.now() + datetime.timedelta(days=1),
        stage="Published",
        correspondence_author=correspondence_author,
        section=section_factory(),
    )
    newsletter_article.keywords.add(newsletter_user_keyword)
    newsletter_article.save()

    no_newsletter_article = article_factory(
        journal=journal,
        date_published=timezone.now() + datetime.timedelta(days=1),
        stage="Published",
        correspondence_author=correspondence_author,
        section=section_factory(),
    )
    no_newsletter_article.keywords.add(keyword_factory())
    no_newsletter_article.save()

    newsletter_article_recipient = recipient_factory(
        user=newsletter_article_user,
        news=True,
        language=language,
    )
    newsletter_article_recipient.topics.add(newsletter_user_keyword)
    newsletter_article_recipient.save()

    recipient_factory(user=no_newsletter_article_user, news=False, language=language)

    management.call_command("send_newsletter_notifications", journal.code)

    assert newsletter.last_sent.date() == timezone.now().date()
    assert len(mail.outbox) == 1
    assert mail.outbox[0].to == [newsletter_article_recipient.newsletter_destination_email]

    check_email_body(mail.outbox, journal)
    check_email_regarding_language(mail.outbox, language=language, kind="email")


@pytest.mark.django_db
def test_newsletters_are_correctly_sent_with_both_news_and_articles_for_subscribed_users_and_anonymous_users(
    account_factory,
    article_factory,
    news_item_factory,
    recipient_factory,
    section_factory,
    newsletter_factory,
    custom_newsletter_setting,
    keywords,
    journal,
    mock_premailer_load_url,
):
    newsletter = newsletter_factory()
    content_type = ContentType.objects.get_for_model(journal)
    correspondence_author = account_factory()
    for _ in range(10):
        news_item_factory(
            posted=timezone.now() + datetime.timedelta(days=1),
            content_type=content_type,
            object_id=journal.pk,
        )
    for i in range(30):
        is_anonymous = random.choice([True, False])
        if is_anonymous:
            recipient = recipient_factory(email=f"randomuser{i}@random.com")
        else:
            recipient = recipient_factory(user=account_factory())
        selected_keywords = select_random_keywords(keywords)
        for keyword in selected_keywords:
            recipient.topics.add(keyword)
        recipient.save()
    for _ in range(50):
        article = article_factory(
            journal=journal,
            date_published=timezone.now() + datetime.timedelta(days=1),
            stage="Published",
            correspondence_author=correspondence_author,
            section=section_factory(),
        )
        article_keywords = select_random_keywords(keywords)
        for keyword in article_keywords:
            article.keywords.add(keyword)
        article.save()

    management.call_command("send_newsletter_notifications", journal.code)

    newsletter.refresh_from_db()
    assert newsletter.last_sent.date() == timezone.now().date()
    filtered_articles = Article.objects.filter(date_published__date__gt=timezone.now())
    emailed_subscribers = Recipient.objects.filter(
        Q(topics__in=filtered_articles.values_list("keywords")) | Q(news=True),
    ).distinct()
    assert len(mail.outbox) == emailed_subscribers.count()

    check_email_body(mail.outbox, journal)


@pytest.mark.django_db
def test_two_recipients_one_news(
    account_factory,
    recipient_factory,
    newsletter_factory,
    news_item_factory,
    custom_newsletter_setting,
    journal,
    mock_premailer_load_url,
):
    """Service test.

    This test is useful only to study
    `send_newsletter_notifications.handle` from a known and simple
    state. For instance, add a breakpoint before `for subscriber` and
    run this test (with pytest -s).

    """

    newsletter = newsletter_factory()
    content_type = ContentType.objects.get_for_model(journal)
    # Two news recipients
    nr1 = recipient_factory(user=account_factory(), news=True)
    nr2 = recipient_factory(user=account_factory(), news=True)
    news_item_factory(
        posted=timezone.now() + datetime.timedelta(days=1),
        content_type=content_type,
        object_id=journal.pk,
    )

    management.call_command("send_newsletter_notifications", journal.code)

    assert newsletter.last_sent.date() == timezone.now().date()
    assert len(mail.outbox) == 2
    # msg.to is a list (i.e. a message can have multiple "To:")
    # here I just assume that there is only one To per message
    mail_recipients = [msg.to[0] for msg in mail.outbox]
    assert nr1.user.email in mail_recipients
    assert nr2.user.email in mail_recipients

    check_email_body(mail.outbox, journal)


@pytest.mark.django_db
def test_two_recipients_one_article(
    account_factory,
    recipient_factory,
    newsletter_factory,
    article_factory,
    keyword_factory,
    custom_newsletter_setting,
    journal,
    mock_premailer_load_url,
):
    """Service test.

    This test is useful only to study
    `send_newsletter_notifications.handle` from a known and simple
    state. For instance, add a breakpoint before `for subscriber` and
    run this test (with pytest -s).

    """

    newsletter = newsletter_factory()

    # One published article, with a known kwd
    kwd1 = keyword_factory()
    tomorrow = timezone.now() + datetime.timedelta(days=1)
    a1 = article_factory(journal=journal, date_published=tomorrow)
    a1.keywords.add(kwd1)

    # Two newsletter recipients with the same topic (kwd)
    nr1 = recipient_factory(user=account_factory(), news=False)
    nr1.topics.add(kwd1)
    nr2 = recipient_factory(user=account_factory(), news=False)
    nr2.topics.add(kwd1)

    management.call_command("send_newsletter_notifications", journal.code)

    assert newsletter.last_sent.date() == timezone.now().date()
    assert len(mail.outbox) == 2
    check_email_body(mail.outbox, journal)


@pytest.mark.django_db
def test_one_recipient_one_article_two_topics(
    recipient_factory,
    newsletter_factory,
    article_factory,
    keyword_factory,
    custom_newsletter_setting,
    journal,
    mock_premailer_load_url,
):
    """
    Test recipients not related to any account.
    """

    newsletter = newsletter_factory()

    # One published article, with a known kwd
    kwd1 = keyword_factory()
    kwd2 = keyword_factory()
    tomorrow = timezone.now() + datetime.timedelta(days=1)
    a1 = article_factory(journal=journal, date_published=tomorrow)
    a1.keywords.add(kwd1)
    a1.keywords.add(kwd2)

    # Two newsletter recipients with the same topic (kwd)
    nr1 = recipient_factory(journal=journal, news=False, email="nr1@email.com")
    nr1.topics.add(kwd1)
    nr1.topics.add(kwd2)
    nr2 = recipient_factory(journal=journal, news=False, email="nr2@email.com")
    nr2.topics.add(kwd1)
    nr2.topics.add(kwd2)

    management.call_command("send_newsletter_notifications", journal.code)

    assert newsletter.last_sent.date() == timezone.now().date()
    assert len(mail.outbox) == 2
    check_email_body(mail.outbox, journal)


rome_tz = ZoneInfo("Europe/Rome")
last_sent_23 = datetime.datetime(2023, 3, 26, 23, 0, 3, tzinfo=rome_tz)
last_sent_11 = datetime.datetime(2023, 3, 26, 11, 0, 3, tzinfo=rome_tz)
CASES = (
    # Papers imported from wjapp have dates set from info gathered
    # from the XML, that only reports a date (i.e. not a datetime).
    (last_sent_23, datetime.datetime(2023, 3, 27, tzinfo=rome_tz), False),
    (last_sent_23, last_sent_23 + datetime.timedelta(hours=1), False),
    (last_sent_23, last_sent_23 + datetime.timedelta(hours=2), False),
    (last_sent_23, last_sent_23 + datetime.timedelta(hours=3), False),
    (last_sent_23, last_sent_23 + datetime.timedelta(hours=4), False),
    #
    (last_sent_11, datetime.datetime(2023, 3, 27, tzinfo=rome_tz), False),
    (last_sent_11, last_sent_11 + datetime.timedelta(hours=1), True),
    (last_sent_11, last_sent_11 + datetime.timedelta(hours=2), True),
    (last_sent_11, last_sent_11 + datetime.timedelta(hours=3), True),
    (last_sent_11, last_sent_11 + datetime.timedelta(hours=4), True),
    #
    (last_sent_11, last_sent_11 + datetime.timedelta(hours=12), True),
    (last_sent_11, last_sent_11 + datetime.timedelta(hours=13), False),
    (last_sent_11, last_sent_11 + datetime.timedelta(hours=14), False),
    (last_sent_11, last_sent_11 + datetime.timedelta(hours=15), False),
)


@pytest.mark.parametrize(["last_sent", "date_published", "empty"], CASES)
@pytest.mark.django_db
def test_last_sent_timezone(last_sent, date_published, empty, article_factory, journal):
    nms = NewsletterMailerService()
    a1 = article_factory(journal=journal, date_published=timezone.now())

    a1.date_published = date_published
    a1.save()
    recipients, articles, news = nms._get_objects(journal, last_sent)
    if empty:
        assert len(articles) == 0
    else:
        assert len(articles) == 1
        assert articles[0] == a1


@pytest.mark.django_db
def test_registration_as_non_logged_user_creates_a_recipient_and_redirects_to_email_sent_view(
    client,
    journal,
    recipient_factory,
    newsletter,
    article_factory,
    keyword_factory,
    custom_newsletter_setting,
    mock_premailer_load_url,
):
    before_recipients = [x.pk for x in Recipient.objects.all()]
    url = f"/{journal.code}/register/newsletters/"
    response = client.post(url, {"email": "nr1@email.com"}, SERVER_NAME="testserver", follow=True)
    new_recipients = Recipient.objects.exclude(pk__in=before_recipients)
    assert new_recipients.count() == 1
    new_recipient = new_recipients.first()
    # Check new Recipient object's fields
    assert new_recipient.user is None
    assert new_recipient.email == "nr1@email.com"
    assert new_recipient.journal == journal
    assert len(new_recipient.newsletter_token) > 0
    last_url, status_code = response.redirect_chain[-1]
    assert last_url == f"/{journal.code}/register/newsletters/email-sent/"
    assert "reminder" not in response.context_data.keys()


@pytest.mark.django_db
def test_registration_as_non_logged_user_when_there_is_already_a_recipient(
    client,
    journal,
    recipient_factory,
    newsletter,
    article_factory,
    keyword_factory,
    custom_newsletter_setting,
    mock_premailer_load_url,
):
    # Please note that we set the `news=True`, as if the recipient had
    # already set his preferences. This is to distinguish from the
    # case when an anonymous user registers the same email multiple
    # times but never edits his preferences (this case is considered
    # as a "new registration" - see
    # views.AnonymousUserNewsletterRegistration:1173)
    r1 = recipient_factory(journal=journal, news=True, email="nr1@email.com")
    content_type = ContentType.objects.get_for_model(journal)
    Page.objects.create(content_type=content_type, object_id=journal.pk, name="privacy")

    before_recipients = [x.pk for x in Recipient.objects.all()]

    url = f"/{journal.code}/register/newsletters/"
    response = client.post(url, {"email": r1.email}, SERVER_NAME="testserver", follow=True)

    # No new Recipient is created
    new_recipients = Recipient.objects.exclude(pk__in=before_recipients)
    assert new_recipients.count() == 0

    # We get the usual message in the browser (no indication that this
    # is a reminder, for security reasons)
    last_url, status_code = response.redirect_chain[-1]
    assert last_url == f"/{journal.code}/register/newsletters/email-sent/"

    # Check the email
    assert len(mail.outbox) == 1
    from_email = get_setting(
        "general",
        "from_address",
        journal,
        create=False,
        default=True,
    )
    mail_message = mail.outbox[0]
    assert mail_message.from_email == from_email.value
    assert mail_message.to == [r1.email]
    assert "Please note that you are already subscribed" in mail_message.body
    if getattr(settings, "NEWSLETTER_URL", ""):
        assert f"{settings.NEWSLETTER_URL}/JCOM/site/privacy" in mail_message.body
    else:
        assert "http://testserver.org/JCOM/site/privacy" in mail_message.body


@pytest.mark.parametrize("language", ("en", "es", "pt"))
@pytest.mark.django_db
def test_registration_as_logged_user_via_post_in_homepage_plugin(
    jcom_user,
    client,
    journal,
    keyword_factory,
    language,
    settings,
):
    # Set an email for the user
    jcom_user.email = "jcom_user@email.com"
    jcom_user.save()
    assert Recipient.objects.filter(user=jcom_user, journal=journal).count() == 0
    # Add some keywords to the journal
    kwd_count = 3
    keywords = [keyword_factory() for _ in range(kwd_count)]
    journal.keywords.set(keywords)
    assert Keyword.objects.count() == kwd_count
    # Force language in Django test client https://docs.djangoproject.com/en/4.1/topics/testing/tools/#setting-the-language   # noqa: E501
    client.cookies.load({settings.LANGUAGE_COOKIE_NAME: language})
    client.force_login(jcom_user)
    url = f"/{journal.code}/register/newsletters/"
    response = client.post(
        url,
        {
            "email": "any@example.com",  # Any email does the same: the logged user takes precedence!
        },
        SERVER_NAME="testserver",
        follow=True,
    )

    # Check that a new Recipient was created
    new_recipients = Recipient.objects.filter(user=jcom_user, journal=journal)
    assert new_recipients.count() == 1

    # Check the new Recipient's characteristics
    new_recipient = new_recipients.first()
    assert new_recipient.newsletter_token == ""
    #  all active at first
    assert new_recipient.news is True
    assert new_recipient.topics.count() == new_recipient.journal.keywords.count()
    assert new_recipient.language == language

    # Check the redirect
    last_url, status_code = response.redirect_chain[-1]
    assert last_url == f"/{journal.code}/update/newsletters/"
    # Check the email
    assert len(mail.outbox) == 0


@pytest.mark.django_db
def test_registration_as_logged_user_via_link_in_profile_page(
    jcom_user,
    client,
    journal,
    keyword_factory,
):
    # Set an email for the user
    jcom_user.email = "jcom_user@email.com"
    jcom_user.save()
    assert Recipient.objects.filter(user=jcom_user, journal=journal).count() == 0
    # Add some keywords to the journal
    kwd_count = 3
    keywords = [keyword_factory() for _ in range(kwd_count)]
    journal.keywords.set(keywords)
    assert Keyword.objects.count() == kwd_count

    # Login and make visit the update-newsletter page (reachable from the profile page)
    client.force_login(jcom_user)
    url = f"/{journal.code}/update/newsletters/"
    response = client.get(url, SERVER_NAME="testserver", follow=True)

    # Check that a new Recipient was created
    new_recipients = Recipient.objects.filter(user=jcom_user, journal=journal)
    assert new_recipients.count() == 1

    # Check the new Recipient's characteristics
    new_recipient = new_recipients.first()
    assert new_recipient.newsletter_token == ""
    #  all active at first
    assert new_recipient.news is True
    assert new_recipient.topics.count() == new_recipient.journal.keywords.count()

    # Check the response
    assert response.status_code == 200
    # Check the email
    assert len(mail.outbox) == 0


@pytest.mark.django_db
def test_update_newsletter_subscription_get_single_language(jcom_user, keywords, journal_factory):
    """
    Language field is hidden if only one language is configured in the journal.
    """
    journal = journal_factory("due")
    # language not available in LANGUAGES is ignored
    setting_handler.save_setting("general", "journal_languages", journal, ["es"])
    journal.keywords.set(keywords)

    client = Client()
    client.force_login(jcom_user)
    url = f"/{journal.code}/update/newsletters/"
    response = client.get(url, follow=True)
    assert response.status_code == 200
    html = lxml.html.fromstring(response.content.decode())
    for keyword in keywords:
        assert html.findall(f".//input[@type='checkbox'][@name='topics'][@value='{keyword.pk}']")
    assert len(html.findall(".//option")) == 0


@pytest.mark.django_db
def test_update_newsletter_subscription_show_language_select(jcom_user, keywords, journal_factory):
    """
    Language and keyword fields are filled with journal current data.

    Language field is shown because multiple languages are configured in the journal, default language is selected.

    This is similar to test_update_newsletter_subscription_show_dynamic_language_select, except that the languages
    settings does not change.
    """
    journal = journal_factory("due")
    # language not available in LANGUAGES is ignored
    setting_handler.save_setting("general", "journal_languages", journal, ["en", "es", "it"])
    journal.keywords.set(keywords)

    client = Client()
    client.force_login(jcom_user)
    url = f"/{journal.code}/update/newsletters/"
    response = client.get(url, follow=True)
    assert response.status_code == 200
    html = lxml.html.fromstring(response.content.decode())
    for keyword in keywords:
        assert html.findall(f".//input[@type='checkbox'][@name='topics'][@value='{keyword.pk}']")
    assert len(html.findall(".//option")) == 2
    assert html.findall(".//option[@value='es']")
    assert html.findall(".//option[@value='en'][@selected]")


@pytest.mark.django_db
def test_update_newsletter_subscription_show_dynamic_language_select(jcom_user, keywords, journal_factory):
    """
    Language and keyword fields are filled with journal current data.

    Test that fields provided by the form are updated if the journal languages changes between invocations.

    This is a regression test for wjs-profile-project#23.
    """
    journal = journal_factory("due")
    # language not available in LANGUAGES is ignored
    setting_handler.save_setting("general", "journal_languages", journal, ["es"])
    journal.keywords.set(keywords)

    # First run with only one language configured -> no language field is shown
    client = Client()
    client.force_login(jcom_user)
    url = f"/{journal.code}/update/newsletters/"
    response = client.get(url, follow=True)
    assert response.status_code == 200
    html = lxml.html.fromstring(response.content.decode())
    for keyword in keywords:
        assert html.findall(f".//input[@type='checkbox'][@name='topics'][@value='{keyword.pk}']")
    assert len(html.findall(".//option")) == 0

    # Second run with two languages configured ("it" is not in LANGUAGES, and it is ignored) -> language field is shown
    setting_handler.save_setting("general", "journal_languages", journal, ["en", "es", "it"])
    client.force_login(jcom_user)
    url = f"/{journal.code}/update/newsletters/"
    response = client.get(url, follow=True)
    assert response.status_code == 200
    html = lxml.html.fromstring(response.content.decode())
    for keyword in keywords:
        assert html.findall(f".//input[@type='checkbox'][@name='topics'][@value='{keyword.pk}']")
    assert len(html.findall(".//option")) == 2
    assert html.findall(".//option[@value='es']")
    assert html.findall(".//option[@value='en'][@selected]")


@pytest.mark.parametrize("is_news", (True, False))
@pytest.mark.django_db
def test_update_newsletter_subscription(jcom_user, keywords, journal_factory, is_news):
    """
    Recipient is created storing the provided configuration using NewsletterParametersUpdate view.
    """
    journal = journal_factory("due")
    setting_handler.save_setting("general", "journal_languages", journal, ["en", "es"])
    journal.keywords.set(keywords)
    keywords = random.choices(journal.keywords.values_list("id", "word"), k=5)

    client = Client()
    client.force_login(jcom_user)
    url = f"/{journal.code}/update/newsletters/"
    # Pass a language in the POST data
    data = {"topics": [k[0] for k in keywords], "news": is_news, "language": "es"}
    response = client.post(url, data, follow=True)
    assert response.status_code == 200

    user_recipient = Recipient.objects.get(user=jcom_user, journal=journal)
    assert user_recipient.language == "es"
    topics = user_recipient.topics.all()
    for topic in topics:
        assert topic.word in [k[1] for k in keywords]
    assert "Thank you for setting your preferences" in response.content.decode()


@pytest.mark.django_db
def test_registered_user_newsletter_unsubscription(jcom_user, journal):
    client = Client()
    client.force_login(jcom_user)
    user_recipient = Recipient.objects.create(user=jcom_user, journal=journal)

    url = f"/{journal.code}/newsletters/unsubscribe/{user_recipient.pk}"
    response = client.get(url, follow=True)
    redirect_url, status_code = response.redirect_chain[-1]

    assert status_code == 302
    assert redirect_url == reverse("unsubscribe_newsletter_confirm")

    with pytest.raises(Recipient.DoesNotExist):
        user_recipient.refresh_from_db()


@pytest.mark.parametrize("language", ("en", "es", "pt"))
@pytest.mark.django_db
def test_register_to_newsletter_as_anonymous_user(
    journal,
    custom_newsletter_setting,
    mock_premailer_load_url,
    language,
    settings,
):
    client = Client()
    # Force language in Django test client https://docs.djangoproject.com/en/4.1/topics/testing/tools/#setting-the-language   # noqa: E501
    client.cookies.load({settings.LANGUAGE_COOKIE_NAME: language})
    url = f"/{journal.code}/register/newsletters/"
    anonymous_email = "anonymous@email.com"
    newsletter_token = generate_token(anonymous_email, journal.code)

    response_get = client.get(url)
    request = RequestFactory().get(url)
    assert response_get.status_code == 200

    data = {"email": anonymous_email}
    response_register = client.post(url, data, follow=True)
    redirect_url, status_code = response_register.redirect_chain[-1]

    anonymous_recipient = Recipient.objects.get(email=anonymous_email)

    assert status_code == 302
    assert redirect_url == reverse("register_newsletters_email_sent")

    assert len(mail.outbox) == 1
    newsletter_email = mail.outbox[0]
    acceptance_url = (
        request.build_absolute_uri(reverse("edit_newsletters")) + f"?{urlencode({'token': newsletter_token})}"
    )
    assert newsletter_email.subject == setting_handler.get_setting(
        "email",
        "publication_alert_subscription_email_subject",
        journal,
    ).processed_value.format(journal, acceptance_url)
    assert anonymous_recipient.newsletter_token in newsletter_email.body
    assert anonymous_recipient.newsletter_token == newsletter_token
    assert anonymous_recipient.language == language

    from_email = get_setting(
        "general",
        "from_address",
        journal,
        create=False,
        default=True,
    )
    assert newsletter_email.from_email == from_email.value
    check_email_regarding_language(mail.outbox, language=language, kind="subscription_email")


@pytest.mark.django_db
def test_anonymous_user_newsletter_edit_without_token_raises_error(journal):
    client = Client()
    url = f"/{journal.code}/update/newsletters/"
    response = client.get(url)
    assert response.status_code == 403


@pytest.mark.django_db
def test_anonymous_user_newsletter_edit_with_nonexistent_token_raises_error(journal):
    client = Client()
    anonymous_email = "anonymous@email.com"
    nonexistent_newsletter_token = generate_token(anonymous_email, journal.code)
    url = f"/{journal.code}/update/newsletters/?{urlencode({'token': nonexistent_newsletter_token})}"
    response = client.get(url)
    assert response.status_code == 403


@pytest.mark.django_db
def test_anonymous_user_newsletter_unsubscription(journal):
    client = Client()
    anonymous_email = "anonymous@email.com"
    newsletter_token = generate_token(anonymous_email, journal.code)
    anonymous_recipient = Recipient.objects.create(
        email=anonymous_email,
        newsletter_token=newsletter_token,
        journal=journal,
    )

    url = f"/{journal.code}/newsletters/unsubscribe/{anonymous_recipient.newsletter_token}/"
    response = client.get(url, follow=True)
    redirect_url, status_code = response.redirect_chain[-1]

    assert status_code == 302
    assert redirect_url == reverse("unsubscribe_newsletter_confirm")
    assert not Recipient.objects.filter(pk=anonymous_recipient.pk)


@pytest.mark.django_db
def test_anonymous_user_recipient_registers_for_second_journal(journal):
    """Test that an anonymous user can subscribe to multiple journals.

    An "anonymous user" is basically just an email.
    It should be possible for the same email (recipient) to receive
    notifications from multiple journals.
    """
    # Let's create a recipient for an anonymous user on the first journal
    anonymous_email = "anonymous@email.com"
    Recipient.objects.create(journal=journal, email=anonymous_email, language="en", news=True)

    # Let's create a second journal and try to register the same anonymous user for this journal
    # TODO: make as a fixture? / factory? / use a factory for the current "journal" fixture?
    from journal import models as journal_models

    journal_due = journal_models.Journal.objects.create(code="JOURNALDUE", domain="testserver2.org")
    journal_due.title = "Test Journal DUE: A journal of tests DUE"
    journal_due.save()
    # TODO: ignoring  update_issue_types / set_jcom_theme / set_jcom_settings /

    # I choose to ignore the language, forms, etc. because here I want to concentrate on the models.

    # Let's create a recipient for the same anonymous user on a different journal (now this fails)
    Recipient.objects.create(journal=journal_due, email=anonymous_email, language="en", news=True)
    #                                ^^^^^^^^^^^

    assert True


@pytest.mark.django_db
def test_anonymous_user_recipient_confirms_registration_to_second_journal(journal, client, settings):
    """Test that an anonymous user can subscribe to multiple journals.

    Here we test that the recipient can visit the page where he sets
    his newslettere parameters (topics, language,...).

    The access token might depend only on the email, so it will be the
    same for the "same" recipient on multiple journals.
    """

    # The anonymous user registers on the first journal
    url = f"/{journal.code}/register/newsletters/"
    anonymous_email = "anonymous@email.com"
    data = {"email": anonymous_email}
    client.post(url, data)

    # Let's create a second journal and register the same anonymous user for this journal
    # (see notes on test_anonymous_user_recipient_registers_for_second_journal)
    from journal import models as journal_models

    journal_due = journal_models.Journal.objects.create(code="JCOMAL", domain="testserver2.org")
    journal_due.title = "Test Journal DUE: A journal of tests DUE"
    journal_due.save()
    update_issue_types(journal_due)
    set_jcom_theme(journal_due)
    set_jcom_settings(journal_due)

    # The anonymous user registers on the second journal
    # (here only the `url` changes)
    url = f"/{journal_due.code}/register/newsletters/"
    client.post(url, data)

    recipients = Recipient.objects.filter(email=anonymous_email)
    assert recipients.count() == 2

    # The anonimous user follows one of the received links
    newsletter_token = generate_token(anonymous_email, journal.code)
    acceptance_url = reverse("edit_newsletters") + f"?{urlencode({'token': newsletter_token})}"
    client.get(acceptance_url)

    assert True
