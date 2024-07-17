"""My URLs. Looking for a way to "enrich" Janeway's `edit_profile`."""

from core import include_urls
from django.conf import settings
from django.urls import include, path, re_path
from journal import views as journal_views

from wjs.jcom_profile import experimental_views, views
from wjs.jcom_profile.newsletter import views as newsletter_views

urlpatterns = [
    re_path(r"^(?P<type>[-\w.]+)/start/$", views.start, name="submission_start"),
    path("profile/", views.edit_profile, name="core_edit_profile"),
    path("register/step/1/", views.register, name="core_register"),
    re_path(
        r"^register/activate/gdpr/(?P<token>.+)/",
        views.confirm_gdpr_acceptance,
        name="accept_gdpr",
    ),
    # Override journal search
    path("search/", views.PublishedArticlesListView.as_view(), name="search"),
    # Override submission's second step defined in submission.url ...
    # (remember that core.include_url adds a "prefix" to the pattern,
    # here "submit/")
    path(
        "submit/<int:article_id>/info/",
        views.SpecialIssues.as_view(),
        name="submit_info",
    ),
    # ... and "rename" it (i.e. the submission's second step) to be
    # able to get back in the loop
    path(
        "submit/<int:article_id>/info-metadata/",
        # was submission_views.submit_info, but I'm also overriding this part:
        views.submit_info,
        name="submit_info_original",
    ),
    path(
        "update/parameters/",
        views.EditorAssignmentParametersUpdate.as_view(),
        name="assignment_parameters",
    ),
    path(
        "update/parameters/<int:editor_pk>/",
        views.DirectorEditorAssignmentParametersUpdate.as_view(),
        name="assignment_parameters",
    ),
    # Special Issues mgmt
    #     add, view, update
    path("manage/si/new", views.SICreate.as_view(template_name="admin/core/si_new.html"), name="si-create"),
    path("si/<int:pk>/", views.SIDetails.as_view(template_name="si_details.html"), name="si-details"),
    path(
        "manage/si/<int:pk>/edit",
        views.SIUpdate.as_view(template_name="admin/core/si_update.html"),
        name="si-update",
    ),
    #     files (aka documents; upload & download)
    re_path(
        r"^si/(?P<special_issue_id>\d+)/file/(?P<file_id>\d+)/",
        views.serve_special_issue_file,
        name="special_issue_file_download",
    ),
    re_path(
        r"^si_file_upload/(?P<special_issue_id>\d+)/",
        views.SIFileUpload.as_view(),
        name="special_issue_file_upload",
    ),
    #
    # IMU - Insert Many Users
    #
    path(
        "si/<int:pk>/imu1-upload",
        views.IMUStep1.as_view(template_name="admin/core/si_imu_upload.html"),
        name="si-imu-1",
    ),
    path(
        "si/<int:pk>/imu2-import",
        views.IMUStep2.as_view(template_name="admin/core/si_imu_imported.html"),
        name="si-imu-2",
    ),
    path(
        "si/<int:pk>/imu3-edit",
        views.IMUStep3.as_view(),
        name="si-imu-3",
    ),
    # Issues - override view "journal_issues" from journal.urls
    path("issues/", views.issues, name="journal_issues"),
    #
    # JCOM has "()" in some pubid identifiers; I need to overwrite
    # "article_view" from journal.urls
    re_path(
        r"^article/(?P<identifier_type>pubid)/(?P<identifier>[\w().-]+)/$",
        journal_views.article,
        name="article_view",
    ),
    path(
        "update/newsletters/",
        views.NewsletterParametersUpdate.as_view(),
        name="edit_newsletters",
    ),
    path(
        "register/newsletters/",
        views.AnonymousUserNewsletterRegistration.as_view(),
        name="register_newsletters",
    ),
    path(
        "register/newsletters/email-sent/",
        views.AnonymousUserNewsletterConfirmationEmailSent.as_view(),
        name="register_newsletters_email_sent",
    ),
    path(
        "newsletters/unsubscribe/confirm/",
        views.UnsubscribeUserConfirmation.as_view(),
        name="unsubscribe_newsletter_confirm",
    ),
    re_path(
        r"^newsletters/unsubscribe/(?P<token>\w+)/$",
        views.unsubscribe_newsletter,
        name="unsubscribe_newsletter",
    ),
    path(
        "articles/",
        views.PublishedArticlesListView.as_view(exclude_children=True),
        name="journal_articles",
    ),
    re_path(
        r"^articles/keyword/(?P<keyword>[\w.-]+)/$",
        views.PublishedArticlesListView.as_view(filter_by="keyword"),
        name="articles_by_keyword",
    ),
    re_path(
        r"^articles/section/(?P<section>[\w.-]+)/$",
        views.PublishedArticlesListView.as_view(filter_by="section"),
        name="articles_by_section",
    ),
    re_path(
        r"^articles/author/(?P<author>[\w.-]+)/$",
        views.PublishedArticlesListView.as_view(filter_by="author"),
        name="articles_by_author",
    ),
    # Redirects - start
    # Drupal favicon
    re_path(
        r"^sites/all/themes/jcom(?:al)?/favicon.png$",
        views.FaviconRedirect.as_view(),
        name="jcom_redirect_favicon",
    ),
    # JCOM issues were /archive/01/02/
    # JCOMAL issues were /es/01/02/ (or /pt-br/01/02/)
    re_path(
        r"^(?P<root>archive|es|pt-br)/(?P<volume>\d{2})/(?P<issue>[\d]{2,3})/?$",
        views.JcomIssueRedirect.as_view(),
        name="jcom_redirect_issue",
    ),
    # Drupal-style supplementary file url
    #    RewriteRule ".../(JCOM[^/]+_ATTACH_[^/]+)$"
    re_path(
        r"sites/default/files/documents/supplementary_material/(?P<pubid>[\w.()-]+?)(?P<attachment>_ATTACH_[^/]+)$",
        views.JcomFileRedirect.as_view(),
        name="jcom_redirect_file",
    ),
    # Drupal-style galley url
    #     sites/default/files/documents/jcom_123.pdf
    # and old form of citation_pdf_url
    #     RewriteRule "^/archive/.*/(JCOM[^/]+\.pdf)"
    re_path(
        r"(?P<root>archive/.*/|sites/default/files/documents/|(?P<site_language>(pt-br|es))/.*/)"
        r"(?P<pubid>[\w.()-]+?)(?:_(?P<language>[a-z]{2}))?(?P<error>_\d)?\.(?P<extension>pdf|epub)$",
        views.JcomFileRedirect.as_view(),
        name="jcom_redirect_file",
    ),
    # Search engines (google scholar & co.) want the PDF file in the
    # same subfolder as the paper's landing page (see #107);
    re_path(
        r"^article/(?P<identifier_type>pubid)/(?P<identifier>.+)/download/pdf/$",
        journal_views.serve_article_pdf,
        name="serve_article_pdf",
    ),
    re_path(
        "^keywords/(?P<kwd_slug>[a-z-]+)$",
        views.DrupalKeywordsRedirect.as_view(),
        name="drupal_keywords_redirect",
    ),
    re_path(
        "^(?P<jcomal_lang>pt-br/|es/)?author/(?P<author_slug>[a-zA-Z-]+)$",
        views.DrupalAuthorsRedirect.as_view(),
        name="drupal_author_redirect",
    ),
    # Redirects - end
    path(
        "dashboard/eo/",
        views.eo_home,
        name="dashboard_eo",
    ),
    # Set notify flag
    path(
        "set_notify/",
        views.set_notify_hijack,
        name="set_notify_hijack",
    ),
]

