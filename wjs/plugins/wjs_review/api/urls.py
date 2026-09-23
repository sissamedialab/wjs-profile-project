from django.urls import path

from .views import (
    ArticleGalleyListView,
    ArticleGalleyView,
    ArticleZipDownloadView,
    CollaborationListView,
    JournalProductionListView,
    TypesetterPapersListView,
)

urlpatterns = [
    path("collaborations/", CollaborationListView.as_view(), name="collaborations"),
    path("article/<int:pk>/zip/", ArticleZipDownloadView.as_view(), name="article-zip"),
    path("article/<int:pk>/galleys/", ArticleGalleyListView.as_view(), name="article-galleys"),
    path("article/<int:pk>/galley/<str:file_type>/", ArticleGalleyView.as_view(), name="article-galley"),
    path(
        "article/<int:pk>/galley/<str:file_type>/<int:sequence>/",
        ArticleGalleyView.as_view(),
        name="article-galley-seq",
    ),
    path(
        "journal/<str:code>/typesetter/<int:typesetter_pk>/papers/",
        TypesetterPapersListView.as_view(),
        name="typesetter-papers",
    ),
    # G7
    path(
        "journal/<str:code>/production/",
        JournalProductionListView.as_view(),
        name="journal-production",
    ),
]
