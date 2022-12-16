"""Test Import Many Users functionality"""

import io
from collections import namedtuple

import lxml.html
import pytest
from core.models import Account
from django.urls import reverse
from odf.opendocument import OpenDocumentSpreadsheet
from odf.table import Table, TableCell, TableRow
from odf.text import P
from submission.models import Article


def make_ods(data):
    """Return a ods file with the give data.

    Data is a list of lists (rows/cols).
    """
    # Generate document object
    doc = OpenDocumentSpreadsheet()
    table = Table()

    def newtc(value):
        tc = TableCell(valuetype="string")
        tc.addElement(P(text=value))
        return tc

    for row in data:
        tr = TableRow()
        for col in row:
            tr.addElement(newtc(col))
            table.addElement(tr)

    # save ods
    doc.spreadsheet.addElement(table)
    f = io.BytesIO()
    doc.save(f, False)
    f.seek(0)
    return f


@pytest.mark.django_db
def test_si_imu_upload_one_existing_one_new(
    article_journal,
    client,
    admin,
    existing_user,
    fb_special_issue,
):
    """Upload a sheet with one-existing / one-new authors."""
    client.force_login(admin)
    url = reverse("si-imu-1", kwargs={"pk": fb_special_issue.id})

    # foglio 1: "Main session" + 2 contributi diversi (diversi autori e
    # titolo). Il primo autore è già presente nel DB.
    foglio = (
        ("Main session", None, None, None, None, None),
        (
            existing_user.first_name,
            existing_user.middle_name,
            existing_user.last_name,
            existing_user.email,
            existing_user.institution,
            "Title ふう",
        ),
        ("Novicius", None, "Fabulator", "nfabulator@domain.net", "Affilia", "Title ばる"),
    )
    ods = make_ods(foglio)
    data = {
        "data_file": ods,
        "create_articles_on_import": "on",
        "match_euristic": "optimistic",
        "type_of_new_articles": fb_special_issue.allowed_sections.first().id,
    }
    response = client.post(url, data)

    # Preliminary checks
    assert response.status_code == 200
    expectations = (
        "Insert Users — Step 2/3",  # NB: second step! Looking at the POST
        'name="email_1" value="iamsum@example.com"',
        'name="email_2" value="nfabulator@domain.net"',
        "Title ふう",
        "Title ばる",
    )
    response_content = response.content.decode()
    for expected in expectations:
        assert expected in response_content

    # Interesting checks
    html = lxml.html.fromstring(response_content)
    # Row 0 is for the "partition", we don't care.
    # Row 1 is about the already exising user: expect an "edit" action:
    a1 = html.find(".//input[@name='action-1'][@checked]")
    assert a1.value.startswith("edit")
    # Row 2 is about the new user: expect a "new" action:
    a2 = html.find(".//input[@name='action-2'][@checked]")
    assert a2.value == "new"


