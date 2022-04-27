"""Signals.

Every time a user model instance is created, a corresponding user
profile instance must be created as well.

"""

from django.db.models.signals import post_save
from django.dispatch import receiver
from django.conf import settings
from wjs.jcom_profile.models import JCOMProfile


@receiver(post_save, sender=settings.AUTH_USER_MODEL)
def create_profile_handler(sender, instance, created, **kwargs):
    """Handle AccountProfession creation (if necessary)."""
    if not created:
        return
    # Create the profile object, only if it is newly created

    # TODO: move defalt to model OR
    # change the user-creation form OR
    # do something else?
    default_profession = 3
    profile = JCOMProfile(profession=default_profession)
    profile.save()
    instance.profession = profile
    instance.save()
