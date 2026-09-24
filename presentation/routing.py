"""WebSocket URL routing."""
from django.urls import path
from . import consumer

websocket_urlpatterns = [
    path("ws/room/<str:room_code>/", consumer.RoomConsumer.as_asgi()),
]