@pytest.mark.django_db
def test_si_imu_upload_two_identical_lines(
    article_journal,
    client,
    admin,
    existing_user,
    fb_special_issue,
):
    """Detect two identical contributions (same title and author).

    Refuse to process the second.
    """
    client.force_login(admin)
    url = reverse("si-imu-1", kwargs={"pk": fb_special_issue.id})

    # foglio 2: "Main session" + 2 contributi uguali tra loro (stesso
    # autore e titolo). L'autore è già presente nel DB.
    foglio = (
        ("Main session", None, None, None, None, None),
        (
            existing_user.first_name,
            existing_user.middle_name,
            existing_user.last_name,
            existing_user.email,
            existing_user.institution,
            "Title ふう",
        ),
        (
            existing_user.first_name,
            existing_user.middle_name,
            existing_user.last_name,
            existing_user.email,
            existing_user.institution,
            "Title ふう",
        ),
    )
    ods = make_ods(foglio)
    data = {
        "data_file": ods,
        "create_articles_on_import": "on",
        "match_euristic": "optimistic",
        "type_of_new_articles": fb_special_issue.allowed_sections.first().id,
    }
    response = client.post(url, data)

    # Preliminary checks
    assert response.status_code == 200
    expectations = (
        "Insert Users — Step 2/3",
        'name="email_1" value="iamsum@example.com"',
        # no `"email_2" value="iamsum@example.com"`: only an error line
        "Title ふう",
    )
    response_content = response.content.decode()
    for expected in expectations:
        assert expected in response_content

    # Interesting checks
    html = lxml.html.fromstring(response_content)
    # Row 0 is for the "partition", we don't care.
    # Row 1 is about the first already exising user: expect an "edit" action:
    a1 = html.find(".//input[@name='action-1'][@checked]")
    assert a1.value.startswith("edit")
    # Row 2 is about the spurious copy-paste: expect an error line
    error_tr = html.find(".//tr[@class='error']")
    # the error line should contain the line data (e.g. the email)...
    assert error_tr.xpath(f"td[text()='{existing_user.email}']")
    # ...and an error message
    error_msg = error_tr.find("td[@class='error']")
    assert error_msg.text == "Line 2 is the same as 1"


@pytest.mark.django_db
def test_si_imu_upload_iequal_emails(
    article_journal,
    client,
    admin,
    existing_user,
    fb_special_issue,
):
    """Suggestions based on email should be case-insensitive.

    Same as foglio 1, but the emails of the existing user in the DB
    and in te ods file are equal only ignoring case
    (uppercase/lowercase).
    """
    client.force_login(admin)
    url = reverse("si-imu-1", kwargs={"pk": fb_special_issue.id})

    case_changed_email = existing_user.email.upper()
    foglio = (
        ("Main session", None, None, None, None, None),
        (
            existing_user.first_name,
            existing_user.middle_name,
            existing_user.last_name,
            case_changed_email,  # ⇦ interesting piece here
            existing_user.institution,
            "Title ふう",
        ),
        ("Novicius", None, "Fabulator", "nfabulator@domain.net", "Affilia", "Title ばる"),
    )
    ods = make_ods(foglio)
    data = {
        "data_file": ods,
        "create_articles_on_import": "on",
        "match_euristic": "optimistic",
        "type_of_new_articles": fb_special_issue.allowed_sections.first().id,
    }
    response = client.post(url, data)

    # Preliminary checks
    assert response.status_code == 200
    expectations = (
        "Insert Users — Step 2/3",
        f'name="email_1" value="{case_changed_email}"',
        'name="email_2" value="nfabulator@domain.net"',
        "Title ふう",
        "Title ばる",
    )
    response_content = response.content.decode()
    for expected in expectations:
        assert expected in response_content

    # Interesting checks
    html = lxml.html.fromstring(response_content)
    # Row 0 is for the "partition", we don't care.
    # Row 1 is about the first already exising user: expect an "edit" action:
    a1 = html.find(".//input[@name='action-1'][@checked]")
    assert a1.value.startswith("edit")
    # Row 2 is about the new user: expect a "new" action:
    a2 = html.find(".//input[@name='action-2'][@checked]")
    assert a2.value == "new"


