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
        } else if (msg.type === "slide_change") {
          // Minimal navigation message: no content is retransmitted.
          if (!state) { send({ type: "get_state" }); return; }
          if (state.contentType !== msg.contentType || state.itemId !== msg.itemId) {
            // Content no longer matches -> fetch authoritative snapshot.
            send({ type: "get_state" });
            return;
          }
          state.slideIndex = msg.slideIndex;
          state.slideCount = msg.slideCount;
          render();
        } else if (msg.type === "blank") {
          if (!state) return;
          state.blank = msg.blank;
          render();
        } else if (msg.type === "style_change") {
          if (!state) return;
          state.styles = msg.styles;
          applyStyles(state.styles);
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
    applyStyles(state.styles);

    if (state.blank) {
      stage.classList.add("blank");
      return;
    }
    stage.classList.remove("blank");

    var ct = state.contentType;
    var c = state.content || {};
    var idx = state.slideIndex || 0;

    content.classList.toggle("d-image-stage", ct === "image");

    if (!ct) {
      content.innerHTML = '<div class="d-slide__note">Awaiting presentation…</div>';
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
    fitStage();
  }

  // Shrink scale (--d-fit) in steps so long scripture/song/announcement text
  // fits the stage, down to 60% of the operator's global --d-mult size.
  function fitStage() {
    var root = document.documentElement;
    root.style.setProperty("--d-fit", "1");
    if (!state || state.blank) return;
    var ct = state.contentType;
    if (ct !== "scripture" && ct !== "song" && ct !== "announcement") return;
    var el = document.getElementById("content");
    if (!el) return;
    var MIN = 0.6;
    var cur = 1;
    while (cur > MIN) {
      if (el.scrollHeight <= el.clientHeight + 2 && el.scrollWidth <= el.clientWidth + 2) break;
      cur = Math.max(MIN, cur - 0.05);
      root.style.setProperty("--d-fit", String(cur));
    }
  }

  function renderScripture(c, idx) {
    var slides = c.slides || [];
    var slide = slides[idx] || slides[0];
    if (!slide) { content.innerHTML = ""; return; }
    content.innerHTML =
      '<div class="d-slide d-slide--scripture">' +
        '<div class="d-slide__ref">' + esc(slide.reference) + "</div>" +
        '<div class="d-slide__verse">\u201C' + esc(slide.text) + "\u201D</div>" +
      "</div>";
  }

  function renderSong(c, idx) {
    var slides = c.slides || [];
    var slide = slides[idx] || slides[0];
    if (!slide) { content.innerHTML = ""; return; }
    var labelHtml = slide.label
      ? '<div class="d-slide__label">' + esc(slide.label) + "</div>"
      : "";
    content.innerHTML =
      '<div class="d-slide d-slide--song">' +
        labelHtml +
        '<div class="d-slide__lyric">' + esc(slide.text) + "</div>" +
      "</div>";
  }

  function renderAnnouncement(c) {
    var title = (c.title || "").trim();
    var body = c.body ? '<div class="d-slide__body">' + esc(c.body).replace(/\n/g, "<br>") + "</div>" : "";
    // Title and body are centred horizontally and vertically as one slide.
    content.innerHTML =
      '<div class="d-slide d-slide--announcement">' +
        (title ? '<div class="d-slide__title">' + esc(title) + "</div>" : "") +
        body +
      "</div>";
  }

  function renderImage(c) {
    var fit = c.fit || "blur";
    var url = c.url || "";
    var back = c.backdrop || "";
    var html = '<div class="d-image d-image--' + esc(fit) + '">';
    if (fit === "blur" && back) {
      html += '<div class="d-image__backdrop"><img src="' + esc(back) + '" alt=""></div>';
    }
    html += '<img class="d-image__front" src="' + esc(url) + '" alt=""></div>';
    content.innerHTML = html;
  }

  function renderCountdown(cd) {
    if (!cd || !cd.endTime) {
      content.innerHTML = '<div class="d-slide__note">Countdown</div>';
      return;
    }
    var title = cd.title || "";
    content.innerHTML =
      '<div class="d-slide d-slide--countdown">' +
        (title ? '<div class="d-slide__timer-label">' + esc(title) + "</div>" : "") +
        '<div class="d-slide__timer" id="cdTime">0:00</div>' +
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

  var FONTS = {
  default: '-apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, "Helvetica Neue", Arial, sans-serif',
  sans: '"Segoe UI", Roboto, "Helvetica Neue", Arial, sans-serif',
  serif: 'Georgia, Cambria, "Times New Roman", serif',
  "noto-sans": '"Noto Sans", -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, sans-serif',
  "noto-serif": '"Noto Serif", Georgia, Cambria, "Times New Roman", serif',
  montserrat: '"Montserrat", -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, sans-serif',
  merriweather: '"Merriweather", Georgia, Cambria, "Times New Roman", serif'
};
var SIZE_SCALE = { sm: "0.82", md: "1", lg: "1.18" };
var LINE_SCALE = { tight: "1.3", normal: "1.5", loose: "1.8" };

// Resolves a room style size value (named preset or number) to a CSS factor.
function sizeFactor(size) {
  if (typeof size === "number") return String(size);
  return SIZE_SCALE[size] || "1";
}

// Sets a CSS var, or removes it when empty so the stylesheet fallback applies.
function setCssVar(root, name, value) {
  if (value === "" || value == null) root.style.removeProperty(name);
  else root.style.setProperty(name, value);
}

// Background kinds. "color" and "image" are the original two; "transparent"
// and "chroma" are the compositing modes.
var DEFAULT_BG_COLOR = "#05070A";
var CHROMA_DEFAULT_COLOR = "#00FF00";

// Chooses the text-shadow the operator asked for. Auto means: always on for
// image backgrounds; on solid backgrounds only when it actually helps
// (dark text gets a light shadow, light text a dark one). Transparent and
// chroma backgrounds always get the dark shadow for light text, because
// nothing about the frame behind the text is known — this is what keeps an
// overlay readable over arbitrary video.
function textShadowFor(styles, bgMode) {
  var mode = styles.shadow || "auto";
  if (mode === "off") return "none";
  var hex = /^#[0-9a-f]{6}$/.test(styles.textColor) ? styles.textColor : "#F5F7FA";
  var light = (parseInt(hex.slice(1, 3), 16) * 0.299 +
    parseInt(hex.slice(3, 5), 16) * 0.587 +
    parseInt(hex.slice(5, 7), 16) * 0.114) > 150;
  if (mode === "strong") {
    return light
      ? "0 2px 6px rgba(0,0,0,0.7), 0 6px 30px rgba(0,0,0,0.55)"
      : "0 2px 6px rgba(255,255,255,0.6), 0 6px 30px rgba(255,255,255,0.4)";
  }
  if (!light && bgMode === "color") return "0 1px 3px rgba(255,255,255,0.55), 0 0 18px rgba(255,255,255,0.25)";
  return "0 1px 4px rgba(0,0,0,0.65), 0 0 24px rgba(0,0,0,0.35)";
}

// Reads the background the display should paint, honouring a per-display
// override (/display/ROOM?bg=transparent) so a dedicated vMix overlay browser
// can be transparent without changing the room the congregation is watching.
function effectiveBackground(styles) {
  var bg = (styles || {}).background || {};
  var override = (window.DISPLAY_BG || "").toLowerCase();
  if (override && override !== "room") {
    return override === "transparent"
      ? { type: "transparent" }
      : { type: "chroma", value: CHROMA_DEFAULT_COLOR };
  }
  return bg;
}

// Applies the background to every painted surface. Each of html, body, #display
// , the blanked stage and an image's letterbox reads one of the three
// --d-*-bg variables set here, so one branch covers the whole frame and
// nothing opaque can survive.
function applyBackground(bg) {
  var root = document.documentElement;
  var disp = document.getElementById("display");
  var stage = document.getElementById("stage");
  var type = bg && bg.type;

  root.classList.toggle("d-transparent", type === "transparent");

  if (type === "image" && bg.value) {
    var src = bg.value;
    if (bg.blur === "soft" || bg.blur === "strong") src = imageVariantUrl(src, bg.blur);
    disp.style.backgroundImage = "url('" + src + "')";
    disp.style.backgroundSize = "cover";
    disp.style.backgroundPosition = "center";
    stage.classList.add("has-bg");
    // The page background stays the near-black base colour: it is only ever seen
    // behind the cover-fitted image, and --d-page-bg must remain a *colour* (it
    // is used as `background:` on html and body, where a URL would be invalid).
    root.style.setProperty("--d-page-bg", "#05070A");
    root.style.setProperty("--d-blank-bg", "#000");
    root.style.setProperty("--d-figure-bg", "#000");
    // Clear any inline tint left by a previous colour/chroma mode.
    disp.style.backgroundColor = "";
    return;
  }

  disp.style.backgroundImage = "";
  stage.classList.remove("has-bg");

  if (type === "transparent") {
    // Genuine alpha: html, body and #display all resolve to transparent, and
    // the blanked stage and image letterbox follow so nothing flashes an
    // opaque rectangle over the program underneath.
    root.style.setProperty("--d-page-bg", "transparent");
    root.style.setProperty("--d-blank-bg", "transparent");
    root.style.setProperty("--d-figure-bg", "transparent");
    disp.style.backgroundColor = "";
    return;
  }

  var color;
  if (type === "chroma") {
    color = /^#[0-9a-f]{6}$/i.test(bg.value || "") ? bg.value : CHROMA_DEFAULT_COLOR;
    root.style.setProperty("--d-blank-bg", color);
    root.style.setProperty("--d-figure-bg", color);
  } else {
    color = (type === "color" && bg.value) ? bg.value : DEFAULT_BG_COLOR;
    root.style.setProperty("--d-blank-bg", "#000");
    root.style.setProperty("--d-figure-bg", "#000");
  }
  root.style.setProperty("--d-page-bg", color);
  disp.style.backgroundColor = color;
}

// Applies operator styles (font / text size / colour / weight / spacing /
// alignment / shadow / background) from room state.
function applyStyles(styles) {
  styles = styles || {};
  var root = document.documentElement;
  root.style.setProperty("--d-font", FONTS[styles.font] || FONTS.default);
  root.style.setProperty("--d-mult", sizeFactor(styles.size));
  setCssVar(root, "--d-color", styles.textColor);
  setCssVar(root, "--d-ref-color", styles.referenceColor);
  setCssVar(root, "--d-weight", styles.bold ? "700" : "");
  setCssVar(root, "--d-line", LINE_SCALE[styles.lineSpacing]);

  // Alignment is a design-system token now (--pres-text-align) rather than a
  // value pasted onto one container, so every slide type follows it.
  setCssVar(root, "--pres-text-align", styles.align === "left" ? "left" : "");

  var bg = effectiveBackground(styles);
  applyBackground(bg);
  root.style.setProperty("--d-shadow", textShadowFor(styles, bg && bg.type));
  warmFonts(styles.font);
}

// Preloads the face currently selected so text is rendered in it immediately
// (and so the operator's preview updates without a flash of fallback text).
function warmFonts(fontKey) {
  if (!document || !document.fonts || typeof document.fonts.load !== "function") return;
  var fam = FONTS[fontKey];
  if (!fam) return;
  var name = (fam.match(/^"([^"]+)"/) || [])[1];
  if (!name) return;
  try {
    document.fonts.load('400 20px "' + name + '"');
    document.fonts.load('700 20px "' + name + '"');
  } catch (e) {}
}

// Variant convention: /media/images/<uuid>.jpg -> /media/images/<uuid>.soft.jpg
function imageVariantUrl(url, kind) {
  if (!url || !kind) return url || "";
  var i = url.lastIndexOf(".");
  if (i <= 0) return url;
  return url.slice(0, i) + "." + kind + url.slice(i);
}

function esc(s) {
    if (s == null) return "";
    return String(s)
      .replace(/&/g, "&amp;")
      .replace(/</g, "&lt;")
      .replace(/>/g, "&gt;")
      .replace(/"/g, "&quot;");
  }

  window.addEventListener("resize", function () {
    if (state) render();
  });

  connect();
})();