# Some experimental / Easter-egg URLs
experimental_urls = [
    re_path("experimental/issues", experimental_views.IssuesForceGraph.as_view(), name="issues_forcegraph"),
    re_path("experimental/authors_by_coa", experimental_views.AuthorsForceGraph.as_view(), name="authors_forcegraph"),
    re_path(
        "experimental/authors_by_kwd",
        experimental_views.AuthorsKeywordsForceGraph.as_view(),
        name="authors_forcegraph",
    ),
    re_path(
        "experimental/articles_by_kwd",
        experimental_views.ArticlesByKeywordForceGraph.as_view(),
        name="articles_forcegraph",
    ),
    re_path(r"newsletter/(?P<journal>[\w.()-]+)/(?P<days>\d+)/", newsletter_views.newsletter, name="newsletter"),
    re_path(r"newsletter/(?P<journal>[\w.()-]+)/", newsletter_views.newsletter, name="newsletter"),
]

if "rosetta" in settings.INSTALLED_APPS:
    urlpatterns += [path("rosetta/", include("rosetta.urls"))]

if "wjs_mgmt_cmds" in settings.INSTALLED_APPS:
    urlpatterns += [path("wjs_mgmt_cmds/", include("wjs_mgmt_cmds.urls"))]


urlpatterns.extend(experimental_urls)

urlpatterns.extend(include_urls.urlpatterns)
include_urls.urlpatterns = urlpatterns
