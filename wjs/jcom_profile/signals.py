"""Signals.

Every time a user model instance is created, a corresponding JCOM
profile instance must be created as well.

"""

from django.conf import settings
from django.db.models.signals import post_save
from django.dispatch import receiver
from submission.models import Article
from wjs.jcom_profile.models import ArticleWrapper, JCOMProfile


@receiver(post_save, sender=settings.AUTH_USER_MODEL)
def create_profile_handler(sender, instance, created, **kwargs):
    """Create the JCOM profile, only if the Account is newly created."""
    if not created:
        return

    JCOMProfile.objects.create(janeway_account=instance)

    # If I don't `save()` the instance also, an empty record is
    # created.
    #
    # I think this is because the post_save message is emitted by one
    # of core.forms.RegistrationForm's ancestor (l.133) but with
    # `commit=False`, so that the form's data is not yet in the DB.
    instance.save()

    # NB: instance.save_m2m() fails with
    # AttributeError: 'Account' object has no attribute 'save_m2m'
    # because this is not a many-to-many relation
    # https://django.readthedocs.io/en/stable/topics/forms/modelforms.html?highlight=save_m2m#the-save-method


@receiver(post_save, sender=Article)
def create_articlewrapper_handler(sender, instance, created, **kwargs):
    """Create a record in our ArticleWrapper when any Article is newly created."""
    if not created:
        return
    ArticleWrapper.objects.get_or_create(janeway_article=instance)
