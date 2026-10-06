"""
Which address can the operator's phone actually reach?

Proclaim is a trusted, LAN-only application: nothing here ever leaves the
local network and nothing here needs the Internet. This module works out the
addresses a phone on the same Wi-Fi (or on the phone's own hotspot) could use
to open Proclaim, and renders a QR code for one of those URLs — all offline.

Why not hard-code an address
============================
Hotspot addressing is not standardised. Android hands out 192.168.43.x on many
versions, 192.168.x.x with a DHCP range on others, iOS Personal Hotspot uses
192.168.2.x, and Windows "Mobile hotspot" hands out 192.168.137.x. A hard-coded
guess is wrong on some of those, so the address is always discovered.

How addresses are ranked
========================
A modern laptop carries a dozen IPv4 interfaces: Wi-Fi, Ethernet, Bluetooth
Network Connection, VPN clients, Hyper-V / vEthernet, VirtualBox bridges,
Docker and WSL veth pairs. Picking "the first non-loopback address" is a coin
flip — a VirtualBox or Docker address is often listed first and is unreachable
from a phone.

So every usable IPv4 address is enumerated and *ranked*:

  1. Wi-Fi and Ethernet interfaces (what a phone shares a network with),
  2. anything else that is a real private LAN address,
  3. virtual / VPN / container interfaces last, flagged as unlikely.

Nothing is ever silently discarded: the caller shows every candidate so the
operator can pick the one that matches the network they actually joined. Only
loopback (127.x), unspecified (0.0.0.0) and link-local (169.254.x) addresses are
excluded outright, because those can never be typed on another device.

Interface names come from psutil when it is installed (accurate across
Windows/Linux/macOS). psutil is optional: without it the module falls back to
the standard library, which is less precise but still correct for the common
"one Wi-Fi adapter" case, so Proclaim never fails to start over it.
"""
import io
import os
import re
import socket

# The port Proclaim uses when neither --port nor PROCLAIM_PORT says otherwise.
# Kept in step with portable.DEFAULT_PORT.
DEFAULT_PORT = 3000

# RFC 5737 TEST-NET-1. Connecting a UDP socket does NOT send a packet — it only
# asks the OS which local address it would use for that destination, i.e. which
# interface owns the default route. Using a documentation address (rather than
# a real public one) makes it obvious that nothing is ever transmitted and that
# no DNS lookup happens.
_ROUTE_PROBE = ("192.0.2.1", 9)

_LOOPBACK = re.compile(r"^127\.")
_LINK_LOCAL_V4 = re.compile(r"^169\.254\.")

# Interface-name fragments that mean "this is not a network a phone shares with
# the laptop": container bridges, hypervisor switches, VPN and tunnel clients,
# Bluetooth PAN, and the loopback pseudo-interface Windows always installs.
_VIRTUAL_NAME_HINTS = (
    "loopback", "pseudo", "bluetooth", "docker", "veth", "vbox", "vboxnet",
    "vmware", "vmnet", "virtualbox", "virbr", "hyper-v", "vethernet",
    "tap", "tun", "tailscale", "zerotier",
    "hamachi", "radmin", "openvpn", "wireguard", "wsl", "npcap", "teredo",
    "isatap", "ppp", "l2tp", "pptp", "sstp", "wintun",
    # Linux container/hypervisor plumbing: docker0 and the br-<id> bridges it
    # creates, libvirt/vagrant bridges, lxd, CNI and flannel pod networks.
    "br-", "br0", "vnet", "lxc", "lxdbr", "cni", "flannel", "kube",
)
# ...and fragments that positively identify the two interface families a phone
# can join. Windows uses "Wi-Fi"/"Ethernet", Linux "wlp"/"enp"/"eth", macOS
# "en0"/"en1".
_WIRELESS_NAME_HINTS = (
    "wi-fi", "wifi", "wlan", "wlp", "wlan0", "wl", "wireless", "airport",
    "wlannet", "atheros", "intel centrino", "broadcom", "realtek",
)
_WIRED_NAME_HINTS = (
    "ethernet", "eth", "enp", "en0", "en1", "eno", "ens", "enx", "local area",
    "i217", "i219", "i225", "e1000", "e1000e", "netwtw", "netcfg",
)

# RFC 1918 private ranges only. A phone on a local network always gets one of
# these; anything else (a routable public address) is still listed but never
# preferred.
_PRIVATE_V4 = (
    re.compile(r"^10\."),
    re.compile(r"^192\.168\."),
    re.compile(r"^172\.(1[6-9]|2\d|3[01])\."),
)


def is_loopback(ip):
    """True for 127.x and ::1 — addresses no other device can ever reach."""
    return bool(ip) and (ip.startswith("127.") or ip == "::1")


def is_link_local(ip):
    """169.254.x / fe80:: — self-assigned, never routable off the host."""
    return bool(ip) and (_LINK_LOCAL_V4.match(ip) or ip.lower().startswith("fe80:"))


