/* ============================================================
   Proclaim — Connect panel behaviour (laptop landing page only)

   Three jobs, all local:

     1. point the QR code at whichever address the operator picks,
     2. show whether a phone controller is actually connected,
     3. surface the Windows Firewall remedy if the phone cannot connect.

   Presence comes from the same process-local registry the
   RoomConsumer maintains, over a single small JSON read. There is
   deliberately no polling loop here: this page is a setup screen, not
   the live display, and the control page's WebSocket already carries
   connection state. A slow, cheap heartbeat (10s) is enough to keep a
   dot honest while an operator sets up, and it stops entirely once
   the tab is hidden.
   ============================================================ */
(function () {
  "use strict";

  var PRESENT_INTERVAL_MS = 10000;

  var qrImage = document.getElementById("qrImage");
  var qrLabel = document.getElementById("qrLabel");
  var netDot = document.getElementById("netDot");
  var netStatusText = document.getElementById("netStatusText");
  var list = document.getElementById("netList");
  var firewallBtn = document.getElementById("firewallBtn");
  var timer = null;

  function setStatus(connected, count) {
    if (netDot) netDot.classList.toggle("on", connected);
    if (netStatusText) {
      netStatusText.textContent = connected
        ? "Phone connected (" + count + ")"
        : "Waiting for a controller…";
    }
  }

  // The room rows render their state server-side, so a page left open while the
  // phone connects (or drops) would otherwise keep claiming the old answer.
  function paintRooms(presence) {
    var rows = document.querySelectorAll("[data-room]");
    Array.prototype.forEach.call(rows, function (row) {
      var code = row.getAttribute("data-room");
      var on = !!(presence[code] && presence[code].control);
      var label = row.querySelector(".cc-presence");
      if (!label) return;
      label.classList.toggle("cc-presence--on", on);
      label.textContent = on ? "● phone connected" : "○ no controller";
    });
  }

  function refreshPresence() {
    fetch("/api/network", { headers: { Accept: "application/json" } })
      .then(function (r) { return r.json(); })
      .then(function (data) {
        var rooms = data.presence || {};
        var total = 0;
        Object.keys(rooms).forEach(function (code) {
          total += (rooms[code] && rooms[code].control) || 0;
        });
        setStatus(total > 0, total);
        paintRooms(rooms);
      })
      .catch(function () {
        // Offline or the server restarted: say so rather than lying.
        setStatus(false, 0);
        if (netStatusText) netStatusText.textContent = "Server unreachable — is Proclaim still running?";
      });
  }

  // Point the QR code at a specific address when the operator clicks one.
  function wireAddresses() {
    if (!list) return;
    list.addEventListener("click", function (ev) {
      var link = ev.target.closest ? ev.target.closest(".cc-addr__url") : null;
      if (!link) return;
      // Let the browser follow the link; only take over when it is one of the
      // addresses Proclaim itself advertised (never a click on plain text).
      var index = Array.prototype.indexOf.call(
        list.querySelectorAll(".cc-addr"), link.parentNode
      );
      if (index < 0 || !qrImage) return;
      ev.preventDefault();
      var url = link.textContent.trim();
      qrImage.src = "/api/network/qr?index=" + index;
      if (qrLabel) qrLabel.textContent = url;
      window.location.href = url;
    });
  }

  function wireFirewall() {
    if (!firewallBtn) return;
    // ms-settings: is a local Windows protocol; it only opens that machine's
    // own firewall settings page. Nothing is changed automatically and the
    // firewall is never disabled — the operator has to allow the rule.
    firewallBtn.onclick = function () {
      window.location.href = "ms-settings:network-firewall";
    };
  }

  function start() {
    refreshPresence();
    timer = setInterval(refreshPresence, PRESENT_INTERVAL_MS);
    document.addEventListener("visibilitychange", function () {
      if (document.hidden) {
        clearInterval(timer);
        timer = null;
      } else if (!timer) {
        refreshPresence();
        timer = setInterval(refreshPresence, PRESENT_INTERVAL_MS);
      }
    });
  }

  wireAddresses();
  wireFirewall();
  start();
})();