@pytest.mark.django_db
def test_si_imu_upload_new_author_two_contributions(
    article_journal,
    client,
    admin,
    fb_special_issue,
):
    """Two different contribution from the same author not present in the DB.

    System should suggest "new" for both lines.

    Subsequently the system will notice that the new user has two
    contributions and will re-use the newly created account, but this
    is checked elsewhere.
    """
    client.force_login(admin)
    url = reverse("si-imu-1", kwargs={"pk": fb_special_issue.id})

    foglio = (
        ("Main session", None, None, None, None, None),
        ("Novicius", None, "Fabulator", "nfabulator@domain.net", "Affilia", "Title ふう"),
        ("Novicius", None, "Fabulator", "nfabulator@domain.net", "Affilia", "Title ばる"),
    )
    ods = make_ods(foglio)
    data = {
        "data_file": ods,
        "create_articles_on_import": "on",
        "match_euristic": "optimistic",
        "type_of_new_articles": fb_special_issue.allowed_sections.first().id,
    }
    response = client.post(url, data)

    # Preliminary checks
    assert response.status_code == 200
    expectations = (
        "Insert Users — Step 2/3",
        'name="email_1" value="nfabulator@domain.net"',
        'name="email_2" value="nfabulator@domain.net"',
        "Title ふう",
        "Title ばる",
    )
    response_content = response.content.decode()
    for expected in expectations:
        assert expected in response_content

    # Interesting checks
    html = lxml.html.fromstring(response_content)
    # Row 0 is for the "partition", we don't care.
    # Row 1 is about the first already exising user: expect an "edit" action:
    a1 = html.find(".//input[@name='action-1'][@checked]")
    assert a1.value == "new"
    # Row 2 is about the new user: expect a "new" action:
    a2 = html.find(".//input[@name='action-2'][@checked]")
    assert a2.value == "new"


@pytest.mark.django_db
def test_si_imu_upload_new_author_two_contributions_iequal_emails(
    article_journal,
    client,
    admin,
    fb_special_issue,
):
    """Two different contribution from the same author not present in the DB.

    The emails in the ods are iexact, but not exact. The system should
    suggest "new" for both lines.

    Subsequently the system will notice that the new user has two
    contributions and will re-use the newly created account, but this
    is checked elsewhere.
    """
    client.force_login(admin)
    url = reverse("si-imu-1", kwargs={"pk": fb_special_issue.id})

    foglio = (
        ("Main session", None, None, None, None, None),
        ("Novicius", None, "Fabulator", "NFABULATOR@DOMAIN.NET", "Affilia", "Title ふう"),
        ("Novicius", None, "Fabulator", "nfabulator@domain.net", "Affilia", "Title ばる"),
    )
    ods = make_ods(foglio)
    data = {
        "data_file": ods,
        "create_articles_on_import": "on",
        "match_euristic": "optimistic",
        "type_of_new_articles": fb_special_issue.allowed_sections.first().id,
    }
    response = client.post(url, data)

    # Preliminary checks
    assert response.status_code == 200
    expectations = (
        "Insert Users — Step 2/3",
        'name="email_1" value="NFABULATOR@DOMAIN.NET"',
        'name="email_2" value="nfabulator@domain.net"',
        "Title ふう",
        "Title ばる",
    )
    response_content = response.content.decode()
    for expected in expectations:
        assert expected in response_content

    # Interesting checks
    html = lxml.html.fromstring(response_content)
    # Row 0 is for the "partition", we don't care.
    # Row 1 is about the first already exising user: expect an "edit" action:
    a1 = html.find(".//input[@name='action-1'][@checked]")
    assert a1.value == "new"
    # Row 2 is about the new user: expect a "new" action:
    a2 = html.find(".//input[@name='action-2'][@checked]")
    assert a2.value == "new"


WrongData = namedtuple("WrongData", ["what", "where"])
WRONG_DATA = (
    WrongData(what="Errabis", where=0),  # first
    WrongData(what="Errabis", where=1),  # middle
    WrongData(what="Errabis", where=2),  # last
    WrongData(what="Errabis", where=4),  # institution
)


