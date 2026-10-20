"""
Template tag ``{% staticver 'path/to/file' %}`` — static URL with an automatic
cache-busting query: ``<url>?v=<source-file-mtime>``.

The version comes from the asset on disk, resolved through the staticfiles
finder. In development that is the real file in presentation/static, so any
edit changes the URL and browsers re-fetch. In the portable build PyInstaller's
onedir layout keeps each bundled file's build-time mtime inside ``_internal``,
so the version is stable for a given build and changes exactly when a rebuild
happens. If the file cannot be resolved the plain (unversioned) URL is
returned, so a broken lookup can never take a page down.
"""
import os

from django import template
from django.contrib.staticfiles import finders
from django.templatetags.static import static

register = template.Library()

# Runtime assets that make up the whole-app build stamp. Any edit to any of
# them means a long-open tab is running stale code; the stamp is embedded in
# the page (<meta name="proclaim-build">) and echoed by the WebSocket connect
# snapshot so the client can detect staleness without polling.
_BUILD_ASSETS = (
    "presentation/js/control.js",
    "presentation/js/display.js",
    "presentation/js/scripture-picker.js",
    "presentation/js/song-slides.js",
    "presentation/css/app.css",
    "presentation/css/display.css",
    "presentation/css/presentation.css",
)


def build_stamp():
    """Compact stamp of the runtime JS/CSS: the newest mtime of the set.

    Any edited asset gets the current time, so the max rises exactly when
    something changed — in dev on every save, in the portable build once per
    rebuild (bundled mtimes are stable per build). Empty string if nothing
    resolves (never raise: this rides the connect path).
    """
    stamps = [s for s in (_mtime_stamp(p) for p in _BUILD_ASSETS) if s is not None]
    return str(max(stamps)) if stamps else ""


@register.simple_tag
def buildver():
    return build_stamp()


@register.simple_tag
def staticver(path):
    url = static(path)
    stamp = _mtime_stamp(path)
    if stamp is None:
        return url
    sep = "&amp;" if "?" in url else "?"
    return "%s%sv=%d" % (url, sep, stamp)


def _mtime_stamp(path):
    try:
        found = finders.find(path)
        if not found:
            return None
        return int(os.path.getmtime(found))
    except OSError:
        return None