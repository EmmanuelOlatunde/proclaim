# -*- mode: python ; coding: utf-8 -*-
"""
Proclaim portable build (Windows 10/11 x64, PyInstaller --onedir).

Entry script:  portable.py   (console launcher: seeds runtime data, runs
                              migrations, prints the banner, starts Daphne)
Output:        dist/Proclaim/Proclaim.exe  +  dist/Proclaim/_internal/
"""

a = Analysis(
    ["portable.py"],
    pathex=["."],
    binaries=[],
    datas=[
        # Templates, static files and scripture JSONs are bundled at their
        # exact on-tree positions so every __file__-relative path in the
        # existing application code keeps resolving inside _internal/.
        ("presentation/templates", "presentation/templates"),
        ("presentation/static", "presentation/static"),
        ("presentation/data", "presentation/data"),
        # Read-only first-run seed: a snapshot of today's database (the 975
        # imported hymns, songs, rooms) plus uploaded media. portable.py
        # copies these into the writable data//media/ folders only on the
        # first launch of a fresh computer and never overwrites anything.
        ("db.sqlite3", "seed"),
        ("media", "seed/media"),
    ],
    hiddenimports=[
        # The app's ASGI application module is passed to Daphne BY NAME as a
        # string ("churchcast.asgi:application"), so static analysis never sees
        # an import of it. PyInstaller's bundled hook-django.py also only adds
        # <package>.wsgi (not .asgi) for the detected project. Without this the
        # packaged app dies at launch with:
        #   ModuleNotFoundError: No module named 'churchcast.asgi'
        "churchcast.asgi",
        # Daphne ASGI server, started programmatically from portable.py.
        "daphne",
        "daphne.cli",
        "daphne.server",
        "daphne.http_protocol",
        "daphne.ws_protocol",
        "daphne.utils",
        "daphne.access",
        # Twisted pieces daphne imports (serverFromString, HTTP, web headers,
        # policies, logging) so the packaged server can actually serve
        # requests rather than just launching.
        "twisted.internet.endpoints",
        "twisted.internet.asyncioreactor",
        "twisted.web.http",
        "twisted.web.http_headers",
        "twisted.protocols.policies",
        "twisted.logger",
        "zope.interface",
        "autobahn.twisted.websocket",
        # Django management commands invoked BY NAME at startup (invisible to
        # PyInstaller's static analysis).
        "django.core.management.commands.migrate",
        "django.core.management.commands.check",
    ],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[
        "tkinter",
        "channels.testing",   # test-only, must not ship
        "daphne.testing",
        "presentation.tests",
        "numpy",
        "matplotlib",
        # autobahn's optional native UTF-8/XOR accelerator (nvx). Its CFFI
        # modules are compiled at import time from C source (the .c files in
        # autobahn/nvx/) that PyInstaller does not bundle, so importing nvx in
        # the packaged app raises FileNotFoundError during django.setup().
        # Excluding nvx and the top-level _nvx_* probe modules makes autobahn's
        # HAS_NVX probe fail cleanly and the code falls back to the bundled
        # pure-Python UTF-8 validator (see websocket/utf8validator.py).
        "autobahn.nvx",
        "_nvx_utf8validator",
        "_nvx_xormasker",
    ],
    noarchive=False,
)
pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,          # onedir: binaries/data go to _internal/
    name="Proclaim",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,                      # avoid UPX issues with twisted/pillow
    console=True,                   # visible terminal required (see portable.py)
)
coll = COLLECT(
    exe,
    a.binaries,
    a.datas,
    strip=False,
    upx=False,
    name="Proclaim",
)