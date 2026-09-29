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