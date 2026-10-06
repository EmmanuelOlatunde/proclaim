"""
ASGI config for churchcast — Channels WebSocket support.
"""
import os
from django.core.asgi import get_asgi_application
from django.urls import Resolver404

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "churchcast.settings")

django_asgi_app = get_asgi_application()

from presentation.routing import websocket_urlpatterns
from channels.routing import ProtocolTypeRouter, URLRouter

_UNROUTED = "No route found for path"


class RejectUnroutedWebSocket:
    """Answer an unrouted WebSocket path with a clean rejection, not a 500.

    ``URLRouter`` signals "nothing matched" by raising, and Daphne turns any
    exception raised during the handshake into HTTP 500 plus a logged
    traceback. A phone handed ``/ws/control/CHURCH1`` — one character short of
    the real route ``/ws/control/CHURCH1/`` — therefore produced an internal
    server error for what is really a typo'd URL.

    Sending ``websocket.close`` while the handshake is still open instead makes
    Daphne reject the connection without a traceback. Only the router's own
    no-route message is caught; anything raised from inside a matched consumer
    keeps propagating, so genuine failures still surface as errors.
    """

    def __init__(self, router):
        self.router = router

    async def __call__(self, scope, receive, send):
        try:
            return await self.router(scope, receive, send)
        except (Resolver404, ValueError) as exc:
            if scope.get("type") != "websocket" or not str(exc).startswith(
                _UNROUTED
            ):
                raise
        await send({"type": "websocket.close", "code": 1008})


application = ProtocolTypeRouter(
    {
        "http": django_asgi_app,
        "websocket": RejectUnroutedWebSocket(URLRouter(websocket_urlpatterns)),
    }
)
