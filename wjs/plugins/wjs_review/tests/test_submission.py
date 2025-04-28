"""Test related to the submission process."""

import pytest
from django.contrib.auth import get_user_model
from django.test.client import Client
from django.urls import reverse
from submission.models import Article

from wjs.jcom_profile.models import JCOMProfile

Account = get_user_model()


@pytest.mark.django_db
def test_create_coauthor_during_submission(
    article: Article,
    normal_user: JCOMProfile,
    client: Client,
):
    """New users created as co-authors are not active."""
    # Janeway decides whether to create a new Account if the given email cannot be found in the DB
    new_email = "mc1@invalid.com"
    assert not Account.objects.filter(email=new_email).exists()

    # The creation of the new co-author Account is performed by the view.
    # This was the easiest way I was able to find to simulate the process...

    # Janeway decorator article_edit_user_required() checks for article owner:
    article.correspondence_author = normal_user.janeway_account
    article.owner = normal_user.janeway_account
    # Janeway decorator article_is_not_submitted() checks for the submitted date:
    article.date_submitted = None
    # submit_authors view checks the article's current submission step:
    article.current_step = 3
    article.save()
    url = reverse("submit_authors", kwargs={"article_id": article.pk})
    client.force_login(normal_user)
    response = client.post(
        path=url,
        data={
            "first_name": "Marco",
            "last_name": "Caco",
            "email": new_email,
            "add_author": "",  # ⇦ this triggers the creation of new Account!
        },
    )
    # When a new co-author is created and added to the article,
    # we are redirected to the submit-authors stage
    assert response.status_code == 302
    assert response.headers["Location"] == url

    # Here is the interesting part:
    # a new Account has been created
    # but it's not active (this is a problem)
    assert Account.objects.get(email=new_email)
    assert not Account.objects.get(email=new_email).is_active
