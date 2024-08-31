"""Signals.

Every time a user model instance is created, a corresponding JCOM
profile instance must be created as well.

"""

from django.conf import settings
from django.db.models.signals import post_save
from django.dispatch import receiver
from journal.models import Issue

from wjs.jcom_profile.models import IssueParameters, JCOMProfile


@receiver(post_save, sender=settings.AUTH_USER_MODEL)
def create_profile_handler(sender, instance, created, **kwargs):
    """Create the JCOM profile, only if the Account is newly created."""
    if not created:
        return

    # Using save_base skips the save() method of the JCOMProfile model and correctly creates the instance of our
    # subclass without resetting the user data.
    # It ensures no django magic is applied because we are basically creating a duplicate of the original data.
    # https://stackoverflow.com/questions/9821935/django-model-inheritance-create-a-subclass-using-existing-super-class
    return JCOMProfile(janeway_account=instance).save_base(raw=True)


@receiver(post_save, sender=Issue)
def create_special_issue_parameters(sender, instance, created, **kwargs):
    """Create IssueParameters instance for each Issue."""
    IssueParameters.objects.get_or_create(issue=instance)