@pytest.mark.parametrize("wrong_data", WRONG_DATA)
@pytest.mark.django_db
def test_si_imu_upload_two_authors_same_email_different_metadata(
    article_journal,
    client,
    admin,
    fb_special_issue,
    wrong_data,
):
    """Two different contribution from the same author not present in the DB.

    The emails in the ods are identical, but the metadata in the two
    lines is different. The system notice the problem and refuses to
    process the second.
    """
    client.force_login(admin)
    url = reverse("si-imu-1", kwargs={"pk": fb_special_issue.id})

    foglio = [
        ("Main session", None, None, None, None, None),
        ("Novicius", None, "Fabulator", "nfabulator@domain.net", "Affilia", "Title ふう"),
    ]
    problematic_line = ["Novicius", None, "Fabulator", "nfabulator@domain.net", "Affilia", "Title ばる"]
    problematic_line[wrong_data.where] = wrong_data.what
    foglio.append(problematic_line)
    ods = make_ods(foglio)
    data = {
        "data_file": ods,
        "create_articles_on_import": "on",
        "match_euristic": "optimistic",
        "type_of_new_articles": fb_special_issue.allowed_sections.first().id,
    }
    response = client.post(url, data)

    # Preliminary checks
    assert response.status_code == 200
    expectations = (
        "Insert Users — Step 2/3",
        'name="email_1" value="nfabulator@domain.net"',
        # no `"email_2" value="nfabulator@domain.net"`: only an error line
        "Title ふう",
        "Title ばる",
    )
    response_content = response.content.decode()
    for expected in expectations:
        assert expected in response_content

    # Interesting checks
    html = lxml.html.fromstring(response_content)
    # Row 0 is for the "partition", we don't care.
    # Row 1 is about the first already exising user: expect an "edit" action:
    a1 = html.find(".//input[@name='action-1'][@checked]")
    assert a1.value == "new"
    # Row 2 is about the same author with wrong data: expect an error line
    error_tr = html.find(".//tr[@class='error']")
    # the error line should contain the line data...
    assert error_tr.xpath(f"td[text()='{wrong_data.what}']")
    # ...and an error message
    error_msg = error_tr.find("td[@class='error']")
    assert error_msg.text == "Line 2 has same email but different data than 1"


@pytest.mark.django_db
def test_si_imu_new_author_and_contribution(
    article_journal,
    client,
    admin,
    fb_special_issue,
):
    """Create new account and contribution."""
    client.force_login(admin)
    url = reverse("si-imu-2", kwargs={"pk": fb_special_issue.id})
    data = {
        "tot_lines": "1",
        "create_articles_on_import": "on",
        "type_of_new_articles": fb_special_issue.allowed_sections.first().id,
        "first_name_0": "Novicius",
        "middle_name_0": None,
        "last_name_0": "Fabulator",
        "email_0": "nfabulator@domain.net",
        "institution_0": "Affilia",
        "title_0": "Title ばる",
        "action-0": "new",
    }
    response = client.post(url, data)
    assert response.status_code == 200

    # The new user has been created
    author = Account.objects.get(email=data["email_0"])

    # The new article has been created, the new user is the owner
    article = Article.objects.first()

    assert article.owner == author


@pytest.mark.django_db
def test_si_imu_new_author_same_as_exising(
    article_journal,
    client,
    admin,
    fb_special_issue,
    existing_user,
):
    """Choose "new" for an account with the same email as an existing one is the same as choosing "DB"."""
    client.force_login(admin)
    url = reverse("si-imu-2", kwargs={"pk": fb_special_issue.id})
    data = {
        "tot_lines": "1",
        "create_articles_on_import": "on",
        "type_of_new_articles": fb_special_issue.allowed_sections.first().id,
        "first_name_0": existing_user.first_name,
        "middle_name_0": existing_user.middle_name or "",
        "last_name_0": existing_user.last_name,
        "email_0": existing_user.email,  # ⇦ these two don't agree :)
        "institution_0": existing_user.institution or "",
        "title_0": "Title ばる",
        "action-0": "new",  # ⇦ these two don't agree :)
    }
    response = client.post(url, data)
    assert response.status_code == 200

    # The new user has been created
    author = Account.objects.get(email=data["email_0"])

    # The new article has been created, the new user is the owner
    article = Article.objects.first()
    assert article.owner == author


