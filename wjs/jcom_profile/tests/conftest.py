"""pytest common stuff and fixtures."""
import os

import pytest
from core.models import Account, Setting
from django.conf import settings
from django.core import management
from django.core.exceptions import ObjectDoesNotExist
from django.core.serializers import deserialize
from django.urls.base import clear_script_prefix
from django.utils import translation
from journal import models as journal_models
from journal.tests.utils import make_test_journal
from press.models import Press
from submission import models as submission_models
from utils import setting_handler
from utils.install import update_issue_types, update_settings, update_xsl_files

from wjs.jcom_profile.models import JCOMProfile
from wjs.jcom_profile.utils import generate_token

USERNAME = "user"
JOURNAL_CODE = "CODE"

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

INVITE_BUTTON = """<li>
        <a href="/admin/core/account/invite/" class="btn btn-high btn-success">Invite</a>
    </li>"""


def drop_user():
    """Delete the test user."""
    try:
        user_x = Account.objects.get(username=USERNAME)
    except ObjectDoesNotExist:
        pass
    else:
        user_x.delete()


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


@pytest.fixture
def user():
    """Create / reset a user in the DB.

    Create both core.models.Account and wjs.jcom_profile.models.JCOMProfile.
    """
    # Delete the test user (just in case...).
    drop_user()
    user = Account(username=USERNAME, first_name="User", last_name="Ics")
    user.save()
    yield user


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
    apress.delete()


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
    journal_kwargs = {
        "code": JOURNAL_CODE,
        "domain": "sitetest.org",
    }
    journal = make_test_journal(**journal_kwargs)
    set_jcom_theme(journal)
    yield journal
    # probably redundant because of django db transactions rollbacks
    journal.delete()


@pytest.fixture
def article_journal(press):
    """Create a journal for a test article."""
    # FIXME: Can't figure out why the journal fixtures does not work with article submission
    update_xsl_files()
    update_settings()
    journal_one = journal_models.Journal(code="TST", domain="testserver")
    journal_one.title = "Test Journal: A journal of tests"
    journal_one.save()
    update_issue_types(journal_one)
    set_jcom_theme(journal_one)

    return journal_one


@pytest.fixture
def section(article_journal):
    with translation.override("en"):
        section = submission_models.Section.objects.create(
            journal=article_journal,
            name="section",
            public_submissions=False,
        )
    return section


@pytest.fixture
def article(admin, coauthor, article_journal, section):
    article = submission_models.Article.objects.create(
        abstract="Abstract",
        journal=article_journal,
        journal_id=article_journal.id,
        title="Title",
        correspondence_author=admin,
        owner=admin,
        date_submitted=None,
        section=section,
    )
    article.authors.add(admin, coauthor)
    return article


@pytest.fixture
def coauthors_setting():
    """Run add_coauthors_submission_email_settings command to install custom settings for coauthors email."""
    management.call_command("add_coauthors_submission_email_settings")


@pytest.fixture
def user_as_main_author_setting():
    management.call_command("add_user_as_main_author_setting")


@pytest.fixture
def roles():
    roles_relative_path = "utils/install/roles.json"
    roles_path = os.path.join(settings.BASE_DIR, roles_relative_path)
    with open(roles_path, encoding="utf-8") as f:
        roles = f.read()
        for role in deserialize("json", roles):
            role.save()


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
