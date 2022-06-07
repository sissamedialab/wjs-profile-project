"""Do not cleanup / use a read only database."""

# You can replace the ordinary django_db_setup to completely avoid
# database creation/migrations. If you have no need for rollbacks or
# truncating tables, you can simply avoid blocking the database and
# use it directly. When using this method you must ensure that your
# tests do not change the database state.

# https://pytest-django.readthedocs.io/en/latest/database.html

import pytest


# @pytest.fixture(scope='session')
# def django_db_setup():
#     """Avoid creating/setting up the test database."""
#     pass


# @pytest.fixture
# def db_access_without_rollback_and_truncate(
#         request,
#         django_db_setup, django_db_blocker):
#     """Do not clean the DB."""
#     django_db_blocker.unblock()
#     request.addfinalizer(django_db_blocker.restore)
