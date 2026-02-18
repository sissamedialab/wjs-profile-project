from django.urls import path

from .views import ArticleGalleyListView, ArticleGalleyView, ArticleZipDownloadView

urlpatterns = [
    path("article/<int:pk>/zip/", ArticleZipDownloadView.as_view(), name="article-zip"),
    path("article/<int:pk>/galleys/", ArticleGalleyListView.as_view(), name="article-galleys"),
    path("article/<int:pk>/galley/<str:file_type>/", ArticleGalleyView.as_view(), name="article-galley"),
    path(
        "article/<int:pk>/galley/<str:file_type>/<int:sequence>/",
        ArticleGalleyView.as_view(),
        name="article-galley-seq",
    ),
]
