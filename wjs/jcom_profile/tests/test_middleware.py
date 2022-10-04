"""Tests related to the if-no-gdpr-acknowledged-redirect-me middleware."""
import pytest
from core.models import Account
from django.test import Client


@pytest.mark.django_db
def test_anonymous_can_navigate(journal):
    """Test that anonymous users can navigate.

    Here "anonymous" means not logged in.
    """
    client = Client()
    response = client.get("/contact/")
    assert response.status_code == 200


@pytest.mark.django_db
def test_normal_user_can_navigate(journal):
    """Test that a normal user can navigate normally.

    A normal user has acknowledged our privay policy (i.e. checkbox is
    selected).

    """
    # https://stackoverflow.com/a/39742798/1581629
    client = Client()
    user = Account.objects.get_or_create(username="testuser")[0]
    user.is_active = True
    user.jcomprofile.gdpr_checkbox = True
    user.jcomprofile.save()
    user.save()
    client.force_login(user)

    response = client.get("/contact/")
    assert response.status_code == 200


@pytest.mark.django_db
def test_shy_user_cannot_navigate(journal):
    """Test that a user that didn't acknowledge privacy cannot navigate."""
    # check this also:
    # https://flowfx.de/blog/test-django-with-selenium-pytest-and-user-authentication/
    client = Client()
    user = Account.objects.get_or_create(username="testuser")[0]
    user.is_active = True
    user.jcomprofile.gdpr_checkbox = False
    user.jcomprofile.save()
    user.save()
    client.force_login(user)

    response = client.get("/contact/")
    assert response.status_code == 302


@pytest.mark.django_db
def test_shy_user_cannot_navigate_bis(journal, client):
    """Test that a user that didn't acknowledge privacy cannot navigate.

    Same as test_shy_user_cannot_navigate, but with a different way of
    creating the user and of logging-in.

    """
    # https://pytest-django.readthedocs.io/en/latest/helpers.html#client-django-test-client
    username = "user1"
    password = "bar"
    email = "e@mail.it"
    user = Account.objects.create_user(
        username=username, password=password, email=email
    )
    user.is_active = True
    user.jcomprofile.gdpr_checkbox = False
    user.jcomprofile.save()
    user.save()
    client.login(username=email, password=password)

    response = client.get("/contact/")
    assert response.status_code == 302


@pytest.mark.django_db
def test_middleware_honors_settings(journal, client, settings):
    """Test that the middleware lets shy user navigate configured URLs."""
    shy_user = Account.objects.get_or_create(username="testuser")[0]
    shy_user.is_active = True
    shy_user.jcomprofile.gdpr_checkbox = False
    shy_user.jcomprofile.save()
    shy_user.save()
    client.force_login(shy_user)

    settings.CORE_PRIVACY_MIDDLEWARE_ALLOWED_URLS = ["/profile/", "/logout/"]
    response = client.get("/profile/")
    assert response.status_code == 200
    response = client.get("/contact/")
    assert response.status_code == 302
    settings.CORE_PRIVACY_MIDDLEWARE_ALLOWED_URLS = ["/contact/", ]
    response = client.get("/profile/")
    assert response.status_code == 302
    response = client.get("/contact/")
    assert response.status_code == 200
