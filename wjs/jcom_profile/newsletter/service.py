"""Newsletter aka "Publication alerts" service."""
import datetime
from typing import Dict, Iterable, List, Tuple, TypedDict
from unittest.mock import Mock
from urllib.parse import urlencode

from cms.models import Page
from comms.models import NewsItem
from core.middleware import GlobalRequestMiddleware
from django.conf import settings
from django.contrib.contenttypes.models import ContentType
from django.core.exceptions import ObjectDoesNotExist
from django.core.mail import send_mail
from django.db.models import Q
from django.http import HttpRequest
from django.template.loader import render_to_string
from django.urls import reverse
from django.utils.timezone import now
from django.utils.translation import override
from journal.models import Journal
from premailer import transform
from submission.models import Article
from utils.logger import get_logger
from utils.management.commands.test_fire_event import create_fake_request
from utils.setting_handler import get_setting

from wjs.jcom_profile.context_processors import date_format
from wjs.jcom_profile.models import Newsletter, Recipient

logger = get_logger(__name__)


class NewsletterItem(TypedDict):
    subscriber: Recipient
    content: str


class NewsletterMailerService:
    def site_url(self, journal: Journal):
        """
        Get base site URL.

        Allow overriding Journal.site_url using NEWSLETTER_URL Django setting.
        """
        if getattr(settings, "NEWSLETTER_URL", None):
            return settings.NEWSLETTER_URL
        else:
            return journal.site_url()

    @property
    def send_always_timestamp(self) -> datetime.datetime:
        """Get timestamp that ensures that there will be something in the publication alert message.

        Debug purposes only.
        """
        return now() - datetime.timedelta(days=120)

    def process_content(self, content: str, journal: Journal):
        """Process the message content with premailer."""
        processed = transform(
            content,
            base_url=self.site_url(journal),
            allow_network=True,
            allow_loading_external_files=True,
            allow_insecure_ssl=getattr(settings, "NEWSLETTER_URL_INSECURE", False),
            cssutils_logging_level="CRITICAL",
        )
        return processed

    def get_unsubscribe_url(self, subscriber: Recipient):
        """Return the unsubscribe URL according to the subscriber type."""
        if subscriber.newsletter_token:
            return f'{reverse("edit_newsletters")}?token={subscriber.newsletter_token}'
        else:
            return reverse("edit_newsletters")

    def get_privacy_url(self, journal: Journal):
        """Return the privacy page URL (if exists)."""
        content_type = ContentType.objects.get_for_model(journal)
        try:
            page = Page.objects.filter(content_type=content_type, object_id=journal.pk, name="privacy").get()
            return reverse("cms_page", args=(page.name,))
        except Page.DoesNotExist:
            try:
                privacy_policy_url = get_setting(
                    "general",
                    "privacy_policy_url",
                    journal,
                    create=False,
                    default=True,
                )
                if privacy_policy_url.processed_value:
                    return privacy_policy_url.processed_value
            except ObjectDoesNotExist:
                pass
        return ""

    def _render_newsletter_message(
        self,
        journal: Journal,
        subscriber: Recipient,
        rendered_articles: List[str],
        rendered_news: List[str],
    ) -> str:
        """
        Render the newsletter for a subscriber.

        :param journal: Journal instance.
        :param subscriber: The subscriber Recipient model.
        :param rendered_articles: The articles to be rendered in newsletter email.
        :param rendered_news: The news to be rendered in newsletter emails.
        """
        intro_message = get_setting(
            "email",
            "publication_alert_email_intro_message",
            subscriber.journal,
            create=False,
            default=True,
        )

        content = render_to_string(
            "newsletters/newsletter_issue.html",
            {
                "subscriber": subscriber.user,
                "articles": "".join(rendered_articles),
                "news": "".join(rendered_news),
                "intro_message": intro_message,
                **self.get_context_data(subscriber),
            },
        )

        return self.process_content(content, subscriber.journal)

    def _get_newsletter(self, journal: Journal, force: bool = False) -> Tuple[Newsletter, datetime.datetime]:
        newsletter, created = Newsletter.objects.get_or_create(journal=journal)
        if created:
            newsletter.last_sent = now()
            newsletter.save()
        last_sent = newsletter.last_sent
        if force:
            last_sent = self.send_always_timestamp
        return newsletter, last_sent

    def _get_request(self, journal: Journal) -> HttpRequest:
        """Create fake request.

        Add the current journal to the request and populate the local thread to use utils.logic.get_current_request.
        """
        # - cron/management/commands/send_publication_notifications.py
        fake_request = create_fake_request(user=None, journal=journal)
        # Workaround for possible override in DEBUG mode
        # (please read utils.template_override_middleware:60)
        fake_request.GET.get = Mock(return_value=False)
        GlobalRequestMiddleware.process_request(fake_request)
        return fake_request

    def _get_objects(
        self,
        journal: Journal,
        last_sent: datetime.datetime,
    ) -> Tuple[Iterable[Recipient], Iterable[Article], Iterable[NewsItem]]:
        content_type = ContentType.objects.get_for_model(journal)

        filtered_articles = Article.objects.filter(date_published__date__gt=last_sent, journal=journal)
        filtered_news = NewsItem.objects.filter(
            start_display__gt=last_sent,
            start_display__lte=now(),
            content_type=content_type,
            object_id=journal.pk,
        )
        # Explicitly filter Recipient objects by Journal
        journal_subscribers = Recipient.objects.filter(journal=journal)
        subscribers_filter = Q(topics__in=filtered_articles.values_list("keywords"))
        if filtered_news.exists():
            subscribers_filter |= Q(news=True)
        filtered_subscribers = journal_subscribers.filter(subscribers_filter).distinct()

        logger.debug(
            f"Newsletter: last sent: {last_sent} (now is {now()});"
            f" found (filtered) {filtered_subscribers.count()} subscribers,"
            f" {filtered_articles.count()} articles,"
            f" {filtered_news.count()} news.",
        )
        return filtered_subscribers, filtered_articles, filtered_news

    def _render_articles(self, subscriber: Recipient, articles: Iterable[Article], request: HttpRequest) -> List[str]:
        """Create the list of rendered articles."""
        rendered_articles = []

        for article in articles:
            if article.keywords.intersection(subscriber.topics.all()):
                if not hasattr(article, "rendered"):
                    article.rendered = render_to_string(
                        "newsletters/newsletter_article.html",
                        {"article": article, "request": request, **date_format(request)},
                    )
                rendered_articles.append(article.rendered)
        return rendered_articles

    def _render_news(
        self,
        subscriber: Recipient,
        filtered_news: Iterable[NewsItem],
        request: HttpRequest,
    ) -> List[str]:
        """Create the list of rendered news."""
        rendered_news = []

        if subscriber.news:
            for news in filtered_news:
                if not hasattr(news, "rendered"):
                    news.rendered = render_to_string(
                        "newsletters/newsletter_news.html",
                        {"news": news, "request": request, **date_format(request)},
                    )
                rendered_news.append(news.rendered)
        return rendered_news

    def _render_newsletters_batch(self, journal_code: str, last_sent: datetime.datetime) -> NewsletterItem:
        """Return a generator that yields the rendered content of the newsletter for each subscriber."""
        journal = Journal.objects.get(code=journal_code)
        request = self._get_request(journal)

        filtered_subscribers, filtered_articles, filtered_news = self._get_objects(journal, last_sent)

        for subscriber in filtered_subscribers:
            # https://docs.djangoproject.com/en/1.11/ref/utils/#django.utils.translation.override
            with override(subscriber.language):
                rendered_articles = self._render_articles(subscriber, filtered_articles, request)
                rendered_news = self._render_news(subscriber, filtered_news, request)

                if rendered_news or rendered_articles:
                    yield NewsletterItem(
                        subscriber=subscriber,
                        content=self._render_newsletter_message(journal, subscriber, rendered_articles, rendered_news),
                    )

    def _send_newsletter(self, subscriber: Recipient, newsletter_content: str) -> bool:
        # https://docs.djangoproject.com/en/1.11/ref/utils/#django.utils.translation.override
        with override(subscriber.language):
            subject = get_setting(
                "email",
                "publication_alert_email_subject",
                subscriber.journal,
                create=False,
                default=True,
            )
            from_email = get_setting(
                "general",
                "from_address",
                subscriber.journal,
                create=False,
                default=True,
            )

            return send_mail(
                subject.value.format(journal=subscriber.journal, date=datetime.date.today()),
                newsletter_content,
                from_email.value,
                [subscriber.newsletter_destination_email],
                fail_silently=False,
                html_message=newsletter_content,
            )

    def render_sample_newsletter(self, journal_code: str) -> str:
        """Render a sample message for one the existing subscribers for debugging."""
        messages = list(self._render_newsletters_batch(journal_code, self.send_always_timestamp))
        return messages[0]

    def send_newsletter(self, journal_code: str, force: bool = False) -> List[str]:
        """Send the publication alerts.

        Use the unique Newsletter object (creating it if non-existing) to filter articles and news to be sent
        to users based on the last time newsletters have been delivered. Each user is notified considering their
        interests (i.e. topics saved in their Recipient object).
        """
        messages = []

        journal = Journal.objects.get(code=journal_code)
        newsletter, last_sent = self._get_newsletter(journal=journal, force=force)
        for rendered in self._render_newsletters_batch(journal_code, last_sent):
            try:
                self._send_newsletter(rendered["subscriber"], rendered["content"])
            except Exception as e:
                messages.append(str(e))

        newsletter.last_sent = now()
        newsletter.save()
        return messages

    def get_context_data(self, subscriber: Recipient) -> Dict[str, any]:
        """Return context data suitable to be used in the newsletter preference pages."""
        return {
            "journal": subscriber.journal,
            "site_url": self.site_url(subscriber.journal),
            "unsubscribe_url": self.get_unsubscribe_url(subscriber),
            "privacy_url": self.get_privacy_url(subscriber.journal),
        }

    def send_subscription_confirmation(self, subscriber: Recipient, prefix: str):
        """Send a confirmation email to anonymous users.

        :param prefix: could be
        - publication_alert_subscription - for email to "first time" recipients
        - publication_alert_reminder - for "reminder" emails to existing recipients
        """
        # https://docs.djangoproject.com/en/1.11/ref/utils/#django.utils.translation.override
        with override(subscriber.language):
            subject = get_setting(
                "email",
                f"{prefix}_email_subject",
                subscriber.journal,
                create=False,
                default=True,
            )
            email_body = get_setting(
                "email",
                f"{prefix}_email_body",
                subscriber.journal,
                create=False,
                default=True,
            )
            from_email = get_setting(
                "general",
                "from_address",
                subscriber.journal,
                create=False,
                default=True,
            )

            acceptance_url = f"{reverse('edit_newsletters')}?{urlencode({'token': subscriber.newsletter_token})}"
            full_acceptance_url = f"{self.site_url(subscriber.journal).strip('/')}{acceptance_url}"

            content = render_to_string(
                "newsletters/newsletter_template.html",
                {
                    "content": email_body.value.format(
                        journal=subscriber.journal,
                        email=subscriber.newsletter_destination_email,
                        acceptance_url=full_acceptance_url,
                    ),
                    **self.get_context_data(subscriber),
                },
            )

            newsletter_content = self.process_content(content, subscriber.journal)

            send_mail(
                subject.value.format(journal=subscriber.journal, date=datetime.date.today()),
                newsletter_content,
                from_email.value,
                [subscriber.newsletter_destination_email],
                fail_silently=False,
                html_message=newsletter_content,
            )
