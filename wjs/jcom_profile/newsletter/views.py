from urllib.parse import urlencode

from django.apps import apps
from django.contrib.auth.mixins import UserPassesTestMixin
from django.core.exceptions import ObjectDoesNotExist
from django.http import Http404, HttpResponse, HttpResponseRedirect
from django.urls import reverse
from django.utils.translation import get_language
from django.views.generic import FormView, TemplateView, UpdateView

from ..models import Recipient
from ..utils import generate_token
from ..views import logger
from . import forms
from .service import NewsletterMailerService


def newsletter(request, journal, days="120"):
    content = NewsletterMailerService().render_sample_newsletter(journal, int(days))
    return HttpResponse(content["content"])


class NewsletterParametersUpdate(UserPassesTestMixin, UpdateView):
    model = Recipient
    template_name = "wjs/newsletter/edit_newsletters_subscription.html"
    form_class = forms.NewsletterTopicForm

    def test_func(self):
        """
        Protect this view.

        If the user is anonymous, check if a token is provided; if it is not provided,
        then a Forbidden error is raised.
        If the user is not anonymous, the test passes.
        """
        if self.request.user.is_anonymous:
            token = self.request.GET.get("token")
            try:
                Recipient.objects.get(newsletter_token=token)
                return True
            except Recipient.DoesNotExist:
                return False
        return True

    def get_object(self, queryset=None):  # noqa
        user, journal = self.request.user, self.request.journal
        if user.is_anonymous:
            recipient = Recipient.objects.get(newsletter_token=self.request.GET.get("token"))
            if (not recipient.topics.exists()) and (recipient.news is False):
                recipient.topics.set(recipient.journal.keywords.all())
                recipient.news = True
                recipient.save()
        else:
            recipient, created = Recipient.objects.get_or_create(user=user, journal=journal)
            if created:
                recipient.language = get_language()
                recipient.topics.set(recipient.journal.keywords.all())
                recipient.news = True
                recipient.save()

        return recipient

    def get_success_url(self):  # noqa
        user = self.request.user
        url = reverse("edit_newsletters")
        url = f"{url}?update=1"
        if user.is_anonymous:
            url = f"{url}&{urlencode({'token': self.object.newsletter_token})}"
        return url


class AnonymousUserNewsletterRegistration(FormView):
    template_name = "wjs/newsletter/anonymous_user_register_newsletter.html"
    form_class = forms.RegisterUserNewsletterForm

    def form_valid(self, request, *args, **kwargs):  # noqa
        user = self.request.user
        context = self.get_context_data()
        form = context.get("form")
        email = form.data["email"]
        journal = self.request.journal
        token = generate_token(email, journal.code)
        if not user.is_anonymous:
            # User is logged in, get or create the Recipient based on user and journal
            recipient, created = Recipient.objects.get_or_create(user=user, journal=journal)
            recipient.language = get_language()
            if created:
                recipient.topics.set(recipient.journal.keywords.all())
                recipient.news = True
            recipient.save()
        else:
            # User is anonymous
            recipient, created = Recipient.objects.get_or_create(
                journal=journal,
                email=email,
                defaults={
                    "newsletter_token": token,
                },
            )
            recipient.language = get_language()
            recipient.save()
            if created:
                prefix = "publication_alert_subscription"
            else:
                prefix = "publication_alert_reminder"
                # It is possible that an anonymous user registers, but
                # never clicks on the activation link, then
                # re-registers. In this case the `Recipient` object
                # already exists, but it's empty (no topics, no
                # news). We prefer to treat this case as a new
                # registration.
                if recipient.topics.count() == 0 and recipient.news is False:
                    prefix = "publication_alert_subscription"

            NewsletterMailerService().send_subscription_confirmation(
                recipient,
                prefix=prefix,
            )

        self.object = recipient
        return super().form_valid(form)

    def get_success_url(self):  # noqa
        if self.object and self.object.user:
            # The user was logged in, redirect to edit_newsletters
            return reverse("edit_newsletters")
        else:
            return reverse("register_newsletters_email_sent")

    def get_context_data(self, **kwargs):
        """Add to context the title and description as configured in the wjs_subscribe_newsletter plugin."""
        context = super().get_context_data(**kwargs)
        try:
            model = apps.get_model("wjs_subscribe_newsletter.PluginConfig")
            plugin_config = model.objects.get(journal=self.request.journal)
        except LookupError:
            logger.info("wjs_subscribe_newsletter plugin not installed. Please consider installing.")
        except ObjectDoesNotExist:
            logger.info("wjs_subscribe_newsletter plugin not configured. Please complete configuration.")
        else:
            context["wjs_subscribe_newsletter"] = plugin_config

        return context


class AnonymousUserNewsletterConfirmationEmailSent(TemplateView):
    template_name = "wjs/newsletter/anonymous_subscription_email_sent.html"


class UnsubscribeUserConfirmation(TemplateView):
    template_name = "wjs/newsletter/delete_subscription.html"


def unsubscribe_newsletter(request, token):
    """Unsubscribe from newsletter.

    Recipient objects are deleted both for anonymous and registered
    users so that the "fill-all-if-first-time" logic can apply.
    """
    user = request.user
    try:
        if user.is_anonymous:
            recipient = Recipient.objects.get(newsletter_token=token)
        else:
            recipient = Recipient.objects.get(user=request.user, journal=request.journal)
        recipient.delete()
    except Recipient.DoesNotExist:
        return Http404
    return HttpResponseRedirect(reverse("unsubscribe_newsletter_confirm"))
