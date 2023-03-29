import datetime
import random
from zoneinfo import ZoneInfo

import pytest
from django.contrib.contenttypes.models import ContentType
from django.urls import reverse

from comms.models import NewsItem
from django.core import mail, management
from django.db.models import Q
from django.utils import timezone
from submission.models import Article
from utils.setting_handler import get_setting

from wjs.jcom_profile.models import Recipient
from wjs.jcom_profile.newsletter.service import NewsletterMailerService


def select_random_keywords(keywords):
    """
    Return a sampled set of keywords from the given ones.
    :param keywords: Keyword list fixture
    :return: A sampled list of Keyword
    """
    return random.sample(list(keywords), random.randint(1, len(keywords)))


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
    mock_premailer_load_url
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
    mock_premailer_load_url
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
    mock_premailer_load_url
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
    )
    newsletter_article_recipient.topics.add(newsletter_user_keyword)
    newsletter_article_recipient.save()

    recipient_factory(user=no_newsletter_article_user, news=False)

    management.call_command("send_newsletter_notifications", journal.code)

    assert newsletter.last_sent.date() == timezone.now().date()
    assert len(mail.outbox) == 1
    assert mail.outbox[0].to == [newsletter_article_recipient.newsletter_destination_email]

    check_email_body(mail.outbox, journal)


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
    mock_premailer_load_url
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
    mock_premailer_load_url
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
    mock_premailer_load_url
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
    mock_premailer_load_url
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
        newsletter_factory,
        article_factory,
        keyword_factory,
        custom_newsletter_setting,
        mock_premailer_load_url,
):
    newsletter = newsletter_factory()
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
    assert last_url == f"/{journal.code}/register/newsletters/email-sent/{new_recipient.pk}/"
    assert "reminder" not in response.context_data.keys()


@pytest.mark.django_db
def test_registration_as_non_logged_user_when_there_is_already_a_recipient(
        client,
        journal,
        recipient_factory,
        newsletter_factory,
        article_factory,
        keyword_factory,
        custom_newsletter_setting,
        mock_premailer_load_url,
):
    newsletter = newsletter_factory()
    r1 = recipient_factory(journal=journal, news=False, email="nr1@email.com")
    before_recipients = [x.pk for x in Recipient.objects.all()]
    url = f"/{journal.code}/register/newsletters/"
    response = client.post(url, {"email": "nr1@email.com"}, SERVER_NAME="testserver", follow=True)
    new_recipients = Recipient.objects.exclude(pk__in=before_recipients)
    # No new Recipient is created
    assert new_recipients.count() == 0
    last_url, status_code = response.redirect_chain[-1]
    # Check that reminder=1 is in the url
    assert last_url == f"/{journal.code}/register/newsletters/email-sent/{r1.pk}/?reminder=1"
    assert response.context_data.get("reminder", None) == True
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
    assert mail_message.to == ["nr1@email.com"]
    assert "Please note that you are already subscribed" in mail_message.body


@pytest.mark.django_db
def test_registration_as_logged_user_when_a_recipient_does_not_exist(
        jcom_user,
        client,
        journal,
        recipient_factory,
        newsletter_factory,
        article_factory,
        keyword_factory,
        custom_newsletter_setting,
        mock_premailer_load_url,
):
    newsletter = newsletter_factory()
    # Set an email for the user
    jcom_user.email = "jcom_user@email.com"
    jcom_user.save()
    assert Recipient.objects.filter(user=jcom_user, journal=journal).count() == 0
    # Login and make the POST call
    client.force_login(jcom_user)
    url = f"/{journal.code}/register/newsletters/"
    response = client.post(url, {"email": jcom_user.email}, SERVER_NAME="testserver", follow=True)
    # Check that a new Recipient was created
    new_recipients = Recipient.objects.filter(user=jcom_user, journal=journal)
    assert new_recipients.count() == 1
    # Check the new Recipient's characteristics
    new_recipient = new_recipients.first()
    assert new_recipient.newsletter_token == ""
    # Check the redirect
    last_url, status_code = response.redirect_chain[-1]
    assert last_url == f"/{journal.code}/update/newsletters/"
    # Check the email
    assert len(mail.outbox) == 0
