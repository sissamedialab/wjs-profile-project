from django.urls import path

from . import consumers

websocket_urlpatterns = [
    path("ws/feedback/<str:feedback_wsname>/", consumers.FeedbackConsumer.as_asgi()),
]