@pytest.mark.parametrize("wrong_data", WRONG_DATA)
@pytest.mark.django_db
def test_si_imu_new_author_same_as_exising_but_different_data(
    wrong_data,
    article_journal,
    client,
    admin,
    fb_special_issue,
    existing_user,
):
    """Choosing "new" for an account with the same email as an existing but with some different data causes error."""
    client.force_login(admin)
    url = reverse("si-imu-2", kwargs={"pk": fb_special_issue.id})
    messedup_data = [
        existing_user.first_name,
        existing_user.middle_name,
        existing_user.last_name,
        existing_user.email,  # never used, just a place-holder
        existing_user.institution,
    ]
    messedup_data[wrong_data.where] = wrong_data.what
    data = {
        "tot_lines": "1",
        "create_articles_on_import": "on",
        "type_of_new_articles": fb_special_issue.allowed_sections.first().id,
        "first_name_0": messedup_data[0],
        "middle_name_0": messedup_data[1] or "",
        "last_name_0": messedup_data[2],
        "email_0": existing_user.email,  # ⇦ these two don't agree :)
        "institution_0": messedup_data[4] or "",
        "title_0": "Title ばる",
        "action-0": "new",  # ⇦ these two don't agree :)
    }
    response = client.post(url, data)
    assert response.status_code == 200

    # An error has been reported
    assert (
        f"ERROR - different data for existing user with email &quot;{existing_user.email}&quot;"
        in response.content.decode()
    )

    # The existing user has not been changed
    existing_user.refresh_from_db()
    assert wrong_data.what not in [
        existing_user.first_name,
        existing_user.middle_name,
        existing_user.last_name,
        existing_user.institution,
    ]

    # No article has been created
    article = Article.objects.first()
    assert not article


@pytest.mark.parametrize("modified_data", WRONG_DATA)
@pytest.mark.django_db
def test_si_imu_edit_exising(
    article_journal,
    client,
    admin,
    fb_special_issue,
    existing_user,
    modified_data,
):
    """Choose "edit" should produce a form and display data from ods and db.

    The existing user is not yet modified."""
    client.force_login(admin)
    url = reverse("si-imu-2", kwargs={"pk": fb_special_issue.id})
    new_data = [
        existing_user.first_name,
        existing_user.middle_name,
        existing_user.last_name,
        existing_user.email,  # never used, just a place-holder
        existing_user.institution,
    ]
    new_data[modified_data.where] = modified_data.what
    data = {
        "tot_lines": "1",
        "create_articles_on_import": "on",
        "type_of_new_articles": fb_special_issue.allowed_sections.first().id,
        "first_name_0": new_data[0],
        "middle_name_0": new_data[1] or "",
        "last_name_0": new_data[2],
        "email_0": existing_user.email,
        "institution_0": new_data[4] or "",
        "title_0": "Title ばる",
        "action-0": f"edit_{existing_user.id}",
    }
    response = client.post(url, data)
    assert response.status_code == 200

    # The existing_user has _not_ been modified (yet)
    existing_user.refresh_from_db()
    existing_user_data = [
        existing_user.first_name,
        existing_user.middle_name,
        existing_user.last_name,
        existing_user.email,  # never used, just a place-holder
        existing_user.institution,
    ]
    assert existing_user_data[modified_data.where] != modified_data.what

    # Data from the db and from the ods are both shown
    response_content = response.content.decode()
    html = lxml.html.fromstring(response_content)
    assert html.xpath(f".//td[text()='{new_data[modified_data.where]}']")
    # don't look for empty values (they could be anything anywhere)
    expected_text = existing_user_data[modified_data.where]
    if expected_text is not None:
        assert html.xpath(f".//td[text()='{expected_text}']")

    # The new article has been created, the existing user is the owner
    article = Article.objects.first()
    # NB: existing_user is a JCOMProfile, not a core.Account!
    assert article.owner == existing_user.janeway_account
