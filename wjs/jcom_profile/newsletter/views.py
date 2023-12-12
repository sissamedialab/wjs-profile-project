from django.http import HttpResponse

from .service import NewsletterMailerService


def newsletter(request, journal, days="120"):
    content = NewsletterMailerService().render_sample_newsletter(journal, int(days))
    return HttpResponse(content["content"])
