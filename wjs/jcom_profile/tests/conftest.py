"""pytest common stuff and fixtures."""
import os
import random
from unittest.mock import Mock

import pytest
import pytest_factoryboy
from core.middleware import GlobalRequestMiddleware
from core.models import Account, File, Role, Setting, SupplementaryFile
from django.conf import settings as django_settings
from django.core import management
from django.core.cache import cache
from django.urls.base import clear_script_prefix
from django.utils import timezone, translation
from identifiers.models import Identifier
from journal import models as journal_models
from journal.models import Issue, IssueType
from press.models import Press
from submission import models as submission_models
from submission.models import Keyword
from utils import setting_handler
from utils.install import (
    update_emails,
    update_issue_types,
    update_settings,
    update_xsl_files,
)
from utils.management.commands.install_janeway import ROLES_RELATIVE_PATH
from utils.management.commands.test_fire_event import create_fake_request
from utils.testing.helpers import create_galley

from wjs.jcom_profile.custom_settings_utils import (
    add_coauthors_submission_email_settings,
    add_generic_analytics_code_setting,
    add_publication_alert_settings,
    add_submission_figures_data_title,
    add_user_as_main_author_setting,
)
from wjs.jcom_profile.factories import (
    AccountFactory,
    ArticleFactory,
    IssueFactory,
    JCOMProfileFactory,
    KeywordFactory,
    NewsItemFactory,
    NewsletterFactory,
    RecipientFactory,
    SectionFactory,
    SpecialIssueFactory,
    UserFactory,
)
from wjs.jcom_profile.models import (
    ArticleWrapper,
    EditorAssignmentParameters,
    JCOMProfile,
    SpecialIssue,
)
from wjs.jcom_profile.utils import generate_token

USERNAME = "user"
JOURNAL_CODE = "JCOM"
yesterday = timezone.now() - timezone.timedelta(1)

EXTRAFIELDS_FRAGMENTS = [
    # Profession - a <select>
    '<select name="profession" class="validate" required id="id_profession">',
    '<label class="input-field-label" for="id_profession" data-error="" data-success="" id="label_profession">',
    # GDPR - a checkbox
    # NB: this <input> has slightly different layouts in the profile form and in the
    # registration form:
    # - <input type="checkbox" name="gdpr_checkbox" required id="id_gdpr_checkbox" />
    # - <input type="checkbox" name="gdpr_checkbox" id="id_gdpr_checkbox" checked />
    # TODO: be a man and use selenium!
    '<input type="checkbox" id="id_gdpr_checkbox" name="gdpr_checkbox"',
    '<label class="input-field-label" for="id_gdpr_checkbox">',
]

INVITE_BUTTON = f"""<li>
        <a href="/{JOURNAL_CODE}/admin/core/account/invite/" class="btn btn-high btn-success">Invite</a>
    </li>"""

ASSIGNMENT_PARAMETERS_SPAN = """<span class="card-title">Edit assignment parameters</span>"""  # noqa

ASSIGNMENT_PARAMS = """<span class="card-title">Edit assignment parameters</span>"""


@pytest.fixture
def sync_translation_fields(db):
    """Sync DB with translations settings.

    See
    https://django-modeltranslation.readthedocs.io/en/latest/registration.html#committing-fields-to-database
    """
    management.call_command("sync_translation_fields", "--noinput")
    management.call_command("update_translation_fields")


@pytest.fixture(autouse=True)
def clear_cache():
    """Clear cache after any test to avoid flip-flapping test results due Janeway journal/press domain."""
    yield
    cache.clear()


@pytest.fixture
def mock_premailer_load_url(mocker):
    """Provide a empty response for css when fetched by premailer."""
    mock = mocker.patch("premailer.premailer.Premailer._load_external_url", return_value="")
    return mock


@pytest.fixture
def fake_request(journal):
    """Create a fake request suitable for rendering templates."""
    # - cron/management/commands/send_publication_notifications.py
    fake_request = create_fake_request(user=None, journal=journal)
    # Workaround for possible override in DEBUG mode
    # (please read utils.template_override_middleware:60)
    fake_request.GET.get = Mock(return_value=False)
    GlobalRequestMiddleware.process_request(fake_request)
    return fake_request


@pytest.fixture
def user():
    """Create / reset a user in the DB.

    Create both core.models.Account and wjs.jcom_profile.models.JCOMProfile.
    """
    # Delete the test user (just in case...).
    user = Account(username=USERNAME, first_name="User", last_name="Ics", institution="Sissa", department="Media")
    user.save()
    yield user


