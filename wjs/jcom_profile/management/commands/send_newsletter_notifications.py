"""Management command to add a role."""
import datetime
from typing import List
from unittest.mock import Mock

from comms.models import NewsItem
from core.middleware import GlobalRequestMiddleware
from django.conf import settings
from django.core.mail import send_mail
from django.core.management.base import BaseCommand
from django.db.models import Q
from django.template.loader import render_to_string
from journal.models import Journal
from submission.models import Article
from utils.management.commands.test_fire_event import create_fake_request

from wjs.jcom_profile.models import Newsletter, Recipient


class Command(BaseCommand):
    help = "Send newsletter to enrolled users. This command is intended to be used via a cron task."  # noqa

    def render_and_send_newsletter(
        self,
        subscriber: Recipient,
        rendered_articles: List[str],
        rendered_news: List[str],
    ):
        """
        Send the newsletter email to subscriber.

        :param subscriber: The subscriber Recipient model.
        :param rendered_articles: The articles to be rendered in newsletter email.
        :param rendered_news: The news to be rendered in newsletter emails.
        """
        from premailer import transform

        newsletter_content = render_to_string(
            "newsletters/newsletter_template.html",
            {"subscriber": subscriber.user, "articles": "".join(rendered_articles), "news": "".join(rendered_news)},
        )
        processed = transform(newsletter_content)

        send_mail(
            f"{subscriber.journal} journal newsletter - {datetime.date.today()}",
            processed,
            settings.DEFAULT_FROM_EMAIL,
            [subscriber.newsletter_destination_email],
            fail_silently=False,
        )

    def handle(self, *args, **options):
        """
        Command entry point.

        Use the unique Newsletter object (creating it if non-existing) to filter articles and news to be sent
        to users based on the last time newsletters have been delivered. Each user is notified considering their
        interests (i.e. topics saved in their Recipient object).
        """
        newsletter, created = Newsletter.objects.get_or_create()
        last_sent = newsletter.last_sent
        if created:
            self.stdout.write(
                self.style.WARNING("A Newsletter object has been created."),
            )
        filtered_articles = Article.objects.filter(date_published__date__gt=last_sent)
        filtered_news = NewsItem.objects.filter(posted__date__gt=last_sent)
        filtered_subscribers = Recipient.objects.filter(
            Q(topics__in=filtered_articles.values_list("keywords")) | Q(news=True),
        ).distinct()
        articles_list = list(filtered_articles)

        # Templates from themes are found only when there is a request with a journal attached to it.
        # As alternative, J. uses a template from the journal settings. See
        # - cron/management/commands/send_publication_notifications.py
        jcom_code = "JCOM"
        jcom = Journal.objects.get(code=jcom_code)
        fake_request = create_fake_request(user=None, journal=jcom)
        # Workaround for possible override in DEBUG mode
        # (please read utils.template_override_middleware:60)
        fake_request.GET.get = Mock(return_value=False)
        GlobalRequestMiddleware.process_request(fake_request)

        for subscriber in filtered_subscribers:
            rendered_articles = []
            rendered_news = []
            for article in articles_list:
                if article.keywords.intersection(subscriber.topics.all()):
                    if not hasattr(article, "rendered"):
                        article.rendered = render_to_string(
                            "newsletters/newsletter_article.html",
                            {"article": article},
                        )
                    rendered_articles.append(article.rendered)
            if subscriber.news:
                for news in filtered_news:
                    if not hasattr(news, "rendered"):
                        news.rendered = render_to_string(
                            "newsletters/newsletter_news.html",
                            {"news": news},
                        )
                    rendered_news.append(news.rendered)
            if rendered_news or rendered_articles:
                self.render_and_send_newsletter(subscriber, rendered_articles, rendered_news)
        newsletter.save()
