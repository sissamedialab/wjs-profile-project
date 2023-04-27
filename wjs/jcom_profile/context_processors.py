from django.conf import settings
from django.utils import translation


def date_format(request):
    """
    This context processor injects the date formatting strings according to the language.
    domain.

    :param request: the active request
    :return: dictionary containing DATE_FORMAT / DATETIME_FORMAT
    """
    language = translation.get_language()
    return {
        "DATE_FORMAT": settings.DATE_FORMATS.get(language, settings.DATE_FORMAT),
        "DATETIME_FORMAT": settings.DATETIME_FORMATS.get(language, settings.DATETIME_FORMAT),
    }