@pytest.fixture
def jcom_user(user):
    """
    Create standard jcom user
    """
    jcom_user = JCOMProfile.objects.get(janeway_account=user)
    jcom_user.gdpr_checkbox = True
    jcom_user.is_active = True
    jcom_user.save()
    return jcom_user


@pytest.fixture
def roles():
    roles_path = os.path.join(django_settings.BASE_DIR, ROLES_RELATIVE_PATH)
    management.call_command("loaddata", roles_path)


@pytest.fixture
def custom_newsletter_setting(journal):
    """
    Load custom newsletter settings.

    Depends on journal fixture to ensure settings are loaded.
    """
    add_publication_alert_settings()


@pytest.fixture
def generic_analytics_code_setting(journal):
    """
    Load analytics settings.

    Depends on journal fixture to ensure settings are loaded.
    """
    add_generic_analytics_code_setting()


@pytest.fixture
def admin():
    """Create admin user."""
    return JCOMProfile.objects.create(
        username="admin",
        email="admin@admin.it",
        first_name="Admin",
        last_name="Admin",
        is_active=True,
        is_staff=True,
        is_admin=True,
        is_superuser=True,
        gdpr_checkbox=True,
    )


@pytest.fixture
def coauthor():
    """Create coauthor user."""
    return JCOMProfile.objects.create(
        username="coauthor",
        email="coauthor@coauthor.it",
        first_name="Coauthor",
        last_name="Coauthor",
        is_active=True,
        gdpr_checkbox=True,
    )


@pytest.fixture()
def editor(jcom_user, roles, journal, keywords):
    jcom_user.add_account_role("editor", journal)
    return jcom_user


@pytest.fixture()
def director(jcom_user, roles, journal, director_role):
    jcom_user.add_account_role("editor", journal)
    jcom_user.add_account_role("director", journal)
    return jcom_user


@pytest.fixture()
def invited_user(journal):
    """Create an user invited by staff, with minimal data."""
    email = "invited_user@mail.it"
    return JCOMProfile.objects.create(
        first_name="Invited",
        last_name="User",
        email=email,
        department="Dep",
        institution="1",
        is_active=False,
        gdpr_checkbox=False,
        invitation_token=generate_token(email, journal.code),
    )


@pytest.fixture
def press(install_jcom_theme):
    """Prepare a press."""
    # Copied from journal.tests.test_models
    apress = Press.objects.create(domain="testserver", is_secure=False, name="Medialab")
    apress.theme = "JCOM-theme"
    apress.save()
    yield apress


def set_jcom_theme(journal):
    """Set the journal's theme to JCOM-theme."""
    theme = "JCOM-theme"
    theme_setting = Setting.objects.get(name="journal_theme")
    setting_handler.save_setting(theme_setting.group.name, theme_setting.name, journal, theme)
    base_theme = "material"
    base_theme_setting = Setting.objects.get(name="journal_base_theme")
    setting_handler.save_setting(base_theme_setting.group.name, base_theme_setting.name, journal, base_theme)


def set_jcom_settings(journal):
    setting_handler.save_setting("general", "from_address", journal, "jcom-eo@jcom.sissa.it")
    # Languages must be enabled per journal because it's required by journal.middleware.LanguageMiddleware
    setting_handler.save_setting("general", "journal_languages", journal, '["en","es", "pt"]')
    setting_handler.save_setting("general", "privacy_policy_url", journal, "/page-privacy")
    for lang in ["en", "es", "pt"]:
        with translation.override(lang):
            for kind in ["email", "subscription_email", "reminder_email"]:
                setting_handler.save_setting(
                    "email",
                    f"publication_alert_{kind}_subject",
                    journal,
                    f"{lang} publication alert {kind.replace('_', ' ')} subject",
                )


def set_general_settings():
    """Define default settings to replace data defined in datamigration."""
    # general settings must be defined before journal creation
    if not journal_models.Journal.objects.all().exists():
        update_xsl_files()
        update_settings()
        update_emails()
        add_publication_alert_settings()
        add_user_as_main_author_setting()
        add_submission_figures_data_title()


def _journal_factory(code, press, domain=None):
    """Create a journal initializing its settings."""
    domain = domain or f"{code}.testserver.org"
    set_general_settings()
    journal = journal_models.Journal.objects.create(code=code, domain=domain)
    journal.title = f"Journal {code}: A journal of tests"
    journal.save()
    update_issue_types(journal)
    set_jcom_theme(journal)
    set_jcom_settings(journal)
    return journal


@pytest.fixture
def journal(press):
    """Prepare a journal."""
    return _journal_factory(JOURNAL_CODE, press, domain="testserver.org")


