from django.conf.urls import url

from . import views
from .plugin_settings import MANAGER_URL

urlpatterns = [
    url(r"^manager/$", views.ConfigUpdateView.as_view(), name=MANAGER_URL),
]
