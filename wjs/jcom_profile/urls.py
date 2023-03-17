"""My URLs. Looking for a way to "enrich" Janeway's `edit_profile`."""

from core import include_urls
from django.conf.urls import url
from journal import views as journal_views

from wjs.jcom_profile import experimental_views, views
from wjs.jcom_profile.newsletter import views as newsletter_views

urlpatterns = [
    url(r"^(?P<type>[-\w.]+)/start/$", views.start, name="submission_start"),
    url(r"^profile/$", views.edit_profile, name="core_edit_profile"),
    url(r"^register/step/1/$", views.register, name="core_register"),
    url(
        r"^register/activate/gdpr/(?P<token>.+)/",
        views.confirm_gdpr_acceptance,
        name="accept_gdpr",
    ),
    # Override journal search
    url(r"^search/$", views.search, name="search"),
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
        # was submission_views.submit_info, but I'm also overriding this part:
        views.submit_info,
        name="submit_info_original",
    ),
    url(
        r"^update/parameters/$",
        views.EditorAssignmentParametersUpdate.as_view(),
        name="assignment_parameters",
    ),
    url(
        r"^update/parameters/(?P<editor_pk>\d+)/$",
        views.DirectorEditorAssignmentParametersUpdate.as_view(),
        name="assignment_parameters",
    ),
    # Special Issues mgmt
    #     add, view, update
    url(r"^manage/si/new$", views.SICreate.as_view(template_name="admin/core/si_new.html"), name="si-create"),
    url(r"^si/(?P<pk>\d+)/$", views.SIDetails.as_view(template_name="si_details.html"), name="si-details"),
    url(
        r"^manage/si/(?P<pk>\d+)/edit$",
        views.SIUpdate.as_view(template_name="admin/core/si_update.html"),
        name="si-update",
    ),
    #     files (aka documents; upload & download)
    url(
        r"^si/(?P<special_issue_id>\d+)/file/(?P<file_id>\d+)/",
        views.serve_special_issue_file,
        name="special_issue_file_download",
    ),
    url(
        r"^si_file_upload/(?P<special_issue_id>\d+)/",
        views.SIFileUpload.as_view(),
        name="special_issue_file_upload",
    ),
    #
    # IMU - Insert Many Users
    #
    url(
        r"^si/(?P<pk>\d+)/imu1-upload$",
        views.IMUStep1.as_view(template_name="admin/core/si_imu_upload.html"),
        name="si-imu-1",
    ),
    url(
        r"^si/(?P<pk>\d+)/imu2-import$",
        views.IMUStep2.as_view(template_name="admin/core/si_imu_imported.html"),
        name="si-imu-2",
    ),
    url(
        r"^si/(?P<pk>\d+)/imu3-edit$",
        views.IMUStep3.as_view(),
        name="si-imu-3",
    ),
    # Issues - override view "journal_issues" from journal.urls
    url(r"^issues/$", views.issues, name="journal_issues"),
    #
    # JCOM has "()" in some pubid identifiers; I need to overwrite
    # "article_view" from journal.urls
    url(
        r"^article/(?P<identifier_type>pubid)/(?P<identifier>[\w().-]+)/$",
        journal_views.article,
        name="article_view",
    ),
    url(
        r"^update/newsletters/$",
        views.NewsletterParametersUpdate.as_view(),
        name="edit_newsletters",
    ),
    url(
        r"^register/newsletters/$",
        views.AnonymousUserNewsletterRegistration.as_view(),
        name="register_newsletters",
    ),
    url(
        r"^register/newsletters/email-sent/$",
        views.AnonymousUserNewsletterConfirmationEmailSent.as_view(),
        name="register_newsletters_email_sent",
    ),
    url(
        r"^register/newsletters/email-sent/(?P<id>\d+)/$",
        views.AnonymousUserNewsletterConfirmationEmailSent.as_view(),
        name="register_newsletters_email_sent",
    ),
    url(
        r"^newsletters/unsubscribe/confirm/$",
        views.UnsubscribeUserConfirmation.as_view(),
        name="unsubscribe_newsletter_confirm",
    ),
    url(
        r"^newsletters/unsubscribe/(?P<token>\w+)/$",
        views.unsubscribe_newsletter,
        name="unsubscribe_newsletter",
    ),
    url(r"^articles/keyword/(?P<keyword>[\w.-]+)/$", views.filter_articles, name="articles_by_keyword"),
    url(r"^articles/section/(?P<section>[\w.-]+)/$", views.filter_articles, name="articles_by_section"),
    url(r"^articles/author/(?P<author>[\w.-]+)/$", views.filter_articles, name="articles_by_author"),
    # Redirects - start
    url(
        r"archive/(?P<volume>\d{2})/(?P<issue>[\d-]{2,3})/?$",
        views.JcomIssueRedirect.as_view(),
        name="jcom_redirect_issue",
    ),
    # Drupal-style supplementary file url
    #    RewriteRule "^/archive/.*/(JCOM[^/]+_ATTACH_[^/]+)$"
    url(
        r"sites/default/files/documents/supplementary_material/(?P<pubid>[\w.()-]+?)(?P<attachment>_ATTACH_[^/]+)$",
        views.JcomFileRedirect.as_view(),
        name="jcom_redirect_file",
    ),
    # Drupal-style galley url
    #     sites/default/files/documents/jcom_123.pdf
    # and old form of citation_pdf_url
    #     RewriteRule "^/archive/.*/(JCOM[^/]+\.pdf)"
    url(
        r"(?P<root>archive/.*/|sites/default/files/documents/)"
        r"(?P<pubid>[\w.()-]+?)(?:_(?P<language>[a-z]{2}))?(?P<error>_\d)?\.(?P<extension>pdf|epub)$",
        views.JcomFileRedirect.as_view(),
        name="jcom_redirect_file",
    ),
    # Search engines (google scholar & co.) want the PDF file in the
    # same subfolder as the paper's landing page (see #107);
    url(
        r"^article/(?P<identifier_type>pubid)/(?P<identifier>.+)/download/pdf/$",
        journal_views.serve_article_pdf,
        name="serve_article_pdf",
    ),
    # Redirects - end
]

# Some experimental / Easter-egg URLs
experimental_urls = [
    url("experimental/issues", experimental_views.IssuesForceGraph.as_view(), name="issues_forcegraph"),
    url(r"newsletter/(?P<journal>[\w.()-]+)/", newsletter_views.newsletter, name="newsletter"),
]
urlpatterns.extend(experimental_urls)

urlpatterns.extend(include_urls.urlpatterns)
include_urls.urlpatterns = urlpatterns
