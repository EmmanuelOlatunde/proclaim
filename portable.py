"""
Proclaim — portable Windows launcher.

PyInstaller entry script (see Proclaim.spec). When the user double-clicks
Proclaim.exe this module:

  1. locates the folder Proclaim.exe lives in (never the working directory),
  2. prepares the writable runtime folders (data/, data/hymns/, media/),
  3. on a first launch copies the bundled seed database/media, so a brand-new
     computer starts with the full imported hymn library,
  4. runs any pending Django migrations,
  5. detects the LAN IP and prints the server banner,
  6. starts the Daphne ASGI server bound to 0.0.0.0 (HTTP + WebSockets).

It is only ever run from Proclaim.exe on Windows, or during development via
``python portable.py`` (the script directory becomes the application
directory). All writable data stays next to the executable, so the whole
Proclaim folder can be copied between computers.
"""

import os
import shutil
import socket
import sys
from pathlib import Path

DEFAULT_PORT = 3000


def app_root():
    """The folder that contains the running application.

    Frozen (Proclaim.exe): the folder the executable lives in.
    Development: PROCLAIM_PORTABLE_ROOT override, else the script's folder.
    """
    if getattr(sys, "frozen", False):
        return Path(sys.executable).resolve().parent
    override = os.environ.get("PROCLAIM_PORTABLE_ROOT")
    if override:
        return Path(override)
    return Path(__file__).resolve().parent


def bundle_dir():
    """Directory holding the bundled (read-only) application files.

    Under PyInstaller this is the _internal folder; in development it is the
    script's folder (where no seed data exists, so nothing is copied).
    """
    meipass = getattr(sys, "_MEIPASS", None)
    if meipass:
        return Path(meipass)
    return Path(__file__).resolve().parent


def detect_lan_ip():
    """The computer's LAN IPv4 address, detected without Internet access.

    Asks the OS which local address the default route would use (no packets
    are ever sent), then falls back to any configured non-loopback address.
    Returns 127.0.0.1 only when nothing better exists.
    """
    ip = ""
    probe = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        probe.connect(("8.8.8.8", 80))
        ip = probe.getsockname()[0]
    except OSError:
        ip = ""
    finally:
        probe.close()
    if ip and not ip.startswith(("127.", "169.254.")):
        return ip
    try:
        for info in socket.getaddrinfo(socket.gethostname(), None, socket.AF_INET):
            cand = info[4][0]
            if not cand.startswith(("127.", "169.254.")):
                return cand
    except OSError:
        pass
    return "127.0.0.1"


def seed_runtime(root):
    """Create the writable folders and seed a fresh computer.

    On the very first launch (no data/proclaim.sqlite3 yet) the bundled seed
    database — a snapshot of the imported hymn library — is copied into data/,
    and bundled media files are copied into media/ only if absent. Existing
    databases and media files are never touched or overwritten.
    """
    data_dir = root / "data"
    media_dir = root / "media"
    (data_dir / "hymns").mkdir(parents=True, exist_ok=True)
    media_dir.mkdir(parents=True, exist_ok=True)

    db_file = data_dir / "proclaim.sqlite3"
    first_launch = not db_file.exists()
    if not first_launch:
        return False
    # The bundled seed database is copied into the runtime data/ folder under
    # the runtime name. Look under either filename PyInstaller may have kept
    # (the spec bundles it as "db.sqlite3" under the seed/ destination).
    seed_db = bundle_dir() / "seed" / "proclaim.sqlite3"
    if not seed_db.exists():
        seed_db = bundle_dir() / "seed" / "db.sqlite3"
    if seed_db.exists():
        shutil.copy2(seed_db, db_file)
    seed_media = bundle_dir() / "seed" / "media"
    if seed_media.is_dir():
        for src in seed_media.rglob("*"):
            if src.is_file():
                dst = media_dir / src.relative_to(seed_media)
                dst.parent.mkdir(parents=True, exist_ok=True)
                if not dst.exists():
                    shutil.copy2(src, dst)
    return True


