from pathlib import Path
from django.apps import apps

from django.db.models import Q
from django.utils.timezone import now

from core.models import HomepageElement
from django.contrib.contenttypes.models import ContentType
from journal.models import Journal
from submission.models import Article
from utils import plugins

PLUGIN_NAME = "WJS Latest articles"
DISPLAY_NAME = "WJS Latest articles"
DESCRIPTION = "A plugin to provide latest articles home page element"
AUTHOR = "Nephila"
VERSION = "0.1"
SHORT_NAME = str(Path(__file__).parent.name)
JANEWAY_VERSION = "1.4.3"
MANAGER_URL = f"{SHORT_NAME}_manager"


class WJSLatestArticles(plugins.Plugin):
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
    def create_home_page_elements():
        journal = Journal.objects.first()
        content_type = ContentType.objects.get_for_model(journal)
        return HomepageElement.objects.get_or_create(
            name=PLUGIN_NAME,
            defaults=dict(
                template_path="homepage_elements/items_list.html",
                content_type=content_type,
                object_id=journal.pk,
                has_config=True,
                configure_url=MANAGER_URL,
            ),
        )[0]


def install():
    """Register the plugin instance and create the corresponding HomepageElement."""
    WJSLatestArticles.install()
    WJSLatestArticles.create_home_page_elements()


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
    PluginConfig = apps.get_model("wjs_latest_articles.PluginConfig")

    # Janeway provides the list of all the home page elements, which is rather weird
    # we only need the first one as we only take the generic foreign key objects, which is the same for all
    # home page elements
    try:
        base_element = homepage_elements[0]
        journal = base_element.object
    except IndexError:
        journal = None

    configuration = PluginConfig.objects.filter(journal=journal).first()

    # filter articles by
    # - date_published in the past
    articles_filter = Q(date_published__lte=now())

    # - current journal if defined
    if journal:
        articles_filter &= Q(journal=journal)

    articles = Article.objects.filter(articles_filter)

    return {
        f"{SHORT_NAME}_list": articles[: configuration.count if configuration else 10],
        f"{SHORT_NAME}_title": configuration.title if configuration else "News",
    }
