"""
Django settings for churchcast — local-first LAN presentation system.
"""
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent

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
        "NAME": BASE_DIR / "db.sqlite3",
    }
}

STATIC_URL = "/static/"
STATIC_ROOT = BASE_DIR / "staticfiles"
STATICFILES_DIRS = [BASE_DIR / "presentation" / "static"]

MEDIA_URL = "/media/"
MEDIA_ROOT = BASE_DIR / "media"

CHANNEL_LAYERS = {
    "default": {"BACKEND": "channels.layers.InMemoryChannelLayer"},
}

DEFAULT_AUTO_FIELD = "django.db.models.BigAutoField"
USE_TZ = True
TIME_ZONE = "UTC"
