import json
from typing import List, Tuple

from django.conf import settings
from journal.models import Journal


def get_journal_language_choices(journal: Journal) -> List[Tuple[str, str]]:
    """
    Get the language choices for a journal.

    See https://gitlab.sissamedialab.it/wjs/wjs-profile-project/-/merge_requests/144

    :param journal: the journal
    :type journal: Journal

    :return: the language choices
    :rtype: List[Tuple[str, str]]
    """
    available_languages = journal.get_setting(
        group_name="general",
        setting_name="journal_languages",
    )
    # TODO: This is needed because the value we get from the database might be a string or a list
    #  This is a bug in Janeway, which has been fixed in the latest version, but we must make sure every database
    #  is updated to store the json object instead of a string before removing this code.
    if isinstance(available_languages, str):
        available_languages = json.loads(available_languages)
    return [lang for lang in settings.LANGUAGES if lang[0] in available_languages]


def get_article_language_choices(journal: Journal) -> List[Tuple[str, str]]:
    """
    Get the language choices for a journal.

    See https://gitlab.sissamedialab.it/wjs/wjs-profile-project/-/merge_requests/144

    :param journal: the journal
    :type journal: Journal

    :return: the language choices
    :rtype: List[Tuple[str, str]]
    """
    return settings.WJS_ARTICLE_LANGUAGES.get(journal.code, settings.WJS_ARTICLE_LANGUAGES.get(None))
