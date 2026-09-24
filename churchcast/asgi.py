"""
ASGI config for churchcast — Channels WebSocket support.
"""
import os
from django.core.asgi import get_asgi_application

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "churchcast.settings")

django_asgi_app = get_asgi_application()

from presentation.routing import websocket_urlpatterns
from channels.routing import ProtocolTypeRouter, URLRouter

application = ProtocolTypeRouter(
    {
        "http": django_asgi_app,
        "websocket": URLRouter(websocket_urlpatterns),
    }
)
