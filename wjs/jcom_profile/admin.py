"""Register the models with the admin interface."""

from django.contrib import admin
from wjs.jcom_profile.models import JCOMProfile
# from django.contrib.admin.sites import NotRegistered
from core.models import Account
from core.admin import AccountAdmin


class JCOMProfileInline(admin.StackedInline):
    """Helper class to "inline" account profession."""

    model = JCOMProfile
    fields = ['profession', 'gdpr_checkbox']
    # TODO: No! this repeats all the fields (first name, password,...)


# TODO: use settings.AUTH_USER_MODEL
# from django.conf import settings
class UserAdmin(AccountAdmin):
    """Another layer..."""

    inlines = (JCOMProfileInline, )


admin.site.unregister(Account)
admin.site.register(Account, UserAdmin)
