"""Signals.

Every time a user model instance is created, a corresponding JCOM
profile instance must be created as well.

"""

from django.db.models.signals import post_save
from django.dispatch import receiver
from django.conf import settings
from wjs.jcom_profile.models import JCOMProfile


@receiver(post_save, sender=settings.AUTH_USER_MODEL)
def create_profile_handler(sender, instance, created, **kwargs):
    """Create the JCOM profile, only if the Account is newly created."""
    if not created:
        return

    # TODO: move defalt to model OR
    # change the user-creation form OR
    # do something else?
    default_profession = 3
    JCOMProfile(
        janeway_account=instance,
        profession=default_profession).save()
    # If I don't `save()` the instance again, an empty record is created
    # (not sure what's happening here...)
    instance.save()
