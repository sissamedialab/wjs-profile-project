"""pytest common stuff and fixtures."""
import pytest

from django.core.exceptions import ObjectDoesNotExist
from django.urls.base import clear_script_prefix
from django.utils import translation
from django.core import management



from core.models import Account
from wjs.jcom_profile.models import JCOMProfile
from submission.models import Article
from journal.tests.utils import make_test_journal
from press.models import Press
from submission import models as submission_models

from utils.install import update_xsl_files, update_settings, update_issue_types
from journal import models as journal_models


from wjs.jcom_profile.models import JCOMProfile
from wjs.jcom_profile.utils import generate_token

USERNAME = "user"
JOURNAL_CODE = "CODE"

PROFESSION_SELECT_FRAGMENTS_JOURNAL = [
    (
        "clean",
        (
            '<select name="profession" class="form-control" title="" required id="id_profession">',
            '<label class="form-control-label" for="id_profession">Profession</label>',
        ),
    ),
    (
        "material",
        (
            '<select name="profession" required id="id_profession">',
            "<label>Profession</label>",
        ),
    ),
    (
        "OLH",
        (
            '<select name="profession" required id="id_profession">',
            '<label for="id_profession">',
        ),
    ),
]

GDPR_FRAGMENTS_JOURNAL = [
    (
        "clean",
        (
            '<input type="checkbox" name="gdpr_checkbox" required id="id_gdpr_checkbox" />',
        ),
    ),
    (
        "material",
        (
            '<input type="checkbox" name="gdpr_checkbox" required id="id_gdpr_checkbox" />',
        ),
    ),
    (
        "OLH",
        (
            '<input type="checkbox" name="gdpr_checkbox" required id="id_gdpr_checkbox" />',
        ),
    ),
]

PROFESSION_SELECT_FRAGMENTS_PRESS = [
    (
        "clean",
        (
            '<select name="profession" class="form-control" title="" required id="id_profession">',
            '<label class="form-control-label" for="id_profession">Profession</label>',
        ),
    ),
    (
        "material",
        (
            '<select name="profession" required id="id_profession">',
            "<label>Profession</label>",
        ),
    ),
    (
        "OLH",
        (
            '<select name="profession" required id="id_profession">',
            """<label for="id_profession">
                Profession
                <span class="red">*</span>""",
        ),
    ),
]

INVITE_BUTTON = """<li>
    <a href="/admin/core/account/invite/" class="btn btn-high btn-success">Invite</a>
</li>"""


def drop_user():
    """Delete the test user."""
    try:
        userX = Account.objects.get(username=USERNAME)
    except ObjectDoesNotExist:
        pass
    else:
        userX.delete()


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
        gdpr_checkbox=True
    )


@pytest.fixture
def coauthor():
    """Create coauthor user."""
    return Account.objects.create(
        username="coauthor",
        email="coauthor@coauthor.it",
        first_name="Coauthor",
        last_name="Coauthor",
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


# Only works at module "resolution", i.e. not for the single test
# https://docs.pytest.org/en/7.1.x/reference/reference.html#globalvar-collect_ignore
# collect_ignore = [
#     'test_app.py',
# ]


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
def press():
    """Prepare a press."""
    # Copied from journal.tests.test_models
    apress = Press.objects.create(
        domain="testserver", is_secure=False, name="Medialab"
    )
    apress.save()
    yield apress
    apress.delete()


@pytest.fixture
def journal(press):
    """Prepare a journal."""
    # The  graphical theme is set by the single tests.
    journal_kwargs = dict(
        code=JOURNAL_CODE,
        domain="sitetest.org",
        # journal_theme='JCOM',  # No!
    )
    journal = make_test_journal(**journal_kwargs)
    yield journal
    # probably redundant because of django db transactions rollbacks
    journal.delete()


@pytest.fixture
def article_journal(press):
    # FIXME: Can't figure out why the journal fixtures does not work with article submission
    update_xsl_files()
    update_settings()
    journal_one = journal_models.Journal(code="TST", domain="testserver")
    journal_one.title = "Test Journal: A journal of tests"
    journal_one.save()
    update_issue_types(journal_one)

    return journal_one


@pytest.fixture
def article(admin, coauthor, article_journal):
    with translation.override("en"):
        section = submission_models.Section.objects.create(
            journal=article_journal,
            name="section",
            public_submissions=False,
        )
    article = Article.objects.create(
        abstract="Abstract",
        journal=article_journal,
        journal_id=article_journal.id,
        title="Title",
        correspondence_author=admin,
        owner=admin,
        date_submitted=None,
        section=section
    )
    article.authors.add(admin, coauthor)
    return article


@pytest.fixture
def coauthors_setting():
    management.call_command("add_coauthors_submission_email_settings")


@pytest.fixture
def clear_script_prefix_fix():
    """Clear django's script prefix at the end of the test.

    Otherwise `reverse()` might produce unexpected results.

    This fixture clears the script prefix before and after the test.
    """
    clear_script_prefix()
    yield None
    clear_script_prefix()
