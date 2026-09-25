# Proclaim — Local Church Presentation System

A local-first, LAN-only presentation and lyrics-casting web application for church services.
No cloud, no Internet required during live use.

There are **two ways to get Proclaim**:

| Artifact | What it is | Who it's for |
|---|---|---|
| **`Proclaim.zip`** | The **final, prebuilt Windows app** — the output of the bundle. Just run `Proclaim.exe`. Download it from the latest **Release** below. | End users (no Python/installation needed) |
| **`pro.zip`** | The **clean Windows build source** — everything needed to re-bundle, without dev artifacts. Committed in this repo. | Developers who want to build/change Proclaim |

> **Releases:** every published version of the ready-to-run app is attached to a
> [GitHub Release](https://github.com/EmmanuelOlatunde/proclaim/releases).
> The newest one is always at:
> `https://github.com/EmmanuelOlatunde/proclaim/releases/latest/download/Proclaim.zip`
> To publish an update, rebuild on Windows (`build_windows.bat`), zip `dist\Proclaim\`
> into `Proclaim.zip`, and upload it as a new Release asset.

---

## Run it (end users — Windows 10/11 x64)

1. Download **`Proclaim.zip`** from the
   [latest Release](https://github.com/EmmanuelOlatunde/proclaim/releases/latest/download/Proclaim.zip)
   and extract it anywhere (e.g. Desktop or USB stick).
2. Double-click **`Proclaim.exe`**. A console window opens showing the server banner and the
   web addresses to open. Windows Firewall will ask to allow network access — click **Allow**.
3. Make sure the phone and laptop are on the **same Wi-Fi network**:
   - **Phone** (controller): open `http://<IP>:3000/control`
   - **Laptop** (display): open `http://<IP>:3000/display/CHURCH1`
   - **vMix** (optional): add the display URL as a Browser/Web source
   - The IP your laptop should type is printed right in the console (`LAN: http://…`).
4. Close the console window to stop the server (`Ctrl+C` stops it cleanly).

**Important notes:**

- **No installation.** Proclaim is self-contained — it does not need Python, pip, or anything else.
- **Data lives next to the exe.** On the first launch Proclaim creates a `data/` folder
  (`data/proclaim.sqlite3` – your songs, rooms, queue) and a `media/` folder (uploaded images)
  **next to `Proclaim.exe`** and seeds them with the bundled hymn library. Keep the whole folder
  together and copy it elsewhere anytime — everything travels with the folder and is never
  overwritten or reset on later launches.
- **Port.** Default is `3000`. If it's taken, start with `Proclaim.exe --port 4000`
  (or set the `PROCLAIM_PORT` environment variable).
- The same instructions ship inside the zip as `README-PORTABLE.txt`.

---

## Quick Start (developers — from source)

```bash
# Linux/macOS/most dev machines
python3 -m venv .venv
source .venv/bin/activate          # Windows: .venv\Scripts\activate
pip install Django channels daphne Pillow

./start.sh
# or: python manage.py runserver 0.0.0.0:3000 --noreload
```

Then:
- On the **phone**: open `http://LAPTOP-IP:3000/control`
- On the **laptop**: open `http://LAPTOP-IP:3000/display/CHURCH1`
- In **vMix**: add the display URL as a Browser source

`start.sh` prints your detected LAN address automatically. Room creation is automatic —
any room code creates its room on first use.

> Note: the Django project package is still named `churchcast` (legacy name); the product is **Proclaim**.

---

## Portable Windows build — how it works (for developers)

Proclaim is packaged as a **PyInstaller `--onedir`** application. The build entry point is
`portable.py`; the bundle definition is `Proclaim.spec`.

### Build prerequisites

- A **Windows 10/11 x64** machine (the packaged app itself runs on any Windows 10/11 x64 PC).
- **Python 3.12 or 3.13** for the build (PyInstaller 6.x support; build Windows from a Python 3.12/3.13 interpreter — the 3.14.x dev venv is not recommended for PyInstaller on Windows).
- No other system tools: `build_windows.bat` creates its own build venv and installs
  `Django`, `channels`, `daphne`, `Pillow`, and `pyinstaller` automatically.

### The one-command build

```bat
rem On Windows, in the project folder:
build_windows.bat
```

This produces **`dist\Proclaim\`** — the complete runnable app (equivalent to the contents
of `Proclaim.zip`) — and copies `README-PORTABLE.txt` + `verify_package.bat` inside it.

### Self-test your bundle

```bat
rem After building:
dist\Proclaim\verify_package.bat
```

The script copies the bundle to a temp folder, starts `Proclaim.exe --port 3999`,
checks every key page/API returns HTTP 200, then cleans up. It prints `SELF-TEST PASSED`
on success. (It will close any running Proclaim.exe first.)

### The portable runtime model (important to understand)

- **Read-only seed** is bundled inside `_internal/seed/`:
  - `_internal/seed/db.sqlite3` — a snapshot of the current `db.sqlite3` (the 978 imported
    hymns, songs, rooms) taken **at build time**. Rebuilding automatically re-snapshots it,
    so new songs/imports ship with the next build.
  - `_internal/seed/media/` — the current `media/` folder.
  - Templates, static files, and scripture/hymn JSONs are bundled at their exact on-tree
    positions so every `__file__`-relative path in the code keeps resolving.
- **Writable runtime** lives next to the executable:
  - `data/proclaim.sqlite3`, `data/hymns/`, `media/`.
- On the **first launch** only, `portable.py` (`seed_runtime`) copies the seed database and
  media into `data/`+`media/`, then runs `migrate`. Existing user data is **never overwritten**.
- `churchcast/settings.py` reads `PROCLAIM_PORTABLE_ROOT` (set by `portable.py`) to switch
  the database/media paths to the runtime folders; without it, development mode uses the
  plain `db.sqlite3`/`media/` at the project root.

### Gotchas baked into the spec (don't "fix" these away)

- **Daphne is started programmatically** via `daphne.cli.CommandLineInterface().run([...])`
  in `portable.py` — deliberately *not* `twistd` (avoids Twisted plugin discovery).
- **`churchcast.asgi` is a hidden import.** PyInstaller's bundled `hook-django.py` only
  auto-adds `<pkg>.wsgi`, and `portable.py` hands Daphne the app *by name* as a string
  (`"churchcast.asgi:application"`), which static analysis cannot see. Without
  `hiddenimports=["churchcast.asgi"]` the packaged app dies with
  `ModuleNotFoundError: No module named 'churchcast.asgi'`.
- **autobahn's native `nvx` accelerator is excluded** (`autobahn.nvx`, `_nvx_utf8validator`,
  `_nvx_xormasker`), and `portable.py` sets `AUTOBAHN_USE_NVX=0`. Its CFFI modules compile
  `autobahn/nvx/*.c` at import time, and PyInstaller does not bundle `.c` files — leaving it
  in caused `FileNotFoundError: …\autobahn\nvx\_utf8validator.c` on startup. Excluding it
  makes autobahn use its bundled pure-Python UTF-8 validator.
- **`DEBUG=True` and `ALLOWED_HOSTS=["*"]` are intentional.** This is a LAN-trust-boundary
  app; Django's `static()` media serving and the `staticfiles_urlpatterns()` in
  `churchcast/urls.py` only work in DEBUG mode (Daphne, unlike `runserver`, does not wire
  static files up automatically). Do not expose the app to the public Internet.
- The `"Hidden import … not found"` warnings during the build are **expected and harmless** —
  they come from PyInstaller's own `hook-django.py` probing `<app>.templatetags` and
  `<app>.context_processors` for every installed app (none of this project's apps define
  them), plus optional modules like `pycparser.lextab`, the unused Oracle DB backend, and
  the deliberately-excluded `channels.testing`. The build still exits 0.

### Ports

Resolution order: `Proclaim.exe --port N` → `PROCLAIM_PORT` env var → default `3000`.

---

## Architecture

```
PHONE (controller) → Wi-Fi → LAPTOP (server + display) → vMix → projector/stream
```

- **Laptop** runs the server (Django + Channels/Daphne) and displays the presentation in a browser.
- **Phone** opens the control page and sends commands over a local WebSocket.
- **vMix** captures the laptop's display browser as a Browser/Web source.
- All data stays on the local network. No cloud realtime, no cloud storage.

---

## Features

- **Songs**: create, edit, delete, search; section-based navigation (Verse/Chorus/Bridge)
- **Hymn Library**: full **Yoruba Baptist Hymnal + New Nigerian Baptist Hymnal** imported
  (978 songs), idempotent via `python manage.py import_hymnals` (`presentation/song_import.py`)
- **Scripture**: completely offline — 8 bundled translations, each parsed flexibly
  (`John 3:16`, `Genesis 1:1-5`, `Psalm 23`, `1 John 1:9`, `Song of Solomon 2:1`, `Jn 3:16`):
  - KJV (`presentation/data/kjv.json`, 66 books / 31,102 verses)
  - Yoruba, Hausa, Igbo Contemporary Bibles + World English, American Standard, Darby,
    Douay-Rheims 1899, Young's Literal translations
  - A scripture picker (`presentation/static/presentation/js/scripture-picker.js`)
- **Song Import**: paste plain text or ChordPro lyrics; auto-parses into sections
- **Images**: upload, browse, search, present full-canvas; safe delete (refuses to delete
  an image that is currently in use)
- **Announcements**: title + body text slides
- **Countdown Timer**: timestamp-synced (no per-second network traffic)
- **Blank Screen**: black out display while preserving position
- **Service Queue**: order items, reorder, jump to any item
- **Style tab**: font (default/serif/sans), text size, and background color/image — mirroring
  a live preview before you press Send
- **Live Preview**: the control page shows a 16:9 mini preview of whatever will hit the display
- **Reconnection**: auto-reconnect WebSocket, state restore on reconnect
- **Offline**: works without Internet — all data local (SQLite + local filesystem)

---

## Network Efficiency

Three small message types keep slide changes cheap over Wi-Fi:

- `{"type": "state", ...}` — full snapshot. Sent once when content changes
  (present/queue-jump/clear/stop) and on (re)connect.
- `{"type": "slide_change", "contentType", "itemId", "slideIndex", "slideCount"}`
  — minimal navigation for next/prev/goto_slide. No content is retransmitted.
- `{"type": "blank", "blank": bool}` — minimal blank toggle; position preserved.

Countdowns sync by absolute `endTime` only — no per-second messages. No polling anywhere.
Images load once and are cached by the browser.

---

## Firewall

The server listens on `0.0.0.0:3000` (all interfaces). Allow inbound TCP port 3000 from the
local network:

**Windows:**
```powershell
netsh advfirewall firewall add rule name="Proclaim" dir=in action=allow protocol=TCP localport=3000
```

**Linux (ufw):**
```bash
sudo ufw allow 3000/tcp
```

**Linux (firewalld):**
```bash
sudo firewall-cmd --add-port=3000/tcp --permanent
sudo firewall-cmd --reload
```

Do NOT expose Proclaim to the public Internet — it is LAN-only by design.

---

## Tests

```bash
.venv/bin/python manage.py test presentation
```

56 tests covering: scripture parsing (multi-word/numbered books, all translations),
plain-text/ChordPro song import, the two-hymnal import, song search, safe image deletion,
and the full WebSocket protocol (present → minimal `slide_change` → `blank` → reconnection
snapshot) via `channels.testing.WebsocketCommunicator`.

---

## Data

All data stored in local SQLite (`db.sqlite3` in dev; `data/proclaim.sqlite3` next to the exe
in the portable build):
- Songs (978 imported hymns + custom), song sections
- Announcements
- Uploaded images (local filesystem under `media/`)
- Rooms and presentation state
- Service queue