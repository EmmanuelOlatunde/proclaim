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
pip install Django channels daphne Pillow segno psutil

./start.sh
# or: python manage.py runserver 0.0.0.0:3000 --noreload
```

`segno` renders the connection QR codes entirely offline (pure Python, its own PNG writer) and
`psutil` ranks the usable network interfaces. Neither is optional in practice: without `segno`
the QR code is unavailable, and without `psutil` the Connect panel falls back to stdlib
enumeration, which still works but cannot name interfaces or demote virtual adapters.

Then:
- On the **laptop**: open `http://127.0.0.1:3000/` — the home page lists every usable LAN
  address with a scannable QR code
- On the **phone**: scan the QR code, or open the shown `http://LAPTOP-IP:3000/control`
- On the **laptop**: open `http://LAPTOP-IP:3000/display/CHURCH1`
- In **vMix**: add the display URL as a Browser source

See [Connecting a phone](#connecting-a-phone) for Wi-Fi vs hotspot setup, and
[Transparent display](#transparent-display) for using the wall as a video overlay.

Room creation is automatic — any room code creates its room on first use.

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
  they come from PyInstaller's own `hook-django.py` probing `<app>.context_processors` for
  every installed app (none of this project's apps define those), plus optional modules like
  `pycparser.lextab`, the unused Oracle DB backend, and the deliberately-excluded
  `channels.testing`. The build still exits 0.
- **EXCEPTION — one warning must NOT appear:** a `"Hidden import 'presentation.templatetags'
  not found"` warning is now a build defect. The project has a real template-tag package
  (`presentation/templatetags/versioned_static.py`, which powers the `{% staticver %}`
  cache-bust on every CSS/JS reference) and it is deliberately listed in `Proclaim.spec`
  `hiddenimports`. If that warning reappears, the tag package was not bundled and the served
  pages will fail to render — `verify_package.bat` catches it via the `?v=` check on the
  `/control` page.

### Ports

Resolution order: `Proclaim.exe --port N` → `PROCLAIM_PORT` env var → default `3000`.

`portable.py` writes the resolved port back into `PROCLAIM_PORT` before starting Daphne, so
the console banner, the Connect panel and the QR code can never disagree about which port is
live. For a request already in flight, the port in `request.META["SERVER_PORT"]` wins — that
is what the phone will actually dial.

The startup banner lists **every** ranked LAN address (marking which one the QR code uses)
rather than guessing a single one, because a machine with Wi-Fi *and* Ethernet — or one whose
phone hotspot lives on a different adapter than the default route — has more than one
plausible answer. See [Connecting a phone](#connecting-a-phone).

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
- **Style tab**: font (default/serif/sans), text size, alignment, text shadow, and background —
  mirroring a live preview before you press Send
- **Background modes** (see [Transparent display](#transparent-display)):
  Normal (solid colour), Image, Transparent (real alpha in the browser), Chroma key
- **Live Preview**: the control page shows a 16:9 mini preview of whatever will hit the display
- **Reconnection**: auto-reconnect WebSocket, state restore on reconnect
- **Offline**: works without Internet — all data local (SQLite + local filesystem)

---

## Connecting a phone

Two ways, both offline. Proclaim works out the addresses itself and shows them on the
home page (`http://127.0.0.1:3000/`) — open that page on the laptop.

### Same Wi-Fi (the usual case)

Start Proclaim, open the home page, and scan the QR code with the phone. The address is the
laptop's Wi-Fi address on the same network. The phone then opens `http://<that-address>:3000/`.

### Phone hotspot (no venue Wi-Fi, no Internet)

The laptop has no Internet requirement at all, which is the point — so a phone hotspot works
even with no data plan:

1. On the phone: **Settings → Personal hotspot / Tethering → ON**. Note the hotspot's address
   range (Android usually `192.168.43.x`, iOS `192.168.2.x`, Windows Mobile hotspot
   `192.168.137.x`).
2. Connect the laptop's Wi-Fi to that hotspot. **Do not** use Ethernet at the same time — it
   usually wins the default route, and Proclaim will print *both* addresses so you can pick.
3. Reload the home page. The hotspot address is now at the top of the list and is what the QR
   code points at.
4. Scan. The phone opens the control page.

### Why several addresses are shown

A modern laptop has a dozen IPv4 interfaces — Wi-Fi, Ethernet, Bluetooth, VPN clients,
Hyper-V/vEthernet, VirtualBox bridges, Docker and WSL veth pairs. "The first non-loopback
address" is a coin flip, and a VirtualBox or Docker address is frequently the one that gets
listed first while being completely unreachable from a phone. So every usable address is
ranked and shown; Wi-Fi and Ethernet come first, virtual adapters last and are labelled
*"phone will not reach this"*. Nothing is silently discarded — only loopback (`127.x`),
unspecified (`0.0.0.0`) and link-local (`169.254.x`) are excluded outright, because those can
never be typed on another device.

The `.local` hostname (`laptop.local`) is listed as a last resort only. mDNS resolution is
unreliable on Windows and often disabled entirely, so it is never what the QR code points at.

### If the phone still cannot connect

Windows Firewall is the usual cause. The home page prints a ready-to-run rule for **your**
port:

```powershell
netsh advfirewall firewall add rule name="Proclaim" dir=in action=allow protocol=TCP localport=3000
```

Allow on **Private networks only**, and do not disable the firewall. See [Firewall](#firewall)
for the Linux equivalents.

### Connection status

The home page shows a live dot: grey/red until a controller connects, green once the phone is
actually talking to Proclaim. It reads an in-memory presence registry, so a green dot means a
real WebSocket is open — not merely that the server is up.

The controller connects on `/ws/control/<room>/` while displays stay on `/ws/room/<room>/`.
Both join the same room group and speak the identical protocol; the separate route exists only
so the Connect panel can tell a phone from a projector. Existing display URLs are unaffected.

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

## Transparent display

Use the wall as a compositing layer over video, instead of an opaque full-screen slide. Two
modes, chosen in the Style tab's **Background mode** row:

| Mode | What the browser paints | Use when |
|---|---|---|
| **Normal** | the chosen solid colour (unchanged default) | the wall is the whole output |
| **Image** | the chosen background image (unchanged) | decorative backdrop |
| **Transparent** | nothing — genuine alpha, all the way up | your capture preserves alpha |
| **Chroma key** | a flat key colour (`#00FF00` by default) | the capture path flattens alpha |

A display can also be pinned per-browser without changing the room:
`/display/CHURCH1?bg=transparent`. That is what lets a dedicated vMix overlay browser stay
transparent while the room the congregation is watching stays normal. Unknown values fall back
to `room`; case and stray whitespace are tolerated, because these URLs get typed by hand.

### The honest caveat about alpha

**A browser can be transparent. A screen capture usually cannot.**

`display.css` really does drive `html`, `body`, `#display`, the blanked stage and an image's
letterbox bars all to `transparent` — there is no opaque surface left anywhere in the frame.
That browser-side half is verified: a transparent-mode display page computed
`rgba(0, 0, 0, 0)` on `html`, `body` and `#display`, and when the page was composited over a
saturated red backdrop in Chromium the backdrop showed through unchanged.

Whether that alpha *reaches vMix* is a separate question, and it depends entirely on how vMix
grabs the browser:

- **A capture that composites the browser's own alpha** (a Browser/OBS-style source) — the
  transparent page should key cleanly. Confirm it on your setup before you rely on it.
- **Screen / Window capture** — these read the framebuffer, which has **no alpha channel at
  all**. The background comes through as black.

That is exactly what Chroma key mode is for: paint the wall a flat green and key it out in
vMix or OBS. It works on every capture method, at the cost of one keyer.

So: try **Transparent** first and check the result against your video. If the background comes
through black or opaque, switch to **Chroma key** — do not expect alpha to survive a framebuffer
capture, and do not disable anything in vMix to try to force it.

### Legibility over video

No scrim, panel or stroke is added behind the text — that would defeat the purpose. Legibility
comes from the existing **Text shadow** setting: "Automatic" already resolves to a dark shadow
for light text, which is what you want over an unknown background.

### Cost

None. Both modes ride the existing `style_change` message. There is no new message type, no
extra round trip, and no polling. Images are never re-encoded to flatten alpha — the pipeline
is untouched, so a PNG that carries its own transparency still composites correctly over the
video underneath instead of sitting in an opaque frame.

### Container notes

Both modes are validated server-side in `normalize_background()`: `transparent` is normalised
to exactly `{"type": "transparent"}` (a stale colour or blur cannot leak into a see-through
frame), and `chroma` accepts only a 6-digit hex, falling back to `#00ff00` on anything
unparseable — a bad value must never be able to paint the wall unreadable.

---

## Firewall

The server listens on `0.0.0.0:3000` (all interfaces) — bound to every adapter on purpose, so
Wi-Fi and Ethernet both work without reconfiguring. Allow inbound TCP port 3000 from the local
network:

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

187 tests covering: scripture parsing (multi-word/numbered books, all translations),
plain-text/ChordPro song import, the two-hymnal import, song search, safe image deletion,
and the full WebSocket protocol (present → minimal `slide_change` → `blank` → reconnection
snapshot) via `channels.testing.WebsocketCommunicator`.

Newer coverage worth knowing about:

- **Transparent / chroma** — both modes over the real socket, the server-side validator as a
  unit, the `?bg=` override (including case/whitespace tolerance and junk rejection), and the
  guarantee that `color`/`image` behaviour is unchanged.
- **Controller route** — `/ws/control/<room>/` speaks the identical protocol, a projector is
  never counted as a phone, and presence is released on disconnect.
- **Networking** — request port beats `PROCLAIM_PORT` beats 3000; virtual adapters are demoted
  but still listed; `.local` never wins the QR code; loopback/link-local never appear; the QR
  is a real PNG and the `netsh` command carries the actual port (not a literal `%d`).
- **Typography** — the type scale stays relative end to end, both pages load the shared design
  system, and no removed per-slide class name survives anywhere.
- **Packaging** — required hidden imports, the `nvx` exclusion, and `segno`/`psutil` are
  declared in the spec and in both install scripts. These are cheap string assertions that
  catch the worst class of regression: a dependency or asset silently unbundled.

> Static assets are read through the `staticfiles` finder rather than fetched over HTTP,
> because the test runner does not serve `/static/` (that is a DEBUG-only, live-server
> behaviour). That also means the assertions hold for the packaged build, which reads the
> same files out of `_internal/`.

---

## Data

All data stored in local SQLite (`db.sqlite3` in dev; `data/proclaim.sqlite3` next to the exe
in the portable build):
- Songs (978 imported hymns + custom), song sections
- Announcements
- Uploaded images (local filesystem under `media/`)
- Rooms and presentation state
- Service queue