@pytest.fixture
def journal_factory(press):
    """Provide a factory to create a journal."""

    def create_journal(code):
        return _journal_factory(code, press)

    return create_journal


@pytest.fixture
def sections(journal):
    with translation.override("en"):
        for i in range(3):
            submission_models.Section.objects.create(
                journal=journal,
                name=f"section{i}",
                public_submissions=False,
            )
    return submission_models.Section.objects.all()


@pytest.fixture
def article(admin, coauthor, journal, sections):
    article = submission_models.Article.objects.create(
        abstract="Abstract",
        journal=journal,
        journal_id=journal.id,
        title="Title",
        correspondence_author=admin,
        owner=admin,
        date_submitted=None,
        section=random.choice(sections),
    )
    article.authors.add(admin, coauthor)
    return article


def _create_published_articles(admin, editor, journal, sections, keywords, items=10):
    """Create articles in published stage - Function version.

    Correspondence author (owner), keywords and section are random"""
    for i in range(items):
        owner = random.choice([admin, editor])
        article = submission_models.Article.objects.create(
            abstract=f"Abstract{i}",
            journal=journal,
            journal_id=journal.id,
            title=f"Title{i}",
            correspondence_author=owner,
            owner=owner,
            date_submitted=timezone.now(),
            date_accepted=timezone.now(),
            date_published=timezone.now(),
            section=random.choice(sections),
            stage="Published",
            language="eng",
        )
        article.authors.add(owner)
        article.keywords.add(random.choice(keywords))
        article.snapshot_authors()
        Identifier.objects.create(id_type="pubid", article=article, identifier=f"JCOM_0101_2022_R0{article.pk}")
        for file_ext in ["_es.pdf", "_en.pdf", ".epub"]:
            file_obj = File.objects.create(original_filename=f"JCOM_0101_2022_R0{article.pk}{file_ext}")
            galley = create_galley(article, file_obj)
            galley.article = article
            galley.last_modified = timezone.now()
            galley.save()
    return submission_models.Article.objects.all()


@pytest.fixture
def published_articles(admin, editor, journal, sections, keywords):
    """Create articles in published stage - Fixture version.

    Correspondence author (owner), keywords and section are random"""
    return _create_published_articles(admin, editor, journal, sections, keywords)


@pytest.fixture
def published_article_with_standard_galleys(journal, article_factory):
    """Create articles in published stage with PDF and EPUB galleys."""
    article = article_factory(
        journal=journal,
        date_published=timezone.now(),
        stage="Published",
    )
    pubid = "JCOM_0102_2023_A04"
    Identifier.objects.create(
        id_type="pubid",
        article=article,
        identifier=pubid,
    )
    for extension in ["pdf", "epub"]:
        for language in ["en", "pt", ""]:
            file_obj = File.objects.create(
                # TODO: we could use the original_filename to match the
                # requested galley, but first we must verify if the
                # original file name always appears in the link (check
                # simple galleys, original language vs. translations and
                # any combination of these with the manual errors _0
                # _1...) and verify if during import we collect and store
                # the original file name in the galley.
                original_filename=f"Anything.{extension}",
            )
            galley = create_galley(article, file_obj)
            galley.type = extension  # "pdf" and "epub" are in core.models.galley_type_choices()
            galley.article = article
            galley.last_modified = timezone.now()
            galley.label = extension.upper()
            if language:
                galley.label += f" ({language})"  # The label is used to find the correct galley
            galley.save()
    # Add some supplementary material (aka attachment): a pdf file
    # with a conventional label. E.g.
    # https://jcom.sissa.it/archive/21/06/JCOM_2106_2022_Y01 has
    # "JCOM_0102_2023_A04_ATTACH_1.pdf"
    convetional_label = f"{pubid}_ATTACH_1.pdf"
    file_obj = File.objects.create(
        original_filename="Supplementary.pdf",
        is_galley=False,
        label=convetional_label,
    )
    supplementary_obj = SupplementaryFile.objects.create(file=file_obj)
    article.supplementary_files.add(supplementary_obj)
    return article


@pytest.fixture
def director_role(roles):
    """Create Director Role."""
    Role.objects.get_or_create(name="Director", slug="director")


@pytest.fixture
def coauthors_setting(journal):
    """
    Install custom settings for coauthors email to send email to coauthors on submission.

    Depends on journal fixture to ensure settings are loaded.
    """
    add_coauthors_submission_email_settings()


@pytest.fixture
def user_as_main_author_setting(journal):
    """Add setting to set current user as main author.

    Depends on journal fixture to ensure settings are loaded.
    """
    add_user_as_main_author_setting()


