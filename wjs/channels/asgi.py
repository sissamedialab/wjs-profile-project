"""
ASGI config for yakunin_service project.

It exposes the ASGI callable as a module-level variable named ``application``.

For more information on this file, see
https://docs.djangoproject.com/en/stable/howto/deployment/asgi/
"""

import os

from channels.auth import AuthMiddlewareStack
from channels.routing import ProtocolTypeRouter, URLRouter
from channels.security.websocket import AllowedHostsOriginValidator
from django.core.asgi import get_asgi_application
from utils import load_janeway_settings

# early init for django app
os.environ.setdefault("JANEWAY_SETTINGS_MODULE", "core.settings")
load_janeway_settings()
django_app = get_asgi_application()

from . import routing  # noqa: E402

application = ProtocolTypeRouter(
    {
        "http": django_app,
        "websocket": AllowedHostsOriginValidator(
            AuthMiddlewareStack(
                URLRouter(
                    routing.websocket_urlpatterns,
                ),
            ),
        ),
    },
)