def resolve_port():
    """Port order: `Proclaim.exe --port N`, PROCLAIM_PORT, default 3000."""
    args = sys.argv[1:]
    for i, arg in enumerate(args):
        if arg == "--port":
            try:
                return int(args[i + 1])
            except (IndexError, ValueError):
                break
    try:
        return int(os.environ.get("PROCLAIM_PORT", str(DEFAULT_PORT)))
    except ValueError:
        return DEFAULT_PORT


def assert_port_free(port):
    """Fail early with a clear message when the port is already taken."""
    probe = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    try:
        probe.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        probe.bind(("0.0.0.0", port))
        probe.close()
    except OSError as exc:
        raise RuntimeError(
            f"Port {port} is already in use by another program ({exc}). "
            f"Close that program, or start Proclaim on another port: "
            f"Proclaim.exe --port 4000"
        ) from exc


def print_banner(ip, port, first_launch):
    width = 76
    line = "=" * width
    print(line)
    print("  Proclaim — Local Presentation Server")
    print(line)
    print("")
    if first_launch:
        print("  First launch: a fresh application database was created.")
        print("  The bundled hymn library was installed into data/.")
        print("")
    print("  Server: RUNNING")
    print("")
    print(f"  Local:   http://127.0.0.1:{port}/")
    print(f"  LAN:     http://{ip}:{port}/")
    print("")
    print("  Control (phone / laptop):")
    print(f"  http://{ip}:{port}/control")
    print("")
    print("  Display (laptop / projector source):")
    print(f"  http://{ip}:{port}/display/CHURCH1")
    print("")
    print("  Press Ctrl+C to stop.")
    print(line)
    print("")


def run():
    root = app_root()
    os.environ["PROCLAIM_PORTABLE_ROOT"] = str(root)
    os.environ.setdefault("DJANGO_SETTINGS_MODULE", "churchcast.settings")

    # Force autobahn onto its pure-Python implementations. Its optional "nvx"
    # native UTF-8 accelerator is excluded from the bundle (see Proclaim.spec):
    # its CFFI modules are built at import time from .c source that PyInstaller
    # does not ship, which used to crash django.setup() with an
    # "nvx/_utf8validator.c not found" error. Auto-disable guards the packaged
    # build even if a future autobahn wheel ships the extensions differently.
    os.environ["AUTOBAHN_USE_NVX"] = "0"

    assert_port_free(resolve_port())
    first_launch = seed_runtime(root)
    port = resolve_port()

    import django
    django.setup()

    from django.core.management import call_command
    call_command("migrate", interactive=False, verbosity=1)

    ip = detect_lan_ip()
    print_banner(ip, port, first_launch)

    # Start Daphne exactly as the `daphne` command would (no twistd, so no
    # Twisted plugin discovery is involved). Serves both HTTP and WebSockets.
    from daphne.cli import CommandLineInterface
    CommandLineInterface().run(
        [
            "-b", "0.0.0.0",
            "-p", str(port),
            "--no-server-name",
            "churchcast.asgi:application",
        ]
    )
    print("Proclaim stopped. You can close this window.")


def main():
    try:
        run()
    except KeyboardInterrupt:
        print("\nProclaim stopped. You can close this window.")
    except SystemExit:
        raise
    except Exception as exc:  # noqa: BLE001 — keep the window open on failure
        width = 72
        print("=" * width)
        print("PROCLAIM STARTUP ERROR")
        print("=" * width)
        print("")
        print("Unable to start Proclaim.")
        print("")
        print(f"Reason: {exc}")
        print("")
        import traceback
        traceback.print_exc()
        print("=" * width)
        print("Please keep this window open when reporting the problem.")
        try:
            input("Press Enter to close this window.")
        except EOFError:
            pass
        sys.exit(1)


if __name__ == "__main__":
    main()