@pytest.fixture
def install_jcom_theme():
    """JCOM-theme must be installed in J. code base for its templates to be found."""
    management.call_command("install_themes")


# FIXME: We should mark this as autouse and fix the tests relying on the dirty script prefix
# We (unconsciously) rely on this in a few tests, and we should not do this anymore.
@pytest.fixture
def clear_script_prefix_fix():
    """Clear django's script prefix at the end of the test.

    Many tests rely on implicit journal prefix injection done by `core.middleware.SiteSettingsMiddleware`
    which is currently propagated to any test

    ```python
    Set the script prefix if the site is in path mode
    if site_path:
        prefix = "/" + site_path
        logger.debug("Setting script prefix to %s" % prefix)
        set_script_prefix(prefix)
        request.path_info = request.path_info[len(prefix):]
    ```
    This code use django `set_script_prefix` to automatically set the journal code as prefix for all the URLs in the
    current thread. At runtime this is not a problem because the middleware is run on every request and the prefix is
    then set on every run for the current journal.

    As the tests are run by "reusing" the threads (either a single thread when not in parallel mode or a limited set of
    threads in parallel mode) the prefix leaks from one test to another.

    This fixture clears the script prefix before and after the test.
    """
    clear_script_prefix()
    yield None
    clear_script_prefix()


@pytest.fixture
def keywords():
    for i in range(10):
        Keyword.objects.create(word=f"{i}-keyword")
    return Keyword.objects.all()


@pytest.fixture
def directors(director_role, journal):
    directors = []
    for i in range(3):
        director = Account.objects.create(
            username=f"Director{i}",
            email=f"Director{i}@Director{i}.it",
            first_name=f"Director{i}",
            last_name=f"Director{i}",
            is_active=True,
        )
        EditorAssignmentParameters.objects.create(
            editor=director,
            journal=journal,
            workload=random.randint(1, 10),
        )
        director.add_account_role("director", journal)
        directors.append(director)
    return directors


@pytest.fixture
def editors(roles, journal):
    editors = []
    for i in range(3):
        editor = Account.objects.create(
            username=f"Editor{i}",
            email=f"Editor{i}@Editor{i}.it",
            first_name=f"Editor{i}",
            last_name=f"Editor{i}",
            is_active=True,
        )
        editor.add_account_role("editor", journal)
        editor.save()

        EditorAssignmentParameters.objects.create(
            editor=editor,
            journal=journal,
            workload=random.randint(1, 10),
        )

        editors.append(editor)
    return editors


@pytest.fixture
def special_issue(article, editors, journal, director_role):
    special_issue = SpecialIssue.objects.create(
        name="Special issue",
        short_name="special-issue",
        journal=journal,
        open_date=timezone.now(),
        close_date=timezone.now() + timezone.timedelta(1),
    )
    for editor in editors:
        special_issue.editors.add(editor)
        special_issue.save()
    article_wrapper = ArticleWrapper.objects.get(janeway_article=article)
    article_wrapper.special_issue = special_issue
    article_wrapper.save()

    return special_issue


@pytest.fixture
def issue_type(journal):
    return IssueType.objects.create(journal=journal, code="1", pretty_name="Issue type")


@pytest.fixture
def issue(issue_type, published_articles):
    issue = Issue.objects.create(
        journal=issue_type.journal,
        date=timezone.now(),
        issue="01",
        issue_title=f"Issue 01, {timezone.now().year}",
        issue_type=issue_type,
    )
    issue.articles.add(*published_articles)
    return issue


# Name the fixture a bit differently. This code, without the second
# option, would produce a "article_factory" fixture (i.e. a factory of
# article objects) and a fixture named "article" (i.e. one article
# object) that would clash with the one defined above.
pytest_factoryboy.register(ArticleFactory, "fb_article")
# Make a fixture that returns a user "already existing" in the DB
pytest_factoryboy.register(
    UserFactory,
    "existing_user",
    first_name="Iam",
    last_name="Sum",
    email="iamsum@example.com",
    institution="ML",
)
pytest_factoryboy.register(JCOMProfileFactory)
pytest_factoryboy.register(AccountFactory)
pytest_factoryboy.register(SpecialIssueFactory, "fb_special_issue")
pytest_factoryboy.register(
    SpecialIssueFactory,
    "open_special_issue",
    open_date=yesterday,
    close_date=None,
)
pytest_factoryboy.register(IssueFactory, "fb_issue")
pytest_factoryboy.register(SectionFactory)
pytest_factoryboy.register(KeywordFactory)
pytest_factoryboy.register(RecipientFactory)
pytest_factoryboy.register(NewsItemFactory)
pytest_factoryboy.register(NewsletterFactory)
