"""
URL routes — pages + REST API.
"""
from django.urls import path
from . import views

urlpatterns = [
    # Pages
    path("", views.index_page, name="index"),
    path("control", views.control_page, name="control"),
    path("display/<str:room_code>", views.display_page, name="display"),

    # REST API — Rooms & Queue
    path("api/rooms", views.room_list, name="room_list"),
    path("api/rooms/<str:code>/queue", views.queue_manage, name="queue_manage"),

    # REST API — Songs
    path("api/songs", views.song_list, name="song_list"),
    path("api/songs/save", views.song_create_or_update, name="song_save"),
    path("api/songs/import", views.song_import, name="song_import"),
    path("api/songs/<int:song_id>", views.song_detail, name="song_detail"),
    path("api/songs/<int:song_id>/delete", views.song_delete, name="song_delete"),

    # REST API — Announcements
    path("api/announcements", views.announcement_list, name="announcement_list"),
    path("api/announcements/save", views.announcement_create_or_update, name="announcement_save"),
    path("api/announcements/<int:ann_id>", views.announcement_detail, name="announcement_detail"),
    path("api/announcements/<int:ann_id>/delete", views.announcement_delete, name="announcement_delete"),

    # REST API — Images
    path("api/images", views.image_list, name="image_list"),
    path("api/images/upload", views.image_upload, name="image_upload"),
    path("api/images/<int:image_id>", views.image_detail, name="image_detail"),

    # REST API — Scripture
    path("api/scripture/translations", views.scripture_translations, name="scripture_translations"),
    path("api/scripture/parse", views.scripture_parse, name="scripture_parse"),
    path("api/scripture/books", views.scripture_books, name="scripture_books"),
    path("api/scripture/index", views.scripture_index, name="scripture_index"),
    path("api/scripture/chapter", views.scripture_chapter, name="scripture_chapter"),
]
