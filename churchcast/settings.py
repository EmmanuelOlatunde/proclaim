"""
Django settings for churchcast — local-first LAN presentation system.
"""
import os
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent

# Portable (packaged) mode: PROCLAIM_PORTABLE_ROOT is set only by Proclaim.exe
# (see portable.py) and points at the folder that contains the executable.
# Writable runtime data then lives next to the executable (data/ and media/),
# so the whole Proclaim folder can be moved between computers. When the
# variable is unset (normal development) nothing changes about where data is
# stored — the database and media stay under BASE_DIR as before.
PORTABLE_ROOT = os.environ.get("PROCLAIM_PORTABLE_ROOT")
if PORTABLE_ROOT:
    RUNTIME_DIR = Path(PORTABLE_ROOT)
    DB_NAME = RUNTIME_DIR / "data" / "proclaim.sqlite3"
    MEDIA_ROOT = RUNTIME_DIR / "media"
else:
    RUNTIME_DIR = BASE_DIR
    DB_NAME = BASE_DIR / "db.sqlite3"
    MEDIA_ROOT = BASE_DIR / "media"

SECRET_KEY = "django-insecure-local-churchcast-dev-key-change-in-prod"
DEBUG = True
ALLOWED_HOSTS = ["*"]

INSTALLED_APPS = [
    "daphne",
    "channels",
    "django.contrib.staticfiles",
    "presentation",
]

MIDDLEWARE = [
    "django.middleware.common.CommonMiddleware",
]

ROOT_URLCONF = "churchcast.urls"

TEMPLATES = [
    {
        "BACKEND": "django.template.backends.django.DjangoTemplates",
        "DIRS": [BASE_DIR / "presentation" / "templates"],
        "APP_DIRS": True,
        "OPTIONS": {"context_processors": []},
    },
]

WSGI_APPLICATION = "churchcast.wsgi.application"
ASGI_APPLICATION = "churchcast.asgi.application"

DATABASES = {
    "default": {
        "ENGINE": "django.db.backends.sqlite3",
        "NAME": DB_NAME,
    }
}

STATIC_URL = "/static/"
STATIC_ROOT = BASE_DIR / "staticfiles"
STATICFILES_DIRS = [BASE_DIR / "presentation" / "static"]

MEDIA_URL = "/media/"
# MEDIA_ROOT is defined at the top of this file (dev: <project>/media,
# portable: <Proclaim folder>/media).

CHANNEL_LAYERS = {
    "default": {"BACKEND": "channels.layers.InMemoryChannelLayer"},
}

DEFAULT_AUTO_FIELD = "django.db.models.BigAutoField"
USE_TZ = True
TIME_ZONE = "UTC"