def is_unspecified(ip):
    """0.0.0.0 / :: — a bind address, not a destination."""
    return ip in ("0.0.0.0", "::")


def is_private(ip):
    return any(p.match(ip) for p in _PRIVATE_V4)


def is_virtual_name(name):
    low = (name or "").lower()
    return any(h in low for h in _VIRTUAL_NAME_HINTS)


def classify_interface(name):
    """Return one of "wifi", "ethernet", "virtual", "other" for an OS name."""
    low = (name or "").lower()
    if is_virtual_name(low) or low in ("lo", "lo0"):
        return "virtual"
    if any(h in low for h in _WIRELESS_NAME_HINTS):
        return "wifi"
    if any(h in low for h in _WIRED_NAME_HINTS):
        return "ethernet"
    return "other"


KIND_LABELS = {
    "wifi": "Wi-Fi",
    "ethernet": "Ethernet",
    "other": "Network adapter",
    "virtual": "Virtual adapter (phone will not reach this)",
}


def default_route_address():
    """The local address the OS would use for the default route, or "".

    A UDP "connect" only performs a route lookup — no packet is sent and no DNS
    is resolved, so this works with the Internet disconnected. It returns "" on
    a machine with no default route at all (a laptop that has joined a phone
    hotspot *without* an upstream gateway is the interesting case).
    """
    probe = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        probe.connect(_ROUTE_PROBE)
        return probe.getsockname()[0]
    except OSError:
        return ""
    finally:
        probe.close()


def _addresses_from_psutil():
    """[(interface_name, ipv4), ...] using psutil when it is installed."""
    try:
        import psutil
    except ImportError:
        return None
    out = []
    try:
        addrs = psutil.net_if_addrs()
        stats = psutil.net_if_stats()
    except Exception:  # pragma: no cover - platform/permission failures
        return None
    for name, entries in addrs.items():
        # A down interface (VPN disconnected, adapter disabled) cannot be
        # reached, so it is not a candidate at all.
        stat = stats.get(name)
        if stat is not None and not stat.isup:
            continue
        for entry in entries:
            # family is an int on some psutil versions and an AddressFamily on
            # others; the numeric AF_INET value is stable.
            if getattr(entry, "family", None) != socket.AF_INET:
                continue
            addr = (entry.address or "").split("%")[0]
            if addr:
                out.append((name, addr))
    return out


def _addresses_from_stdlib():
    """[(interface_name, ipv4), ...] from the standard library only.

    The hostname usually resolves to the primary adapter, which is the right
    answer on a simple machine. Interface names are unknown, so everything is
    classified from the address alone.
    """
    out = []
    try:
        host = socket.gethostname()
    except OSError:
        host = ""
    if host:
        try:
            for info in socket.getaddrinfo(host, None, socket.AF_INET):
                addr = info[4][0]
                if addr:
                    out.append((host, addr))
        except OSError:
            pass
    return out


def enumerate_addresses():
    """Every advertised IPv4 candidate, best first.

    Returns a list of dicts:
        {"ip", "interface", "kind", "label", "likely", "private", "route"}

    `route` marks the address the OS would use for its default route, which is
    the one a phone on the same hotspot will normally reach. Loopback,
    unspecified and link-local addresses are never included. Duplicates are
    collapsed, keeping the best-ranked interface name for each IP.
    """
    pairs = _addresses_from_psutil()
    if pairs is None:
        pairs = _addresses_from_stdlib()

    route_ip = default_route_address()

    by_ip = {}
    for name, ip in pairs:
        if is_loopback(ip) or is_unspecified(ip) or is_link_local(ip):
            continue
        kind = classify_interface(name) if name else "other"
        # A virtual *name* only demotes an address when we could actually read
        # the name; otherwise fall back to guessing from a known virtual range.
        entry = by_ip.get(ip)
        if entry is None:
            by_ip[ip] = {
                "ip": ip,
                "interface": name or "network",
                "kind": kind,
                "private": is_private(ip),
                "route": ip == route_ip,
            }
        else:
            entry["route"] = entry["route"] or (ip == route_ip)
            # Keep the friendliest classification seen for this address.
            if entry["kind"] != "wifi" and kind == "wifi":
                entry["kind"] = kind
                entry["interface"] = name or entry["interface"]
            elif entry["kind"] == "virtual" and kind == "ethernet":
                entry["kind"] = kind
                entry["interface"] = name or entry["interface"]

    def rank(entry):
        likely = 0 if entry["kind"] in ("wifi", "ethernet") else 1
        return (
            likely,                       # real LAN adapters first
            0 if entry["route"] else 1,   # then the default-route address
            0 if entry["private"] else 1, # then private ranges
            entry["kind"] == "virtual",   # virtual adapters last
            _ip_sort_key(entry["ip"]),
        )

    out = []
    for entry in sorted(by_ip.values(), key=rank):
        likely = entry["kind"] in ("wifi", "ethernet")
        out.append({
            "ip": entry["ip"],
            "interface": entry["interface"],
            "kind": entry["kind"],
            "label": KIND_LABELS.get(entry["kind"], KIND_LABELS["other"]),
            "likely": likely,
            "private": entry["private"],
            "route": entry["route"],
        })
    return out


