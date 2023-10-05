import pytest
from django.core import management
from django.test.utils import captured_stdout


@pytest.mark.django_db
def test_two_journals_two_accounts_three_recipients(
    account_factory,
    recipient_factory,
    journal_factory,
):
    journal1 = journal_factory("JCOM")
    journal2 = journal_factory("JCOMAL")

    # jcom - 1 recipient_user 2 recipients
    user1 = account_factory(email="u1@email.com")
    recipient_factory(user=user1, journal=journal1)
    recipient_factory(journal=journal1, email="r1_j1@email.com")
    recipient_factory(journal=journal1, email="r2_j1@email.com")

    # jcomal - 1 recipient_user 1 recipients
    recipient_factory(user=user1, journal=journal2)
    recipient_factory(journal=journal2, email="r1_j2@email.com")

    expected = """JCOM_recipients.value 2
JCOM_recipients_account.value 1
JCOMAL_recipients.value 1
JCOMAL_recipients_accounts.value 1
"""
    with captured_stdout() as stdout:
        management.call_command("munin_count_recipients")
        output = stdout.getvalue()
        assert output == expected


@pytest.mark.django_db
def test_config_output():
    config_expected = """graph_title Newsletter Recipients
graph_vlabel recipients count
graph_category WJS
graph_info Count people receiving WJS Newsletter.
JCOM_recipients.label JCOM anonymous
JCOM_recipients.info Number of JCOM users without account who receive the Newsletter.
JCOM_recipients_account.label JCOM with account
JCOM_recipients_account.info Number of JCOM users with account who receive the Newsletter.
JCOMAL_recipients.label JCOMAL anonymous
JCOMAL_recipients.info Number of JCOMAL users without account who receive the Newsletter.
JCOMAL_recipients_account.label JCOMAL with account
JCOMAL_recipients_account.info Number of JCOMAL users with account who receive the Newsletter.
"""
    with captured_stdout() as stdout:
        management.call_command("munin_count_recipients", "config")
        output = stdout.getvalue()
        assert output == config_expected
