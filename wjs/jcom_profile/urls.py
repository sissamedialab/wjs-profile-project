"""My URLs. Looking for a way to "enrich" Janeway's `edit_profile`."""

from django.conf.urls import url
from wjs.jcom_profile import views
from core import include_urls


urlpatterns = [
    url(r'^profile/$', views.prova, name='core_edit_profile'),
    url(r'^prova/$', views.prova, name='core_edit_profile'),
]

urlpatterns.extend(include_urls.urlpatterns)
include_urls.urlpatterns = urlpatterns