def _ip_sort_key(ip):
    try:
        return tuple(int(p) for p in ip.split("."))
    except ValueError:
        return (999, 999, 999, 999)


def server_port(request=None):
    """The port this Proclaim instance is actually listening on.

    SERVER_PORT from the running server is the authoritative answer (it is
    correct whether the port came from --port, PROCLAIM_PORT or the default),
    which keeps the QR code honest even if those disagree. Outside a request
    (the console banner, which runs before the server starts) the PROCLAIM_PORT
    environment variable is used, then the built-in default.
    """
    if request is not None:
        port = request.META.get("SERVER_PORT")
        if port:
            try:
                return int(port)
            except (TypeError, ValueError):
                pass
    try:
        return int(os.environ.get("PROCLAIM_PORT", ""))
    except ValueError:
        return DEFAULT_PORT


def hostname():
    """The computer's NetBIOS/DNS name, or "".

    Advertised as a best-effort fallback (`<name>.local`); see the note in
    README.md — mDNS resolution from a phone browser is unreliable, so the
    numeric address and the QR code remain the dependable paths.
    """
    try:
        name = (socket.gethostname() or "").strip()
    except OSError:
        return ""
    return "" if name.lower() in ("localhost", "") else name


def connection_options(port):
    """Everything worth showing the operator, best first.

    Returns a list of {"label", "url", "ip", "kind", "likely", "note"} entries
    covering both the raw IPv4 addresses and the hostname fallback.
    """
    options = []
    for entry in enumerate_addresses():
        options.append({
            "label": "%s — %s" % (entry["interface"], entry["label"]),
            "url": "http://%s:%d/" % (entry["ip"], port),
            "ip": entry["ip"],
            "kind": entry["kind"],
            "likely": entry["likely"],
            "note": "Default route" if entry["route"] else "",
        })
    name = hostname()
    if name:
        options.append({
            "label": "%s.local — hostname" % name,
            "url": "http://%s.local:%d/" % (name, port),
            "ip": "%s.local" % name,
            "kind": "hostname",
            "likely": False,
            "note": "Only works if mDNS resolves on the phone",
        })
    return options


def primary_url(port):
    """The single address to put in the QR code and the console banner."""
    options = connection_options(port)
    for option in options:
        if option["likely"]:
            return option["url"]
    for option in options:
        if option["kind"] != "hostname":
            return option["url"]
    return "http://127.0.0.1:%d/" % port


# --- QR codes -------------------------------------------------------------

QR_DARK = "#000000"
QR_LIGHT = "#FFFFFF"
QR_SCALE = 6
QR_BORDER = 2


def qr_png_bytes(text, scale=QR_SCALE):
    """Encode `text` as a PNG QR code, locally and offline.

    segno is a pure-Python encoder with its own PNG writer, so this needs no
    Pillow, no network access and no native extension — it behaves identically
    in the portable Windows build.
    """
    import segno

    # error='m' recovers ~15% of the symbol, which covers a phone camera
    # scanning a laptop screen at an angle in a church. 'h' would add a quiet
    # zone of dense modules for no benefit here.
    qr = segno.make(text, error="m")
    buf = io.BytesIO()
    qr.save(buf, kind="png", scale=scale, border=QR_BORDER,
            dark=QR_DARK, light=QR_LIGHT)
    return buf.getvalue()


def qr_data_uri(text, scale=QR_SCALE):
    """The same QR code as a `data:` URI for inline use in a page."""
    import base64

    png = qr_png_bytes(text, scale)
    return "data:image/png;base64," + base64.b64encode(png).decode("ascii")


def network_report(request):
    """Everything the Connect panel renders, in one payload."""
    port = server_port(request)
    options = connection_options(port)
    return {
        "port": port,
        "bind": "0.0.0.0",
        "hostname": hostname(),
        "primaryUrl": primary_url(port),
        "urls": options,
        "firewallHint": FIREWALL_HINT,
        "netshCommand": NETSH_COMMAND % port,
    }


FIREWALL_HINT = (
    "If the phone cannot connect, Windows Firewall is usually blocking Proclaim. "
    "Allow Proclaim on Private networks only — do not disable the firewall."
)
# %d is filled in per request by network_report(), so the operator gets a rule
# for the port Proclaim is actually listening on rather than the default 3000.
NETSH_COMMAND = (
    "netsh advfirewall firewall add rule name=Proclaim dir=in "
    "action=allow protocol=TCP localport=%d"
)