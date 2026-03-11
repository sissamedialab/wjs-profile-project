from django.urls import path

from . import consumers

websocket_urlpatterns = [
    path("ws/feedback/<str:feedback_wsname>/<int:article_id>/<uuid:uuid>/", consumers.FeedbackConsumer.as_asgi()),
]
