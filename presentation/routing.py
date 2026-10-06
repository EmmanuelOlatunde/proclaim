"""WebSocket URL routing.

Two routes reach the same consumer and the same room group, so the message
protocol is identical on both:

  ws/room/<code>/      displays (the wall, and any late reconnecting display)
  ws/control/<code>/   the phone / laptop controller

The only difference is the `role` passed to the consumer, which lets the
Connect panel report "phone connected" without adding anything to the wire
protocol or to the display URLs operators already have bookmarked.
"""
from django.urls import path
from . import consumer

websocket_urlpatterns = [
    path("ws/room/<str:room_code>/", consumer.RoomConsumer.as_asgi(role="display")),
    path("ws/control/<str:room_code>/", consumer.RoomConsumer.as_asgi(role="control")),
]