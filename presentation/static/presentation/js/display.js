/* ============================================================
   ChurchCast — Display renderer
   Connects via WebSocket, renders current slide on a pure canvas.
   ============================================================ */
(function () {
  "use strict";

  var ROOM_CODE = window.ROOM_CODE;
  var ws = null;
  var state = null;
  var countdownTimer = null;

  var stage = document.getElementById("stage");
  var content = document.getElementById("content");

  function wsUrl() {
    var proto = location.protocol === "https:" ? "wss:" : "ws:";
    return proto + "//" + location.host + "/ws/room/" + ROOM_CODE + "/";
  }

  function connect() {
    ws = new WebSocket(wsUrl());

    ws.onopen = function () {
      // request current state on (re)connect
      send({ type: "get_state" });
    };

    ws.onmessage = function (ev) {
      try {
        var msg = JSON.parse(ev.data);
        if (msg.type === "state") {
          state = msg.state;
          render();
        }
      } catch (e) {
        // ignore malformed
      }
    };

    ws.onclose = function () {
      setTimeout(connect, 1500); // auto-reconnect
    };

    ws.onerror = function () {
      ws.close();
    };
  }

  function send(obj) {
    if (ws && ws.readyState === WebSocket.OPEN) {
      ws.send(JSON.stringify(obj));
    }
  }

  function render() {
    stopCountdown();

    if (!state) return;

    if (state.blank) {
      stage.classList.add("blank");
      return;
    }
    stage.classList.remove("blank");

    var ct = state.contentType;
    var c = state.content || {};
    var idx = state.slideIndex || 0;

    if (!ct) {
      content.innerHTML = '<div class="d-placeholder">Awaiting presentation…</div>';
      return;
    }

    if (ct === "scripture") {
      renderScripture(c, idx);
    } else if (ct === "song") {
      renderSong(c, idx);
    } else if (ct === "announcement") {
      renderAnnouncement(c);
    } else if (ct === "image") {
      renderImage(c);
    } else if (ct === "countdown") {
      renderCountdown(state.countdown);
    }
  }

  function renderScripture(c, idx) {
    var slides = c.slides || [];
    var slide = slides[idx] || slides[0];
    if (!slide) { content.innerHTML = ""; return; }
    content.innerHTML =
      '<div class="d-scripture">' +
        '<div class="d-scripture__ref">' + esc(slide.reference) + "</div>" +
        '<div class="d-scripture__text">' + esc(slide.text) + "</div>" +
      "</div>";
  }

  function renderSong(c, idx) {
    var slides = c.slides || [];
    var slide = slides[idx] || slides[0];
    if (!slide) { content.innerHTML = ""; return; }
    var labelHtml = slide.label
      ? '<div class="d-song__label">' + esc(slide.label) + "</div>"
      : "";
    content.innerHTML =
      '<div class="d-song">' +
        labelHtml +
        '<div class="d-song__text">' + esc(slide.text) + "</div>" +
      "</div>";
  }

  function renderAnnouncement(c) {
    var body = c.body ? '<div class="d-announcement__body">' + esc(c.body).replace(/\n/g, "<br>") + "</div>" : "";
    content.innerHTML =
      '<div class="d-announcement">' +
        '<div class="d-announcement__title">' + esc(c.title || "") + "</div>" +
        body +
      "</div>";
  }

  function renderImage(c) {
    var url = c.url || "";
    content.innerHTML = '<div class="d-image"><img src="' + url + '" alt=""></div>';
  }

  function renderCountdown(cd) {
    if (!cd || !cd.endTime) {
      content.innerHTML = '<div class="d-placeholder">Countdown</div>';
      return;
    }
    var title = cd.title || "";
    content.innerHTML =
      '<div class="d-countdown">' +
        (title ? '<div class="d-countdown__label">' + esc(title) + "</div>" : "") +
        '<div class="d-countdown__time" id="cdTime">0:00</div>' +
      "</div>";
    startCountdown(cd.endTime);
  }

  function startCountdown(endTime) {
    var el = document.getElementById("cdTime");
    if (!el) return;

    function tick() {
      var remaining = Math.max(0, endTime - Date.now());
      var secs = Math.floor(remaining / 1000);
      var m = Math.floor(secs / 60);
      var s = secs % 60;
      el.textContent = m + ":" + (s < 10 ? "0" : "") + s;
      if (remaining <= 0) {
        el.classList.add("done");
        el.textContent = "0:00";
        stopCountdown();
        return;
      }
      countdownTimer = setTimeout(tick, 250);
    }
    tick();
  }

  function stopCountdown() {
    if (countdownTimer) {
      clearTimeout(countdownTimer);
      countdownTimer = null;
    }
  }

  function esc(s) {
    if (s == null) return "";
    return String(s)
      .replace(/&/g, "&amp;")
      .replace(/</g, "&lt;")
      .replace(/>/g, "&gt;")
      .replace(/"/g, "&quot;");
  }

  connect();
})();
