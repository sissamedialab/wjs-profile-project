"""URLs for the synchronization of article metadata between TeX sources and DB."""

from django.urls import path

from .views import SyncTeXDB

urlpatterns = [
    path("sync_texdb/<int:pk>/", SyncTeXDB.as_view(), name="wjs_sync_tex_db"),
]
