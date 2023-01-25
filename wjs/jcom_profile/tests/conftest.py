"""pytest common stuff and fixtures."""
import os
import random

import pytest
import pytest_factoryboy
from core.models import Account, File, Role, Setting
from django.conf import settings
from django.core import management
from django.urls.base import clear_script_prefix
from django.utils import timezone, translation
from identifiers.models import Identifier
from journal import models as journal_models
from journal.models import Issue, IssueType
from press.models import Press
from submission import models as submission_models
from submission.models import Keyword
from utils import setting_handler
from utils.install import update_issue_types
from utils.management.commands.install_janeway import ROLES_RELATIVE_PATH
from utils.testing.helpers import create_galley

from wjs.jcom_profile.factories import ArticleFactory, SpecialIssueFactory, UserFactory
from wjs.jcom_profile.models import (
    ArticleWrapper,
    EditorAssignmentParameters,
    JCOMProfile,
    SpecialIssue,
)
from wjs.jcom_profile.utils import generate_token

USERNAME = "user"
JOURNAL_CODE = "TST"

EXTRAFIELDS_FRAGMENTS = [
    # Profession - a <select>
    '<select name="profession" class="form-control" title="" required id="id_profession">',
    '<label class="form-control-label" for="id_profession">Profession</label>',
    # GDPR - a checkbox
    # NB: this <input> has slightly different layouts in the profile form and in the
    # registration form:
    # - <input type="checkbox" name="gdpr_checkbox" required id="id_gdpr_checkbox" />
    # - <input type="checkbox" name="gdpr_checkbox" id="id_gdpr_checkbox" checked />
    # TODO: be a man and use selenium!
    '<input type="checkbox" name="gdpr_checkbox"',
    'id="id_gdpr_checkbox"',
]

INVITE_BUTTON = f"""<li>
        <a href="/{JOURNAL_CODE}/admin/core/account/invite/" class="btn btn-high btn-success">Invite</a>
    </li>"""

ASSIGNMENT_PARAMETERS_SPAN = """<span class="card-title">Edit assignment parameters</span>"""  # noqa

ASSIGNMENT_PARAMS = """<span class="card-title">Edit assignment parameters</span>"""


@pytest.fixture
def user():
    """Create / reset a user in the DB.

    Create both core.models.Account and wjs.jcom_profile.models.JCOMProfile.
    """
    # Delete the test user (just in case...).
    user = Account(username=USERNAME, first_name="User", last_name="Ics")
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
    roles_path = os.path.join(settings.BASE_DIR, ROLES_RELATIVE_PATH)
    management.call_command("loaddata", roles_path)


@pytest.fixture
def custom_newsletter_setting():
    management.call_command("add_custom_subscribe_email_message_settings")


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
def invited_user():
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
        invitation_token=generate_token(email),
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


@pytest.fixture
def journal(press):
    """Prepare a journal."""
    journal = journal_models.Journal.objects.create(code=JOURNAL_CODE, domain="testserver.org")
    journal.title = "Test Journal: A journal of tests"
    journal.save()
    update_issue_types(journal)
    set_jcom_theme(journal)

    return journal


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


@pytest.fixture
def published_articles(admin, editor, journal, sections, keywords):
    """Create articles in published stage.

    Correspondence author (owner), keywords and section are random"""
    for i in range(10):
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
        )
        article.keywords.add(random.choice(keywords))
        Identifier.objects.create(id_type="pubid", article=article, identifier=f"JCOM_0101_2022_R0{article.pk}")
        for file_ext in ["_es.pdf", "_en.pdf", ".epub"]:
            file_obj = File.objects.create(original_filename=f"JCOM_0101_2022_R0{article.pk}{file_ext}")
            galley = create_galley(article, file_obj)
            galley.article = article
            galley.last_modified = timezone.now()
            galley.save()
    return submission_models.Article.objects.all()


@pytest.fixture
def director_role(roles):
    """Create Director Role."""
    Role.objects.get_or_create(name="Director", slug="director")


@pytest.fixture
def coauthors_setting():
    """Run add_coauthors_submission_email_settings command to install custom settings for coauthors email."""
    management.call_command("add_coauthors_submission_email_settings")


@pytest.fixture
def user_as_main_author_setting():
    management.call_command("add_user_as_main_author_setting")


@pytest.fixture
def install_jcom_theme():
    """JCOM-theme must be installed in J. code base for its templates to be found."""
    management.call_command("install_themes")


@pytest.fixture
def clear_script_prefix_fix():
    """Clear django's script prefix at the end of the test.

    Otherwise `reverse()` might produce unexpected results.

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
        issue="1",
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
pytest_factoryboy.register(SpecialIssueFactory, "fb_special_issue")
yesterday = timezone.now() - timezone.timedelta(1)
pytest_factoryboy.register(
    SpecialIssueFactory,
    "open_special_issue",
    open_date=yesterday,
    close_date=None,
)
