from django.urls import path

from .views import (
    ArticleGalleyListView,
    ArticleGalleyView,
    ArticleZipView,
    CollaborationListView,
    JournalProductionListView,
    RedocUIView,
    SchemaView,
    SwaggerUIView,
    TypesetterPapersListView,
)

urlpatterns = [
    path("collaborations/", CollaborationListView.as_view(), name="collaborations"),
    # GET downloads the published sources; PUT replaces them and regenerates the galleys.
    path("article/<int:pk>/zip/", ArticleZipView.as_view(), name="article-zip"),
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
    path("schema/", SchemaView.as_view(), name="schema"),
    path("schema/swagger-ui/", SwaggerUIView.as_view(url_name="schema"), name="swagger-ui"),
    path("schema/redoc/", RedocUIView.as_view(url_name="schema"), name="redoc"),
]
