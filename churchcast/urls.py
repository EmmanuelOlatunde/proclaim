"""Root URL configuration."""
from django.urls import path, include
from django.conf import settings
from django.conf.urls.static import static

urlpatterns = [
    path("", include("presentation.urls")),
] + static(settings.MEDIA_URL, document_root=settings.MEDIA_ROOT)

if settings.DEBUG:
    # Serve app static files exactly as `runserver` already does. Required so
    # the static files load when the server is started directly with Daphne
    # (the packaged Proclaim.exe), where there is no runserver command to wire
    # static file serving automatically.
    from django.contrib.staticfiles.urls import staticfiles_urlpatterns
    urlpatterns += staticfiles_urlpatterns()
