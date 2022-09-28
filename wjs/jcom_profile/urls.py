"""My URLs. Looking for a way to "enrich" Janeway's `edit_profile`."""

from django.conf.urls import url
from wjs.jcom_profile import views
from core import include_urls


urlpatterns = [
    url(r'^profile/$', views.prova, name='core_edit_profile'),
    url(r'^register/step/1/$', views.register, name='core_register'),
    url(r'^register/activate/gdpr/(?P<token>.+)/', views.confirm_gdpr_acceptance, name='accept_gdpr'),
    # url(r'^register/step/2/(?P<token>[\w-]+)/$', core_views.activate_account, name='core_confirm_account'),
    # url(r'^register/step/orcid/(?P<token>[\w-]+)/$', core_views.orcid_registration, name='core_orcid_registration'),

]

urlpatterns.extend(include_urls.urlpatterns)
include_urls.urlpatterns = urlpatterns
