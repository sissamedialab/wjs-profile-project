import datetime
import random

import pytest
from comms.models import NewsItem
from django.core import mail, management
from django.db.models import Q
from django.utils import timezone
from submission.models import Article

from wjs.jcom_profile.models import Recipient


def select_random_keywords(keywords):
    """
    Return a sampled set of keywords from the given ones.
    :param keywords: Keyword list fixture
    :return: A sampled list of Keyword
    """
    return random.sample(list(keywords), random.randint(1, len(keywords)))


def check_email_body(outbox):
    """
    Check that expected news and articles are correctly rendered in newsletter for each user.
    :param outbox: Django mail.outbox containing email that are sent after send_newsletter_notifications call.
    """
    for email in outbox:
        user_email = email.to[0]
        try:
            user_keywords = Recipient.objects.get(user__email=user_email).topics.all()
        except Recipient.DoesNotExist:
            user_keywords = Recipient.objects.get(email=user_email).topics.all()
        for topic in user_keywords:
            articles = Article.objects.filter(keywords__in=[topic], date_published__date__gt=timezone.now())
            for article in articles:
                assert article.title in email.body
        news_items = NewsItem.objects.filter(posted__date__gt=timezone.now())
        for item in news_items:
            assert item.title in email.body


@pytest.mark.django_db
def test_no_newsletters_must_be_sent_when_no_new_articles_with_interesting_keywords_and_news_exist(
    account_factory,
    article_factory,
    news_item_factory,
    recipient_factory,
    section_factory,
    newsletter_factory,
    keyword_factory,
    keywords,
    journal,
):
    newsletter = newsletter_factory()
    users = []
    correspondence_author = account_factory()
    for _ in range(10):
        users.append(account_factory())
    for _ in range(10):
        news_item_factory(
            posted=timezone.now() + datetime.timedelta(days=-2),
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

    management.call_command("send_newsletter_notifications")

    newsletter.refresh_from_db()
    assert newsletter.last_sent.date() == timezone.now().date()
    assert len(mail.outbox) == 0


@pytest.mark.django_db
def test_newsletters_with_news_items_only_must_be_sent(
    account_factory,
    recipient_factory,
    newsletter_factory,
    news_item_factory,
    keywords,
    journal,
):
    newsletter = newsletter_factory()
    news_user, no_news_user = account_factory(email="news@news.it"), account_factory(email="nonews@nonews.it")

    news_recipient = recipient_factory(user=news_user, news=True)
    news_item_factory(
        posted=timezone.now() + datetime.timedelta(days=1),
    )
    recipient_factory(user=no_news_user, news=False)

    management.call_command("send_newsletter_notifications")

    assert newsletter.last_sent.date() == timezone.now().date()
    assert len(mail.outbox) == 1
    assert mail.outbox[0].to == [news_recipient.newsletter_destination_email]

    check_email_body(mail.outbox)


@pytest.mark.django_db
def test_newsletters_with_articles_only_must_be_sent(
    account_factory,
    recipient_factory,
    newsletter_factory,
    article_factory,
    section_factory,
    keyword_factory,
    journal,
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

    management.call_command("send_newsletter_notifications")

    assert newsletter.last_sent.date() == timezone.now().date()
    assert len(mail.outbox) == 1
    assert mail.outbox[0].to == [newsletter_article_recipient.newsletter_destination_email]

    check_email_body(mail.outbox)


@pytest.mark.django_db
def test_newsletters_are_correctly_sent_with_both_news_and_articles_for_subscribed_users_and_anonymous_users(
    account_factory,
    article_factory,
    news_item_factory,
    recipient_factory,
    section_factory,
    newsletter_factory,
    keywords,
    journal,
):
    newsletter = newsletter_factory()
    correspondence_author = account_factory()
    for _ in range(10):
        news_item_factory(
            posted=timezone.now() + datetime.timedelta(days=1),
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

    management.call_command("send_newsletter_notifications")

    newsletter.refresh_from_db()
    assert newsletter.last_sent.date() == timezone.now().date()
    filtered_articles = Article.objects.filter(date_published__date__gt=timezone.now())
    emailed_subscribers = Recipient.objects.filter(
        Q(topics__in=filtered_articles.values_list("keywords")) | Q(news=True),
    ).distinct()
    assert len(mail.outbox) == emailed_subscribers.count()

    check_email_body(mail.outbox)


@pytest.mark.django_db
def test_two_recipients_one_news(
    account_factory,
    recipient_factory,
    newsletter_factory,
    news_item_factory,
    journal,
):
    """Service test.

    This test is useful only to study
    `send_newsletter_notifications.handle` from a known and simple
    state. For instance, add a breakpoint before `for subscriber` and
    run this test (with pytest -s).

    """
    newsletter = newsletter_factory()
    # Two news recipients
    nr1 = recipient_factory(user=account_factory(), news=True)
    nr2 = recipient_factory(user=account_factory(), news=True)
    news_item_factory(
        posted=timezone.now() + datetime.timedelta(days=1),
    )

    management.call_command("send_newsletter_notifications")

    assert newsletter.last_sent.date() == timezone.now().date()
    assert len(mail.outbox) == 2
    # msg.to is a list (i.e. a message can have multiple "To:")
    # here I just assume that there is only one To per message
    mail_recipients = [msg.to[0] for msg in mail.outbox]
    assert nr1.user.email in mail_recipients
    assert nr2.user.email in mail_recipients

    check_email_body(mail.outbox)


@pytest.mark.django_db
def test_two_recipients_one_article(
    account_factory,
    recipient_factory,
    newsletter_factory,
    article_factory,
    keyword_factory,
    journal,
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

    management.call_command("send_newsletter_notifications")

    assert newsletter.last_sent.date() == timezone.now().date()
    assert len(mail.outbox) == 2
    check_email_body(mail.outbox)


@pytest.mark.django_db
def test_one_recipient_one_article_two_topics(
    recipient_factory,
    newsletter_factory,
    article_factory,
    keyword_factory,
    journal,
):
    """Test recipients not related to any account.

    Bozza! :)
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

    management.call_command("send_newsletter_notifications")

    assert newsletter.last_sent.date() == timezone.now().date()
    assert len(mail.outbox) == 2
    check_email_body(mail.outbox)
