"""My URLs. Looking for a way to "enrich" Janeway's `edit_profile`."""

from core import include_urls
from django.conf.urls import url
from submission import views as submission_views

from wjs.jcom_profile import views

urlpatterns = [
    url(r"^(?P<type>[-\w.]+)/start/$", views.start, name="submission_start"),
    url(r"^profile/$", views.prova, name="core_edit_profile"),
    url(r"^register/step/1/$", views.register, name="core_register"),
    url(
        r"^register/activate/gdpr/(?P<token>.+)/",
        views.confirm_gdpr_acceptance,
        name="accept_gdpr",
    ),
    # Override submission's second step defined in submission.url ...
    # (remember that core.include_url adds a "prefix" to the pattern,
    # here "submit/")
    url(
        r"^submit/(?P<article_id>\d+)/info/$",
        views.SpecialIssues.as_view(),
        name="submit_info",
    ),
    # ... and "rename" it (i.e. the submission's second step) to be
    # able to get back in the loop
    url(
        r"^submit/(?P<article_id>\d+)/info-metadata/$",
        submission_views.submit_info,
        name="submit_info_original",
    ),
]

urlpatterns.extend(include_urls.urlpatterns)
include_urls.urlpatterns = urlpatterns
