from django.http import HttpResponse

from .service import NewsletterMailerService


def newsletter(request, journal):
    content = NewsletterMailerService().render_sample_newsletter(journal)
    return HttpResponse(content["content"])
