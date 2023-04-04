from pathlib import Path
from django.apps import apps
from django.db.models import Q
from django.utils.timezone import now

from comms.models import NewsItem
from core.models import HomepageElement
from django.contrib.contenttypes.models import ContentType
from journal.models import Journal
from utils import plugins

PLUGIN_NAME = "WJS Latest news"
DISPLAY_NAME = "WJS Latest news"
DESCRIPTION = "A plugin to provide latest news home page element"
AUTHOR = "Nephila"
VERSION = "0.1"
SHORT_NAME = str(Path(__file__).parent.name)
JANEWAY_VERSION = "1.4.3"
MANAGER_URL = f"{SHORT_NAME}_manager"


class WJSLatestNews(plugins.Plugin):
    short_name = SHORT_NAME
    plugin_name = PLUGIN_NAME
    display_name = DISPLAY_NAME
    description = DESCRIPTION
    author = AUTHOR
    version = VERSION
    janeway_version = JANEWAY_VERSION
    is_workflow_plugin = False
    manager_url = MANAGER_URL

    @staticmethod
    def create_home_page_elements(journal):
        content_type = ContentType.objects.get_for_model(journal)
        return HomepageElement.objects.get_or_create(
            name=PLUGIN_NAME,
            content_type=content_type,
            object_id=journal.pk,
            defaults=dict(
                template_path="homepage_elements/wjs_news_list.html",
                has_config=True,
                configure_url=MANAGER_URL,
            ),
        )[0]


def install():
    """Register the plugin instance and create the corresponding HomepageElement."""
    WJSLatestNews.install()
    journals = Journal.objects.all()
    for journal in journals:
        WJSLatestNews.create_home_page_elements(journal)


def hook_registry():
    """
    Register hooks for current plugin.

    Currently supported hooks:
    - yield_homepage_element_context
    """
    return {
        "yield_homepage_element_context": {
            "module": f"plugins.{SHORT_NAME}.plugin_settings",
            "function": "get_plugin_context",
            "name": PLUGIN_NAME,
        },
    }


def get_plugin_context(request, homepage_elements):
    # using apps.get_model because if we import the model directly its path won't match the one at runtime because
    # the plugins are imported from plugins package by janeway
    PluginConfig = apps.get_model("wjs_latest_news.PluginConfig")

    # Janeway provides the list of all the home page elements, which is rather weird
    # we only need the first one as we only take the generic foreign key objects, which is the same for all
    # home page elements
    try:
        base_element = homepage_elements[0]
        journal = base_element.object
        content_type = base_element.content_type
    except IndexError:
        journal = None
        content_type = None

    configuration = PluginConfig.objects.filter(journal=journal).first()

    # filter news by
    # - start display in the past
    # - no end display or end display in the future
    news_filter = Q(Q(start_display__lte=now()) & (Q(end_display__gte=now()) | Q(end_display__isnull=True)))

    # - current journal if defined
    if content_type and journal.pk:
        news_filter &= Q(content_type=content_type) & Q(object_id=journal.pk)

    news = NewsItem.objects.filter(news_filter).order_by("sequence", "-start_display")

    return {
        f"{SHORT_NAME}_list": news[: configuration.count if configuration else 10],
        f"{SHORT_NAME}_title": configuration.title if configuration else "News",
    }
