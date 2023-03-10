from django.http import HttpResponse

from .service import SendNewsletter


def newsletter(request, journal):
    content = SendNewsletter().render_sample(journal)
    return HttpResponse(content["content"])
