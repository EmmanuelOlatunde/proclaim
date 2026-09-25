/* ============================================================
   ChurchCast — Control interface
   Mobile-first broadcast controller. WebSocket commands.

   Network protocol (low bandwidth):
   - Full "state" messages only when the presented content changes
   - Minimal "slide_change" / "blank" messages for navigation
   - Content is cached locally; slides never refetch the library
   ============================================================ */
(function () {
  "use strict";

  var ROOM_CODE = window.ROOM_CODE;
  var ws = null;
  var state = null;
  var activeTab = "queue";
  var queue = [];
  var previewCountdownTimer = null;
  var scIndex = null;
  var scIndexTranslation = null;
  var sc = { mode: "instant", testament: "OT", book: null, chapter: null, verse: null, input: "" };
  // Scripture translations: [{id, full_name, license, book_count, verse_count}].
  var translations = [];
  var activeTranslation = "king_james_version";
  var TRANSLATION_KEY = "churchcast.scripture.translation";

  // --- DOM refs ---
  var el = function (id) { return document.getElementById(id); };
  var panel = el("panel");
  var statusDot = el("statusDot");
  var statusText = el("statusText");
  var liveTitle = el("liveTitle");
  var previewCanvas = el("previewCanvas");
  var previewLabel = el("previewLabel");
  var previewRef = el("previewRef");
  var previewTrans = el("previewTrans");
  var previewText = el("previewText");
  var previewImg = el("previewImg");
  var countdownLabel = el("countdownLabel");
  var countdownDisplay = el("countdownDisplay");
  var slideNum = el("slideNum");
  var sectionLabel = el("sectionLabel");
  var slideStrip = el("slideStrip");
  var prevBtn = el("prevBtn");
  var nextBtn = el("nextBtn");
  var blankBtn = el("blankBtn");
  var qPrevBtn = el("qPrev");
  var qNextBtn = el("qNext");
  var qPos = el("qPos");
  var cdBtn = el("cdBtn");

  // --- WebSocket ---
  function wsUrl() {
    var proto = location.protocol === "https:" ? "wss:" : "ws:";
    return proto + "//" + location.host + "/ws/room/" + ROOM_CODE + "/";
  }

  function connect() {
    ws = new WebSocket(wsUrl());
    ws.onopen = function () {
      setLive(true);
      send({ type: "get_state" });
    };
    ws.onmessage = function (ev) {
      try {
        var msg = JSON.parse(ev.data);
        if (msg.type === "state") {
          state = msg.state;
          renderAll();
        } else if (msg.type === "slide_change") {
          if (!state) { send({ type: "get_state" }); return; }
          state.slideIndex = msg.slideIndex;
          state.slideCount = msg.slideCount;
          renderAll();
        } else if (msg.type === "blank") {
          if (!state) return;
          state.blank = msg.blank;
          renderAll();
        } else if (msg.type === "style_change") {
          if (!state) return;
          state.styles = msg.styles;
          renderAll();
        } else if (msg.type === "error") {
          showToast(msg.message || "Something went wrong.");
        }
      } catch (e) {}
    };
    ws.onclose = function () {
      setLive(false);
      setTimeout(connect, 1500);
    };
    ws.onerror = function () { ws.close(); };
  }

  function send(obj) {
    if (ws && ws.readyState === WebSocket.OPEN) {
      ws.send(JSON.stringify(obj));
    }
  }

  function cmd(action, extra) {
    send({ type: "command", command: Object.assign({ action: action }, extra || {}) });
  }

  function setLive(live) {
    if (live) {
      statusDot.classList.add("live");
      statusText.textContent = "LIVE";
    } else {
      statusDot.classList.remove("live");
      statusText.textContent = "Reconnecting…";
    }
  }

  var toastTimer = null;
  function showToast(message) {
    var t = el("toast");
    t.textContent = message;
    t.classList.add("show");
    if (toastTimer) clearTimeout(toastTimer);
    toastTimer = setTimeout(function () { t.classList.remove("show"); }, 3500);
  }

  // --- API helpers ---
  function api(path, method, body) {
    var opts = { method: method || "GET", headers: {} };
    if (body) {
      if (body instanceof FormData) {
        opts.body = body;
      } else {
        opts.headers["Content-Type"] = "application/json";
        opts.body = JSON.stringify(body);
      }
    }
    return fetch(path, opts).then(function (r) { return r.json(); });
  }

  // --- Preview rendering ---
  function renderAll() {
    syncTranslationFromState();
    renderPreview();
    renderSlideStrip();
    renderNavState();
    syncQueueUI();
  }

  // --- Style helpers (mirror display.js so previews match the display) ---
  var FONTS = {
    default: '-apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, "Helvetica Neue", Arial, sans-serif',
    sans: '"Segoe UI", Roboto, "Helvetica Neue", Arial, sans-serif',
    serif: 'Georgia, Cambria, "Times New Roman", serif'
  };
  var SIZE_SCALE = { sm: "0.82", md: "1", lg: "1.18" };

  function stateStyles() {
    return (state && state.styles) ||
      { font: "default", size: "md", background: { type: "color", value: "#05070A" } };
  }

  // Font/size are CSS vars on <html> so every preview inherits them.
  function applyStylesGlobally(styles) {
    styles = styles || {};
    document.documentElement.style.setProperty("--d-font", FONTS[styles.font] || FONTS.default);
    document.documentElement.style.setProperty("--d-mult", SIZE_SCALE[styles.size] || "1");
  }

  function applyBgTo(el, styles) {
    var bg = (styles || {}).background || {};
    if (bg.type === "image" && bg.value) {
      el.style.backgroundImage = "url('" + bg.value + "')";
      el.style.backgroundSize = "cover";
      el.style.backgroundPosition = "center";
      el.style.backgroundColor = "#05070A";
      el.classList.add("has-bg");
    } else {
      el.style.backgroundImage = "";
      el.style.backgroundColor = (bg.type === "color" && bg.value) ? bg.value : "#05070A";
      el.classList.remove("has-bg");
    }
  }

  // Builds inner HTML for the given content, mirroring display.js.
  function renderPreviewInto(container, ct, c, idx, styles) {
    applyStylesGlobally(styles);
    applyBgTo(container, styles);
    var slide = null;
    var html = "";
    if (ct === "song") {
      var sslides = c.slides || [];
      slide = sslides[idx] || sslides[0] || {};
      html =
        (slide.label ? '<div class="cc-mini__song-label">' + esc(slide.label) + "</div>" : "") +
        '<div class="cc-mini__text">' + esc(slide.text) + "</div>";
    } else if (ct === "scripture") {
      var s2 = c.slides || [];
      slide = s2[idx] || s2[0] || {};
      html =
        '<div class="cc-mini__ref">' + esc(slide.reference || c.reference || "") + "</div>" +
        '<div class="cc-mini__text">\u201C' + esc(slide.text) + "\u201D</div>";
    } else if (ct === "announcement") {
      html =
        '<div class="cc-mini__title">' + esc(c.title || "") + "</div>" +
        (c.body ? '<div class="cc-mini__text">' + esc(c.body) + "</div>" : "");
    } else if (ct === "image") {
      html = '<div class="cc-mini__image"><img src="' + esc(c.url || "") + '" alt=""></div>';
    } else if (ct === "countdown") {
      html = '<div class="cc-mini__countdown-label">' + esc(c.title || "") + "</div>" +
        '<div class="cc-mini__countdown">0:00</div>';
    }
    container.innerHTML = html || '<div class="cc-empty">Nothing to preview.</div>';
  }

  // Sample content used by the Style editor so the look can be tuned live.
  function styleSample() {
    return {
      ct: (state && state.contentType) || "scripture",
      content: (state && state.content) || {
        reference: "John 3:16",
        slides: [{ reference: "John 3:16", text: "For God so loved the world, that he gave his only begotten Son, that whosoever believeth in him should not perish, but have everlasting life." }]
      }
    };
  }

  function renderPreview() {
    if (!state) return;
    var styles = stateStyles();
    applyStylesGlobally(styles);
    applyBgTo(previewCanvas, styles);
    previewCanvas.classList.remove("blank");

    if (state.blank) {
      previewCanvas.classList.add("blank");
      previewLabel.textContent = "BLANKED";
      previewRef.textContent = "";
      previewText.textContent = "";
      previewImg.style.display = "none";
      countdownLabel.style.display = "none";
      countdownDisplay.style.display = "none";
      previewTrans.classList.remove("show");
      liveTitle.textContent = "Blanked";
      return;
    }

    var ct = state.contentType;
    var c = state.content || {};
    var idx = state.slideIndex || 0;

    previewImg.style.display = "none";
    countdownLabel.style.display = "none";
    countdownDisplay.style.display = "none";
    previewRef.textContent = "";
    previewText.textContent = "";
    previewTrans.classList.remove("show");

    if (!ct) {
      previewLabel.textContent = "No content";
      liveTitle.textContent = "—";
      return;
    }

    if (ct === "song") {
      var slides = c.slides || [];
      var slide = slides[idx] || slides[0] || {};
      previewLabel.textContent = (c.title || "Song") + (c.author ? " — " + c.author : "");
      previewRef.textContent = slide.label || "";
      previewText.textContent = slide.text || "";
      liveTitle.textContent = c.title || "Song";
    } else if (ct === "scripture") {
      var sslides = c.slides || [];
      var sslide = sslides[idx] || sslides[0] || {};
      previewLabel.textContent = "Scripture";
      previewRef.textContent = sslide.reference || c.reference || "";
      previewText.textContent = sslide.text || "";
      liveTitle.textContent = c.reference || "Scripture";
      var tName = translationName(activeTranslation);
      if (tName) {
        previewTrans.textContent = "· " + tName;
        previewTrans.classList.add("show");
      }
    } else if (ct === "announcement") {
      previewLabel.textContent = "Announcement";
      previewRef.textContent = c.title || "";
      previewText.textContent = (c.body || "").substring(0, 120);
      liveTitle.textContent = c.title || "Announcement";
    } else if (ct === "image") {
      previewLabel.textContent = c.title || "Image";
      previewText.textContent = "";
      previewImg.src = c.url || "";
      previewImg.style.display = "block";
      liveTitle.textContent = c.title || "Image";
    } else if (ct === "countdown") {
      previewLabel.textContent = "Countdown";
      previewText.textContent = "";
      var cd = state.countdown;
      if (cd) {
        countdownLabel.style.display = "block";
        countdownLabel.textContent = cd.title || "";
        countdownDisplay.style.display = "block";
        startPreviewCountdown(cd.endTime);
      }
      liveTitle.textContent = "Countdown";
    }

    var count = state.slideCount || 1;
    slideNum.textContent = (idx + 1) + " / " + count;
    if (ct === "song") {
      var sld = (c.slides || [])[idx];
      sectionLabel.textContent = sld ? sld.label : "";
    } else if (ct === "scripture") {
      var ssld = (c.slides || [])[idx];
      sectionLabel.textContent = ssld ? ssld.reference : "";
    } else {
      sectionLabel.textContent = "";
    }
  }

  function startPreviewCountdown(endTime) {
    if (previewCountdownTimer) clearTimeout(previewCountdownTimer);
    function tick() {
      var remaining = Math.max(0, endTime - Date.now());
      var secs = Math.floor(remaining / 1000);
      var m = Math.floor(secs / 60);
      var s = secs % 60;
      countdownDisplay.textContent = m + ":" + (s < 10 ? "0" : "") + s;
      cdBtn.textContent = "⏱ " + m + ":" + (s < 10 ? "0" : "") + s;
      if (remaining > 0) previewCountdownTimer = setTimeout(tick, 250);
    }
    tick();
  }

  // --- Slide strip ---
  function renderSlideStrip() {
    if (!state || !state.content) { slideStrip.innerHTML = ""; slideStrip.style.display = "none"; return; }
    var ct = state.contentType;
    var c = state.content;
    var slides = c.slides;
    var idx = state.slideIndex || 0;
    if (!slides || slides.length <= 1) { slideStrip.innerHTML = ""; slideStrip.style.display = "none"; return; }
    slideStrip.style.display = "";

    if (ct === "scripture") {
      // Dock scripture mode: verse position + Prev/Next + jump-to-Verses.
      var isFirst = idx === 0;
      var isLast = idx >= slides.length - 1;
      var refHtml = esc(c.reference || "Scripture");
      var transName = translationName(activeTranslation);
      var transHtml = transName
        ? '<span class="cc-vstrip__trans" data-vstrip-trans="1">\u00B7 ' + esc(transName) + "</span>"
        : "";
      var nextLabel = isLast ? "Next chapter" : "Next";
      var prevLabel = isFirst ? "Prev chapter" : "Prev";
      slideStrip.innerHTML =
        '<div class="cc-vstrip">' +
          '<button type="button" class="cc-vstrip__btn" id="vsPrev" aria-label="Previous verse">' +
            (isFirst ? "◀ " + prevLabel : "◀") + "</button>" +
          '<div class="cc-vstrip__mid">' +
            '<div class="cc-vstrip__ref">' + refHtml + transHtml + "</div>" +
            '<div class="cc-vstrip__pos">Verse <strong>' + (idx + 1) + '</strong> / ' + slides.length + "</div>" +
          "</div>" +
          '<button type="button" class="cc-vstrip__btn" id="vsNext" aria-label="Next verse">' +
            (isLast ? nextLabel + " ▶" : "▶") + "</button>" +
          '<button type="button" class="cc-vstrip__btn cc-vstrip__jump" id="vsJump" aria-label="Jump to verse">Verses</button>' +
        "</div>";
      el("vsPrev").onclick = function () { cmd("prev"); };
      el("vsNext").onclick = function () { cmd("next"); };
      el("vsJump").onclick = function () { openVersesJump(); };
      return;
    }

    var html = "";
    for (var i = 0; i < slides.length; i++) {
      var label = slides[i].label || slides[i].reference || ("Slide " + (i + 1));
      var active = i === idx ? " active" : "";
      html += '<button type="button" class="cc-slide-chip' + active + '" data-slide="' + i + '">' +
        '<span class="cc-slide-chip__num">' + (i + 1) + '</span>' +
        esc(label) +
      "</button>";
    }
    slideStrip.innerHTML = html;
  }

  function openVersesJump() {
    if (!state || state.contentType !== "scripture") return;
    var c = state.content || {};
    var slides = c.slides || [];
    var idx = state.slideIndex || 0;
    var body = '<div class="cc-field"><div class="cc-ref-result"><strong>' + esc(c.reference || "") + '</strong> — ' +
      slides.length + " verses</div></div>" +
      '<div class="cc-sc-grid cc-sc-grid--verses" id="vsGrid"></div>' +
      '<button class="cc-btn cc-btn--ghost cc-btn--block" id="vsClose">Close</button>';
    openModal("Jump to verse", body);
    var grid = el("vsGrid");
    var html = "";
    for (var v = 0; v < slides.length; v++) {
      var active = v === idx ? " active" : "";
      html += '<button class="cc-sc-cell cc-sc-cell--verse' + active + '" data-vs="' + v + '">' + (v + 1) + "</button>";
    }
    grid.innerHTML = html;
    grid.querySelectorAll("[data-vs]").forEach(function (b) {
      b.onclick = function () { cmd("goto_slide", { slideIndex: parseInt(b.dataset.vs) }); closeModal(); };
    });
    el("vsClose").onclick = closeModal;
  }

  function renderNavState() {
    if (!state) return;
    if (state.blank) {
      blankBtn.classList.add("active");
      blankBtn.textContent = "BLANKED";
    } else {
      blankBtn.classList.remove("active");
      blankBtn.textContent = "BLANK";
    }
  }

  // --- Queue UI (highlight + dock position) ---
  function queueIndex() {
    return (state && typeof state.queueIndex === "number") ? state.queueIndex : -1;
  }

  function syncQueueUI() {
    var qidx = queueIndex();
    if (qidx < 0) {
      qPos.textContent = "Service —";
    } else {
      var label = queue[qidx] ? queueItemLabel(queue[qidx]) : "";
      qPos.textContent = (qidx + 1) + "/" + queue.length + (label ? " · " + label : "");
    }
    // Highlight active queue row without refetching
    panel.querySelectorAll("[data-queue-idx]").forEach(function (row) {
      var on = parseInt(row.dataset.queueIdx) === qidx;
      row.classList.toggle("active", on);
    });
  }

  // --- Scripture translations ---
  function translationName(id) {
    for (var i = 0; i < translations.length; i++) {
      if (translations[i].id === id) return translations[i].full_name;
    }
    return null;
  }

  // The room's translation is authoritative; the control's last-picked
  // choice (in localStorage) acts as the default until state arrives.
  function syncTranslationFromState() {
    if (!state || !state.translation) return;
    if (activeTranslation === state.translation) return;
    activeTranslation = state.translation;
    try { localStorage.setItem(TRANSLATION_KEY, activeTranslation); } catch (e) {}
    var sel = el("scTranslation");
    if (sel) sel.value = activeTranslation;
    reloadIndexIfStale();
  }

  // Reload the picker index when it was built for a different translation
  // than the one now active (server state may differ from localStorage).
  function reloadIndexIfStale() {
    if (scIndex === null || scIndexTranslation === activeTranslation) return;
    scIndex = null;
    scIndexTranslation = null;
    loadScriptureIndex();
  }

  function loadTranslations() {
    api("/api/scripture/translations").then(function (res) {
      translations = res.translations || [];
      var saved = null;
      try { saved = localStorage.getItem(TRANSLATION_KEY); } catch (e) {}
      if (saved && !(state && state.translation)) {
        for (var i = 0; i < translations.length; i++) {
          if (translations[i].id === saved) {
            activeTranslation = saved;
            scIndex = null;
            break;
          }
        }
      } else if (state && state.translation) {
        activeTranslation = state.translation;
      }
      paintTranslationSelector();
      paintVerseStripTranslation();
      reloadIndexIfStale();
      if (scIndex === null) loadScriptureIndex();
    });
  }

  function paintTranslationSelector() {
    var sel = el("scTranslation");
    if (!sel) return;
    sel.innerHTML = translations.map(function (t) {
      return '<option value="' + esc(t.id) + '">' + esc(t.full_name) + "</option>";
    }).join("");
    sel.value = activeTranslation;
    sel.onchange = function () { selectTranslation(sel.value); };
  }

  function selectTranslation(id) {
    if (!id || id === activeTranslation) return;
    activeTranslation = id;
    try { localStorage.setItem(TRANSLATION_KEY, activeTranslation); } catch (e) {}
    cmd("set_translation", { translation: activeTranslation });
    // Rebuild the picker index for this translation so chapter/verse grids
    // always match what will be presented. This is index data only — it is
    // not fetching or presenting any scripture passage.
    scIndex = null;
    var root = el("scRoot");
    if (root) root.innerHTML = '<div class="cc-empty">Loading Bible index…</div>';
    loadScriptureIndex();
  }

  // Re-label the dock callouts once translation names are known.
  function paintVerseStripTranslation() {
    var tEls = document.querySelectorAll("[data-vstrip-trans]");
    var name = translationName(activeTranslation);
    for (var i = 0; i < tEls.length; i++) {
      tEls[i].textContent = name ? ("\u00B7 " + name) : "";
    }
  }

  // --- Tab rendering ---
  function renderTab() {
    switch (activeTab) {
      case "queue": renderQueue(); break;
      case "library": renderLibrary(); break;
      case "songs": renderSongs(); break;
      case "scripture": renderScripture(); break;
      case "images": renderImages(); break;
      case "announcements": renderAnnouncements(); break;
      case "style": renderStyle(); break;
    }
  }

  function renderQueue() {
    api("/api/rooms/" + ROOM_CODE + "/queue").then(function (res) {
      queue = res.items || [];
      var html = '<div class="cc-panel__search"><button class="cc-btn cc-btn--block" id="addQueueItem">+ Add to Queue</button></div>';
      if (queue.length === 0) {
        html += '<div class="cc-empty">No items in the service queue yet.</div>';
      } else {
        html += '<div class="cc-panel__list">';
        var qidx = queueIndex();
        for (var i = 0; i < queue.length; i++) {
          var q = queue[i];
          var label = queueItemLabel(q);
          var active = (qidx === i) ? " active" : "";
          html += '<div class="cc-item cc-queue-item' + active + '" data-queue-idx="' + i + '">' +
            '<span class="cc-queue-item__num">' + (qidx === i ? "▶" : (i + 1)) + '</span>' +
            '<div class="cc-item__body"><div class="cc-item__title">' + esc(label) + '</div>' +
            '<div class="cc-item__sub">' + esc(q.item_type) + '</div></div>' +
            '<div class="cc-item__actions">' +
              '<button class="cc-item__btn" data-queue-up="' + q.id + '">▲</button>' +
              '<button class="cc-item__btn" data-queue-down="' + q.id + '">▼</button>' +
              '<button class="cc-item__btn" data-queue-preview="' + i + '">Prev</button>' +
              '<button class="cc-item__btn cc-item__btn--present" data-queue-go="' + i + '">Go</button>' +
              '<button class="cc-item__btn cc-item__btn--danger" data-queue-del="' + q.id + '">✕</button>' +
            '</div></div>';
        }
        html += '</div>';
        html += '<div style="margin-top:12px"><button class="cc-btn cc-btn--ghost" id="clearQueue">Clear Queue</button></div>';
      }
      panel.innerHTML = html;

      el("addQueueItem").onclick = openAddQueueModal;
      var clearQ = el("clearQueue");
      if (clearQ) clearQ.onclick = function () {
        api("/api/rooms/" + ROOM_CODE + "/queue", "POST", { action: "clear" }).then(renderQueue);
      };
      panel.querySelectorAll("[data-queue-go]").forEach(function (b) {
        b.onclick = function () { cmd("goto_queue", { index: parseInt(b.dataset.queueGo) }); };
      });
      panel.querySelectorAll("[data-queue-preview]").forEach(function (b) {
        b.onclick = function () { previewQueueItem(parseInt(b.dataset.queuePreview)); };
      });
      panel.querySelectorAll("[data-queue-del]").forEach(function (b) {
        b.onclick = function () {
          api("/api/rooms/" + ROOM_CODE + "/queue", "POST", { action: "remove", id: parseInt(b.dataset.queueDel) }).then(renderQueue);
        };
      });
      panel.querySelectorAll("[data-queue-up]").forEach(function (b) {
        b.onclick = function () { reorderQueue(parseInt(b.dataset.queueUp), -1); };
      });
      panel.querySelectorAll("[data-queue-down]").forEach(function (b) {
        b.onclick = function () { reorderQueue(parseInt(b.dataset.queueDown), 1); };
      });
      syncQueueUI();
    });
  }

  function reorderQueue(itemId, dir) {
    var ids = queue.map(function (q) { return q.id; });
    var idx = ids.indexOf(itemId);
    if (idx < 0) return;
    var newIdx = idx + dir;
    if (newIdx < 0 || newIdx >= ids.length) return;
    ids.splice(idx, 1);
    ids.splice(newIdx, 0, itemId);
    api("/api/rooms/" + ROOM_CODE + "/queue", "POST", { action: "reorder", ids: ids }).then(renderQueue);
  }

  function queueItemLabel(q) {
    if (q.item_type === "song") return q.data.title || "Song #" + q.ref_id;
    if (q.item_type === "scripture") return q.data.reference || "Scripture";
    if (q.item_type === "announcement") return q.data.title || "Announcement";
    if (q.item_type === "image") return q.data.title || "Image";
    if (q.item_type === "countdown") return "Countdown: " + (q.data.title || q.data.seconds + "s");
    return q.item_type;
  }

  function renderLibrary() {
    var html = '<div class="cc-panel__search"><input class="cc-input" id="libSearch" placeholder="Search all content…"></div>';
    html += '<div class="cc-panel__list" id="libList"></div>';
    panel.innerHTML = html;
    var search = el("libSearch");
    function doSearch() {
      var q = search.value;
      Promise.all([
        api("/api/songs?q=" + encodeURIComponent(q)),
        api("/api/announcements?q=" + encodeURIComponent(q)),
        api("/api/images?q=" + encodeURIComponent(q)),
      ]).then(function (results) {
        var songs = results[0].songs || [];
        var anns = results[1].announcements || [];
        var imgs = results[2].images || [];
        var html = "";
        songs.forEach(function (s) {
          html += itemCard("song", s.id, s.title, s.author || "Song", "♪", "present_song");
        });
        anns.forEach(function (a) {
          html += itemCard("announcement", a.id, a.title, "Announcement", "A", "present_announcement");
        });
        imgs.forEach(function (i) {
          html += '<div class="cc-item">' +
            '<img class="cc-item__thumb" src="' + i.url + '">' +
            '<div class="cc-item__body"><div class="cc-item__title">' + esc(i.title || "Image") + '</div></div>' +
            '<div class="cc-item__actions"><button class="cc-item__btn cc-item__btn--present" data-present-image="' + i.id + '">Present</button></div>' +
          '</div>';
        });
        if (!html) html = '<div class="cc-empty">No results.</div>';
        el("libList").innerHTML = html;
        bindPresentButtons();
      });
    }
    search.oninput = doSearch;
    doSearch();
  }

  function renderSongs() {
    var html = '<div class="cc-panel__search"><input class="cc-input" id="songSearch" placeholder="Try \'nnbh 8\', \'ybh 12\' or a title…"></div>';
    html += '<select class="cc-input" id="songLanguage" style="margin:0 0 10px;width:100%">' +
      '<option value="">All languages</option>' +
      '<option value="Yoruba">Yoruba</option>' +
      '<option value="English">English</option></select>';
    html += '<button class="cc-btn cc-btn--block" id="newSongBtn" style="margin-bottom:12px">+ New Song</button>';
    html += '<div class="cc-panel__list" id="songList"></div>';
    panel.innerHTML = html;
    var search = el("songSearch");
    var lang = el("songLanguage");
    function doSearch() {
      api("/api/songs?q=" + encodeURIComponent(search.value) + "&language=" + encodeURIComponent(lang.value)).then(function (res) {
        var songs = res.songs || [];
        var html = "";
        songs.forEach(function (s) {
          html += '<div class="cc-item">' +
            '<div class="cc-item__icon">♪</div>' +
            '<div class="cc-item__body"><div class="cc-item__title">' + esc(s.title) + '</div>' +
            '<div class="cc-item__sub">' + esc(s.author || "") +
            (s.source ? ' · ' + esc(s.source) : '') +
            (s.language ? ' · ' + esc(s.language) : '') +
            '</div></div>' +
            '<div class="cc-item__actions">' +
              '<button class="cc-item__btn" data-edit-song="' + s.id + '">Edit</button>' +
              '<button class="cc-item__btn" data-preview-song="' + s.id + '">Prev</button>' +
              '<button class="cc-item__btn cc-item__btn--present" data-present-song="' + s.id + '">Present</button>' +
              '<button class="cc-item__btn cc-item__btn--danger" data-addq-song="' + s.id + '">+Q</button>' +
            '</div></div>';
        });
        if (!html) html = '<div class="cc-empty">No songs found.</div>';
        el("songList").innerHTML = html;
        bindPresentButtons();
        panel.querySelectorAll("[data-edit-song]").forEach(function (b) {
          b.onclick = function () { openSongEditor(parseInt(b.dataset.editSong)); };
        });
        panel.querySelectorAll("[data-preview-song]").forEach(function (b) {
          b.onclick = function () { previewSong(parseInt(b.dataset.previewSong)); };
        });
        panel.querySelectorAll("[data-addq-song]").forEach(function (b) {
          b.onclick = function () {
            var sid = parseInt(b.dataset.addqSong);
            api("/api/songs/" + sid).then(function (res) {
              var s = res.song;
              api("/api/rooms/" + ROOM_CODE + "/queue", "POST", {
                action: "add", item_type: "song", ref_id: sid, data: { title: s.title }
              }).then(renderQueue);
            });
          };
        });
      });
    }
    search.oninput = doSearch;
    lang.onchange = doSearch;
    el("newSongBtn").onclick = function () { openSongEditor(null); };
    doSearch();
  }

  function renderScripture() {
    var html =
      '<div class="cc-sc-trans">' +
        '<label class="cc-sc-trans__label" for="scTranslation">Translation</label>' +
        '<select class="cc-input" id="scTranslation"></select>' +
      "</div>" +
      '<div class="cc-panel__search cc-ref-input"><input class="cc-input" id="scInput" placeholder="Search or type a reference…" autocomplete="off" autocorrect="off" autocapitalize="off" spellcheck="false"></div>' +
      '<div id="scChips"></div>' +
      '<div id="scRoot"><div class="cc-empty">Loading Bible index…</div></div>' +
      '<div class="cc-sc-footer" id="scFooter" style="display:none">' +
        '<div class="cc-sc-mode" id="scMode"></div>' +
        '<div class="cc-sc-sel" id="scSel"></div>' +
        '<div class="cc-sc-actions">' +
          '<button class="cc-btn" id="scPreview">Preview</button>' +
          '<button class="cc-btn" id="scQueue">+ Queue</button>' +
          '<button class="cc-btn cc-btn--accent" id="scPresent">Present</button>' +
        '</div>' +
      '</div>';
    panel.innerHTML = html;
    paintTranslationSelector();

    var input = el("scInput");
    if (!scIndex) {
      loadScriptureIndex();
    } else {
      paintScripture();
    }
    input.oninput = function () {
      sc.input = input.value;
      if (!scIndex) return;
      var chips = el("scChips");
      var matches = BiblePicker.resolveInput(sc.input, scIndex);
      if (sc.input.trim() && matches.length) {
        chips.innerHTML = '<div class="cc-sc-chips">' + matches.map(function (b) {
          return '<button class="cc-sc-chip" data-sc-book="' + esc(b.name) + '">' + esc(b.name) + "</button>";
        }).join("") + "</div>";
        chips.querySelectorAll("[data-sc-book]").forEach(function (b) {
          b.onclick = function () {
            pickBook(b.dataset.scBook);
          };
        });
      } else {
        chips.innerHTML = "";
      }
      if (!sc.input.trim()) {
        chips.innerHTML = "";
        paintScripture();
      }
    };
    input.onkeydown = function (e) {
      if (e.key !== "Enter") return;
      var r = scIndex && BiblePicker.parse(sc.input, scIndex);
      if (!r || !r.chapter) return;
      sc.book = r.book; sc.chapter = r.chapter; sc.verse = r.verse || null;
      presentScriptureSelection();
    };
  }

  function loadScriptureIndex() {
    var reqTrans = activeTranslation;
    api("/api/scripture/index?translation=" + encodeURIComponent(reqTrans)).then(function (res) {
      scIndex = (res.books || []).map(function (b) {
        return { name: b.name, testament: b.testament, aliases: b.aliases, chapters: b.chapters };
      });
      scIndexTranslation = reqTrans;
      sc.testament = BiblePicker.getTestament("OT");
      sc.mode = BiblePicker.getMode("instant");
      paintScripture();
    }).catch(function () {
      el("scRoot").innerHTML = '<div class="cc-empty">Could not load the Bible.</div>';
    });
  }

  function pickBook(name) {
    sc.book = name; sc.chapter = null; sc.verse = null;
    var input = el("scInput");
    if (input) input.value = "";
    sc.input = "";
    paintScripture();
  }

  function pickChapter(n) {
    sc.chapter = n; sc.verse = null;
    paintScripture();
  }

  function pickVerse(n) {
    sc.verse = n;
    pushRecent();
    if (sc.mode === "instant") {
      presentScriptureSelection();
    } else {
      openScriptureVersePreview("instant_present");
    }
    paintScripture();
  }

  function currentRef() {
    if (!sc.book) return "";
    var ref = sc.book + (sc.chapter ? " " + sc.chapter : "");
    if (sc.chapter && sc.verse) ref += ":" + sc.verse;
    return ref;
  }

  function presentScriptureSelection() {
    if (!sc.book || !sc.chapter) return;
    var payload = { book: sc.book, chapter: sc.chapter };
    if (sc.verse) payload.verse = sc.verse;
    pushRecent();
    cmd("present_scripture", payload);
  }

  function pushRecent() {
    var ref = currentRef();
    if (ref) BiblePicker.pushRecent({ ref: ref, book: sc.book, chapter: sc.chapter, verse: sc.verse });
  }

  function openScriptureVersePreview(from) {
    var ref = currentRef();
    if (!sc.chapter) return;
    var url = "/api/scripture/chapter?book=" + encodeURIComponent(sc.book) + "&chapter=" + sc.chapter +
      (sc.verse ? "&verse=" + sc.verse : "");
    api(url).then(function (res) {
      if (res.error) return;
      var slides = res.slides || [];
      var label = sc.verse ? (res.reference + ":" + sc.verse) : res.reference;
      openModal("Preview", '<div class="cc-field"><div class="cc-mini-preview cc-mini-preview--lg"><div class="cc-mini-preview__inner" id="scPvInner"></div></div></div>' +
        '<button class="cc-btn cc-btn--accent cc-btn--block" id="scPvPresent">Present</button>' +
        '<button class="cc-btn cc-btn--ghost cc-btn--block" id="scPvClose" style="margin-top:8px">Close</button>');
      renderPreviewInto(el("scPvInner"), "scripture", { reference: res.reference, slides: slides }, 0, stateStyles());
      el("scPvPresent").onclick = function () { presentScriptureSelection(); closeModal(); };
      el("scPvClose").onclick = closeModal;
    });
  }

  function paintScripture() {
    if (!scIndex) return;
    var root = el("scRoot");
    if (!root) return;
    if (!sc.book) {
      renderScriptureHome(root);
      renderScriptureFooter();
      return;
    }
    var book = bookByName(sc.book);
    if (!book) { sc.book = null; renderScriptureHome(root); return; }
    var crumb = '<div class="cc-breadcrumb">' +
      '<button class="cc-breadcrumb__back" data-sc-crumb="home" title="Books">‹</button>' +
      '<button class="cc-breadcrumb__seg" data-sc-crumb="book">' + esc(book.name) + "</button>" +
      (sc.chapter ? '<button class="cc-breadcrumb__seg" data-sc-crumb="chapter">' + sc.chapter + "</button>" : "") +
      (sc.chapter && sc.verse ? '<button class="cc-breadcrumb__seg cc-breadcrumb__seg--cur" data-sc-crumb="verse">' + sc.verse + "</button>" : "") +
      "</div>";
    if (!sc.chapter) {
      var chapHtml = crumb + '<div class="cc-sc-grid cc-sc-grid--chapters"><div class="cc-grid-label">Chapter</div>';
      for (var c = 1; c <= book.chapters.length; c++) {
        chapHtml += '<button class="cc-sc-cell cc-sc-cell--chapter" data-sc-chapter="' + c + '">' + c + "</button>";
      }
      chapHtml += "</div>";
      root.innerHTML = chapHtml;
      bindGridCrumbs(root);
      root.querySelectorAll("[data-sc-chapter]").forEach(function (b) {
        b.onclick = function () { pickChapter(parseInt(b.dataset.scChapter)); };
      });
    } else {
      var vcount = book.chapters[sc.chapter - 1] || 0;
      var verseHtml = crumb + '<div class="cc-sc-grid cc-sc-grid--verses"><div class="cc-grid-label">Verse &middot; ' + book.name + " " + sc.chapter + "</div>";
      for (var v = 1; v <= vcount; v++) {
        var active = sc.verse === v ? " active" : "";
        verseHtml += '<button class="cc-sc-cell cc-sc-cell--verse' + active + '" data-sc-verse="' + v + '">' + v + "</button>";
      }
      verseHtml += "</div>";
      root.innerHTML = verseHtml;
      bindGridCrumbs(root);
      root.querySelectorAll("[data-sc-verse]").forEach(function (b) {
        b.onclick = function () { pickVerse(parseInt(b.dataset.scVerse)); };
      });
    }
    renderScriptureFooter();
  }

  function renderScriptureHome(root) {
    var testament = BiblePicker.getTestament("OT");
    var recent = BiblePicker.getRecent();
    var html = '<div class="cc-sc-togg">' +
      '<button class="cc-btn' + (testament === "OT" ? " active" : "") + '" data-sc-test="OT">Old Testament</button>' +
      '<button class="cc-btn' + (testament === "NT" ? " active" : "") + '" data-sc-test="NT">New Testament</button>' +
      "</div>";
    if (recent.length) {
      html += '<div class="cc-sc-recent">' + recent.map(function (r) {
        return '<button class="cc-sc-chip" data-sc-recent="' + r.ref + '" data-sc-rb="' + esc(r.book) + '" data-sc-rc="' + (r.chapter || "") + '" data-sc-rv="' + (r.verse || "") + '">' + esc(r.ref) + "</button>";
      }).join("") + "</div>";
    }
    var books = scIndex.filter(function (b) { return b.testament === testament; });
    html += '<div class="cc-sc-grid cc-sc-grid--books">' + books.map(function (b) {
      return '<button class="cc-sc-cell cc-sc-cell--book" data-sc-book="' + esc(b.name) + '">' + esc(b.name) + "</button>";
    }).join("") + "</div>";
    root.innerHTML = html;
    root.querySelectorAll("[data-sc-test]").forEach(function (b) {
      b.onclick = function () { BiblePicker.setTestament(b.dataset.scTest); renderScriptureHome(root); };
    });
    root.querySelectorAll("[data-sc-book]").forEach(function (b) {
      b.onclick = function () { pickBook(b.dataset.scBook); };
    });
    root.querySelectorAll("[data-sc-recent]").forEach(function (b) {
      b.onclick = function () {
        sc.book = b.dataset.scRb;
        sc.chapter = b.dataset.scRc ? parseInt(b.dataset.scRc) : null;
        sc.verse = b.dataset.scRv ? parseInt(b.dataset.scRv) : null;
        var input = el("scInput");
        if (input) input.value = "";
        sc.input = "";
        paintScripture();
      };
    });
  }

  function bindGridCrumbs(root) {
    root.querySelectorAll("[data-sc-crumb]").forEach(function (b) {
      b.onclick = function () {
        var crumb = b.dataset.scCrumb;
        if (crumb === "home") { sc.book = null; sc.chapter = null; sc.verse = null; }
        else if (crumb === "book") { sc.chapter = null; sc.verse = null; }
        else if (crumb === "chapter") { sc.verse = null; }
        paintScripture();
      };
    });
  }

  function bookByName(name) {
    for (var i = 0; i < scIndex.length; i++) {
      if (scIndex[i].name === name) return scIndex[i];
    }
    return null;
  }

  function renderScriptureFooter() {
    var footer = el("scFooter");
    if (!sc.book) {
      footer.style.display = "none";
      return;
    }
    footer.style.display = "";
    var mode = BiblePicker.getMode("instant");
    sc.mode = mode;
    var modeHtml = '<label>Tap-to-present</label><div class="cc-sc-mode__seg">' +
      '<button class="cc-btn' + (mode === "instant" ? " active" : "") + '" data-sc-mode="instant">Instant</button>' +
      '<button class="cc-btn' + (mode === "stage" ? " active" : "") + '" data-sc-mode="stage">Stage</button>' +
      "</div>";
    var modeEl = el("scMode");
    if (modeEl) {
      modeEl.innerHTML = modeHtml;
      modeEl.querySelectorAll("[data-sc-mode]").forEach(function (b) {
        b.onclick = function () {
          BiblePicker.setMode(b.dataset.scMode);
          renderScriptureFooter();
        };
      });
    }
    var sel = el("scSel");
    if (sel) sel.textContent = sc.chapter ? (currentRef() || "—") : sc.book + " — pick a chapter";
    var ready = !!sc.chapter;
    [el("scPreview"), el("scQueue"), el("scPresent")].forEach(function (b) {
      if (b) b.disabled = !ready;
    });
    if (el("scPreview")) el("scPreview").onclick = function () { if (ready) openScriptureVersePreview(); };
    if (el("scQueue")) el("scQueue").onclick = function () { if (ready) addScriptureSelectionToQueue(); };
    if (el("scPresent")) el("scPresent").onclick = function () { if (ready) presentScriptureSelection(); };
  }

  function addScriptureSelectionToQueue() {
    var ref = currentRef();
    if (!sc.chapter || !ref) return;
    api("/api/rooms/" + ROOM_CODE + "/queue", "POST", {
      action: "add", item_type: "scripture", data: { reference: ref }
    }).then(function () { showToast("Added " + ref + " to queue"); });
  }

  function renderImages() {
    var html = '<div class="cc-panel__search"><input class="cc-input" id="imgSearch" placeholder="Search images…"></div>';
    html += '<button class="cc-btn cc-btn--block" id="uploadBtn" style="margin-bottom:12px">+ Upload Image</button>';
    html += '<input type="file" id="imgFile" accept="image/*" style="display:none">';
    html += '<div class="cc-panel__list" id="imgList"></div>';
    panel.innerHTML = html;
    var search = el("imgSearch");
    function doSearch() {
      api("/api/images?q=" + encodeURIComponent(search.value)).then(function (res) {
        var imgs = res.images || [];
        var html = "";
        imgs.forEach(function (i) {
          html += '<div class="cc-item">' +
            '<img class="cc-item__thumb" src="' + i.url + '">' +
            '<div class="cc-item__body"><div class="cc-item__title">' + esc(i.title || "Image") + '</div></div>' +
            '<div class="cc-item__actions">' +
              '<button class="cc-item__btn" data-preview-image="' + i.id + '">Prev</button>' +
              '<button class="cc-item__btn cc-item__btn--present" data-present-image="' + i.id + '">Present</button>' +
              '<button class="cc-item__btn cc-item__btn--danger" data-addq-image="' + i.id + '">+Q</button>' +
              '<button class="cc-item__btn cc-item__btn--danger" data-del-image="' + i.id + '">Del</button>' +
            '</div></div>';
        });
        if (!html) html = '<div class="cc-empty">No images uploaded yet.</div>';
        el("imgList").innerHTML = html;
        bindPresentButtons();
        panel.querySelectorAll("[data-preview-image]").forEach(function (b) {
          b.onclick = function () { previewImage(parseInt(b.dataset.previewImage)); };
        });
        panel.querySelectorAll("[data-addq-image]").forEach(function (b) {
          b.onclick = function () {
            var iid = parseInt(b.dataset.addqImage);
            api("/api/images/" + iid).then(function (res) {
              api("/api/rooms/" + ROOM_CODE + "/queue", "POST", {
                action: "add", item_type: "image", ref_id: iid, data: { title: res.image.title }
              }).then(renderQueue);
            });
          };
        });
        panel.querySelectorAll("[data-del-image]").forEach(function (b) {
          b.onclick = function () {
            var iid = parseInt(b.dataset.delImage);
            if (!window.confirm("Delete this image? This cannot be undone.")) return;
            api("/api/images/" + iid, "DELETE").then(function (res) {
              if (res.error) { showToast(res.error); return; }
              showToast("Image deleted");
              doSearch();
            });
          };
        });
      });
    }
    search.oninput = doSearch;
    doSearch();
    var fileInput = el("imgFile");
    el("uploadBtn").onclick = function () { fileInput.click(); };
    fileInput.onchange = function () {
      var f = fileInput.files[0];
      if (!f) return;
      var fd = new FormData();
      fd.append("file", f);
      fd.append("title", f.name.replace(/\.[^.]+$/, ""));
      api("/api/images/upload", "POST", fd).then(doSearch);
      fileInput.value = "";
    };
  }

  function renderAnnouncements() {
    var html = '<div class="cc-panel__search"><input class="cc-input" id="annSearch" placeholder="Search announcements…"></div>';
    html += '<button class="cc-btn cc-btn--block" id="newAnnBtn" style="margin-bottom:12px">+ New Announcement</button>';
    html += '<div class="cc-panel__list" id="annList"></div>';
    panel.innerHTML = html;
    var search = el("annSearch");
    function doSearch() {
      api("/api/announcements?q=" + encodeURIComponent(search.value)).then(function (res) {
        var anns = res.announcements || [];
        var html = "";
        anns.forEach(function (a) {
          html += '<div class="cc-item">' +
            '<div class="cc-item__icon">A</div>' +
            '<div class="cc-item__body"><div class="cc-item__title">' + esc(a.title) + '</div>' +
            '<div class="cc-item__sub">' + esc((a.body || "").substring(0, 50)) + '</div></div>' +
            '<div class="cc-item__actions">' +
              '<button class="cc-item__btn" data-edit-ann="' + a.id + '">Edit</button>' +
              '<button class="cc-item__btn" data-preview-ann="' + a.id + '">Prev</button>' +
              '<button class="cc-item__btn cc-item__btn--present" data-present-ann="' + a.id + '">Present</button>' +
              '<button class="cc-item__btn cc-item__btn--danger" data-addq-ann="' + a.id + '">+Q</button>' +
            '</div></div>';
        });
        if (!html) html = '<div class="cc-empty">No announcements yet.</div>';
        el("annList").innerHTML = html;
        bindPresentButtons();
        panel.querySelectorAll("[data-edit-ann]").forEach(function (b) {
          b.onclick = function () { openAnnEditor(parseInt(b.dataset.editAnn)); };
        });
        panel.querySelectorAll("[data-preview-ann]").forEach(function (b) {
          b.onclick = function () { previewAnnouncement(parseInt(b.dataset.previewAnn)); };
        });
        panel.querySelectorAll("[data-addq-ann]").forEach(function (b) {
          b.onclick = function () {
            var aid = parseInt(b.dataset.addqAnn);
            api("/api/announcements/" + aid).then(function (res) {
              api("/api/rooms/" + ROOM_CODE + "/queue", "POST", {
                action: "add", item_type: "announcement", ref_id: aid, data: { title: res.announcement.title }
              }).then(renderQueue);
            });
          };
        });
      });
    }
    search.oninput = doSearch;
    doSearch();
    el("newAnnBtn").onclick = function () { openAnnEditor(null); };
  }

  // --- Style editor (font / text size / background) ---
  var BG_COLORS = ["#05070A", "#0B0F14", "#000000", "#FFFFFF", "#1E2A38"];

  function renderStyle() {
    var st = JSON.parse(JSON.stringify(stateStyles()));
    var swatches = BG_COLORS.map(function (hex) {
      return '<button class="cc-bg-swatch' + (st.background.type === "color" && st.background.value === hex ? " active" : "") +
        '" data-bg-color="' + hex + '" style="background:' + hex + '"></button>';
    }).join("");
    var html =
      '<div class="cc-field"><div class="cc-mini-preview"><div class="cc-mini-preview__inner" id="styleMiniInner"></div></div></div>' +
      '<div class="cc-field"><label>Font</label><select class="cc-input" id="stFont">' +
        '<option value="default">System (default)</option>' +
        '<option value="serif">Serif</option>' +
        '<option value="sans">Sans</option>' +
      "</select></div>" +
      '<div class="cc-field"><label>Text size</label><div class="cc-size-seg" id="stSize">' +
        '<button class="cc-btn' + (st.size === "sm" ? " active" : "") + '" data-size="sm">Small</button>' +
        '<button class="cc-btn' + (st.size === "md" ? " active" : "") + '" data-size="md">Medium</button>' +
        '<button class="cc-btn' + (st.size === "lg" ? " active" : "") + '" data-size="lg">Large</button>' +
      "</div></div>" +
      '<div class="cc-field"><label>Background color</label><div class="cc-bg-colors" id="stBgColors">' + swatches + "</div></div>" +
      '<div class="cc-field"><label>Or background image</label><div class="cc-bg-images" id="stBgImages"></div></div>' +
      '<button class="cc-btn cc-btn--accent cc-btn--block" id="stSave">Save to Display</button>' +
      '<div class="cc-empty" id="stSaved" style="display:none">Saved ✓</div>';
    panel.innerHTML = html;

    el("stFont").value = st.font;

    function repaint() { renderPreviewInto(el("styleMiniInner"), styleSample().ct, styleSample().content, 0, st); }
    repaint();

    function paintSwatches() {
      el("stBgColors").querySelectorAll("[data-bg-color]").forEach(function (b) {
        b.classList.toggle("active", st.background.type === "color" && st.background.value === b.dataset.bgColor);
      });
      el("stBgImages").querySelectorAll("[data-bg-url]").forEach(function (b) {
        b.classList.toggle("active", st.background.type === "image" && st.background.value === b.dataset.bgUrl);
      });
    }

    el("stFont").onchange = function () { st.font = el("stFont").value; repaint(); };
    el("stSize").querySelectorAll("[data-size]").forEach(function (b) {
      b.onclick = function () {
        st.size = b.dataset.size;
        el("stSize").querySelectorAll("[data-size]").forEach(function (x) { x.classList.toggle("active", x === b); });
        repaint();
      };
    });

    loadThumbs();

    function loadThumbs() {
      api("/api/images").then(function (res) {
        paintThumbs(res.images || []);
      });
    }

    function paintThumbs(imgs) {
      var imgHtml = "";
      imgs.forEach(function (i) {
        imgHtml += '<span class="cc-bg-thumb-wrap">' +
          '<button class="cc-bg-thumb" data-bg-url="' + esc(i.url) + '" title="' + esc(i.title || "image") + '"' +
          ' style="background-image:url(\'' + i.url + '\')"></button>' +
          '<button class="cc-bg-thumb-del" data-del-image="' + i.id + '" title="Delete image">×</button>' +
          "</span>";
      });
      el("stBgImages").innerHTML = imgHtml || '<div class="cc-empty">No images uploaded.</div>';
      paintSwatches();
      el("stBgImages").querySelectorAll("[data-bg-url]").forEach(function (b) {
        b.onclick = function () {
          st.background = { type: "image", value: b.dataset.bgUrl };
          paintSwatches();
          repaint();
        };
      });
      el("stBgImages").querySelectorAll("[data-del-image]").forEach(function (b) {
        b.onclick = function () {
          var iid = parseInt(b.dataset.delImage);
          if (!window.confirm("Delete this background image? This cannot be undone.")) return;
          api("/api/images/" + iid, "DELETE").then(function (res) {
            if (res.error) { showToast(res.error); return; }
            // If the draft background referenced the deleted (unused) image,
            // fall the draft back to a plain background before reloading.
            if (st.background.type === "image") {
              for (var k = 0; k < imgs.length; k++) {
                if (imgs[k].id === iid) {
                  st.background = { type: "color", value: BG_COLORS[0] };
                  break;
                }
              }
            }
            showToast("Image deleted");
            loadThumbs();
            repaint();
          });
        };
      });
    }

    el("stBgColors").querySelectorAll("[data-bg-color]").forEach(function (b) {
      b.onclick = function () {
        st.background = { type: "color", value: b.dataset.bgColor };
        paintSwatches();
        repaint();
      };
    });

    el("stSave").onclick = function () {
      cmd("set_style", { styles: st });
      el("stSaved").style.display = "";
    };
  }

  // --- Preview before sending ---
  function openPreview(ct, content, title) {
    var body =
      '<div class="cc-field"><div class="cc-mini-preview cc-mini-preview--lg"><div class="cc-mini-preview__inner" id="pvInner"></div></div></div>' +
      '<button class="cc-btn cc-btn--ghost cc-btn--block" id="pvClose">Close</button>';
    openModal("Preview — " + (title || ct), body);
    renderPreviewInto(el("pvInner"), ct, content, 0, stateStyles());
    el("pvClose").onclick = closeModal;
  }

  function songSlides(s) {
    var slides = [];
    (s.sections || []).forEach(function (sec) {
      var paras = String(sec.lyrics || "").split(/\n\n+/).map(function (p) { return p.trim(); }).filter(Boolean);
      (paras.length ? paras : [String(sec.lyrics || "").trim()]).forEach(function (p) {
        var lines = p.split("\n");
        for (var i = 0; i < lines.length; i += 6) {
          slides.push({ label: sec.label, text: lines.slice(i, i + 6).join("\n") });
        }
      });
    });
    return slides;
  }

  function previewSong(id) {
    api("/api/songs/" + id).then(function (res) {
      var s = res.song || {};
      openPreview("song", { title: s.title, author: s.author, slides: songSlides(s) }, s.title);
    });
  }

  function previewScripture(ref) {
    api("/api/scripture/parse?ref=" + encodeURIComponent(ref)).then(function (res) {
      if (res.error) return;
      openPreview("scripture", { reference: res.reference, slides: res.slides }, res.reference);
    });
  }

  function previewAnnouncement(id) {
    api("/api/announcements/" + id).then(function (res) {
      var a = res.announcement || {};
      openPreview("announcement", { title: a.title, body: a.body }, a.title);
    });
  }

  function previewImage(id) {
    api("/api/images/" + id).then(function (res) {
      var i = res.image || {};
      openPreview("image", { url: i.url, title: i.title }, i.title || "Image");
    });
  }

  function previewQueueItem(i) {
    var q = queue[i];
    if (!q) return;
    if (q.item_type === "song" && q.ref_id) { previewSong(q.ref_id); }
    else if (q.item_type === "scripture" && q.data.reference) { previewScripture(q.data.reference); }
    else if (q.item_type === "announcement" && q.ref_id) { previewAnnouncement(q.ref_id); }
    else if (q.item_type === "image" && q.ref_id) { previewImage(q.ref_id); }
    else if (q.item_type === "countdown") {
      openPreview("countdown", { title: q.data.title || "SERVICE STARTS IN" }, "Countdown " + (q.data.seconds || 0) + "s");
    }
  }

  // --- Present buttons binding ---
  function bindPresentButtons() {
    panel.querySelectorAll("[data-present-song]").forEach(function (b) {
      b.onclick = function () { cmd("present_song", { id: parseInt(b.dataset.presentSong) }); };
    });
    panel.querySelectorAll("[data-present-image]").forEach(function (b) {
      b.onclick = function () { cmd("present_image", { id: parseInt(b.dataset.presentImage) }); };
    });
    panel.querySelectorAll("[data-present-ann]").forEach(function (b) {
      b.onclick = function () { cmd("present_announcement", { id: parseInt(b.dataset.presentAnn) }); };
    });
  }

  function itemCard(type, id, title, sub, icon, action) {
    return '<div class="cc-item">' +
      '<div class="cc-item__icon">' + icon + '</div>' +
      '<div class="cc-item__body"><div class="cc-item__title">' + esc(title) + '</div>' +
      '<div class="cc-item__sub">' + esc(sub) + '</div></div>' +
      '<div class="cc-item__actions">' +
        '<button class="cc-item__btn cc-item__btn--present" data-present-' + type + '="' + id + '">Present</button>' +
      '</div></div>';
  }

  // --- Modals ---
  function openModal(title, bodyHtml) {
    var root = el("modalRoot");
    root.innerHTML = '<div class="cc-modal-overlay" id="modalOverlay"><div class="cc-modal">' +
      '<div class="cc-modal__header"><div class="cc-modal__title">' + esc(title) + '</div>' +
      '<button class="cc-modal__close" id="modalClose">✕</button></div>' +
      '<div id="modalBody">' + bodyHtml + '</div></div></div>';
    el("modalClose").onclick = closeModal;
    el("modalOverlay").onclick = function (e) { if (e.target === this) closeModal(); };
  }

  function closeModal() { el("modalRoot").innerHTML = ""; }

  // Countdown quick-launch
  function openCountdownModal() {
    var running = state && state.countdown && state.countdown.endTime;
    var body = '<div class="cc-field"><label>Title</label><input class="cc-input" id="cdTitle" placeholder="SERVICE STARTS IN"></div>' +
      '<div class="cc-field"><label>Duration</label><div class="cc-countdown-presets" id="cdPresets">' +
      "<button class=\"cc-btn\" data-cd-sec=\"120\">2:00</button>" +
      "<button class=\"cc-btn\" data-cd-sec=\"300\">5:00</button>" +
      "<button class=\"cc-btn\" data-cd-sec=\"600\">10:00</button>" +
      "<button class=\"cc-btn\" data-cd-sec=\"900\">15:00</button>" +
      "</div></div>" +
      '<div class="cc-field"><label>Or exact seconds</label><input class="cc-input" id="cdSecs" type="number" min="1" value="300"></div>' +
      '<button class="cc-btn cc-btn--accent cc-btn--block" id="cdStart">Start Countdown</button>';
    if (running) {
      body += '<button class="cc-btn cc-btn--danger cc-btn--block" id="cdStop" style="margin-top:8px">Stop Countdown</button>';
    }
    openModal("Countdown", body);
    el("cdPresets").querySelectorAll("[data-cd-sec]").forEach(function (b) {
      b.onclick = function () { el("cdSecs").value = parseInt(b.dataset.cdSec); };
    });
    el("cdStart").onclick = function () {
      var secs = parseInt(el("cdSecs").value) || 300;
      cmd("present_countdown", { seconds: secs, title: el("cdTitle").value.trim() });
      closeModal();
    };
    var stop = el("cdStop");
    if (stop) stop.onclick = function () { cmd("stop_countdown"); closeModal(); };
  }

  function openAddQueueModal() {
    var body = '<div class="cc-field"><label>Type</label><select id="qType">' +
      '<option value="song">Song</option>' +
      '<option value="scripture">Scripture</option>' +
      '<option value="announcement">Announcement</option>' +
      '<option value="image">Image</option>' +
      '<option value="countdown">Countdown</option>' +
      "</select></div>" +
      '<div id="qForm"></div>' +
      '<button class="cc-btn cc-btn--accent cc-btn--block" id="qAdd">Add to Queue</button>';
    openModal("Add to Queue", body);
    var qType = el("qType");
    var qForm = el("qForm");
    function renderForm() {
      var t = qType.value;
      if (t === "song") {
        qForm.innerHTML = '<div class="cc-field"><label>Search & select song</label><input class="cc-input" id="qSongSearch" placeholder="Search…"></div><div id="qSongResults"></div>';
        var ss = el("qSongSearch");
        ss.oninput = function () {
          api("/api/songs?q=" + encodeURIComponent(ss.value)).then(function (res) {
            var html = "";
            (res.songs || []).forEach(function (s) {
              html += '<div class="cc-ref-result" data-q-song="' + s.id + '" data-q-title="' + esc(s.title) + '">' + esc(s.title) + " — " + esc(s.author || "") + "</div>";
            });
            el("qSongResults").innerHTML = html || '<div class="cc-empty">No songs.</div>';
            el("qSongResults").querySelectorAll("[data-q-song]").forEach(function (r) {
              r.onclick = function () {
                api("/api/rooms/" + ROOM_CODE + "/queue", "POST", {
                  action: "add", item_type: "song", ref_id: parseInt(r.dataset.qSong), data: { title: r.dataset.qTitle }
                }).then(function () { closeModal(); switchTab("queue"); });
              };
            });
          });
        };
        ss.oninput();
      } else if (t === "scripture") {
        qForm.innerHTML = '<div class="cc-field"><label>Reference</label><input class="cc-input" id="qRef" placeholder="e.g. John 3:16"></div>';
      } else if (t === "announcement") {
        qForm.innerHTML = '<div class="cc-field"><label>Select announcement</label><div id="qAnnResults"></div></div>';
        api("/api/announcements").then(function (res) {
          var html = "";
          (res.announcements || []).forEach(function (a) {
            html += '<div class="cc-ref-result" data-q-ann="' + a.id + '" data-q-title="' + esc(a.title) + '">' + esc(a.title) + "</div>";
          });
          el("qAnnResults").innerHTML = html || '<div class="cc-empty">No announcements.</div>';
          el("qAnnResults").querySelectorAll("[data-q-ann]").forEach(function (r) {
            r.onclick = function () {
              api("/api/rooms/" + ROOM_CODE + "/queue", "POST", {
                action: "add", item_type: "announcement", ref_id: parseInt(r.dataset.qAnn), data: { title: r.dataset.qTitle }
              }).then(function () { closeModal(); switchTab("queue"); });
            };
          });
        });
      } else if (t === "image") {
        qForm.innerHTML = '<div class="cc-field"><label>Select image</label><div id="qImgResults"></div></div>';
        api("/api/images").then(function (res) {
          var html = "";
          (res.images || []).forEach(function (i) {
            html += '<div class="cc-ref-result" data-q-img="' + i.id + '" data-q-title="' + esc(i.title) + '">' + esc(i.title || "Image") + "</div>";
          });
          el("qImgResults").innerHTML = html || '<div class="cc-empty">No images.</div>';
          el("qImgResults").querySelectorAll("[data-q-img]").forEach(function (r) {
            r.onclick = function () {
              api("/api/rooms/" + ROOM_CODE + "/queue", "POST", {
                action: "add", item_type: "image", ref_id: parseInt(r.dataset.qImg), data: { title: r.dataset.qTitle }
              }).then(function () { closeModal(); switchTab("queue"); });
            };
          });
        });
      } else if (t === "countdown") {
        qForm.innerHTML = '<div class="cc-field"><label>Title (optional)</label><input class="cc-input" id="qCdTitle" placeholder="SERVICE STARTS IN"></div>' +
          '<div class="cc-field"><label>Seconds</label><input class="cc-input" id="qCdSecs" type="number" value="300"></div>';
      }
    }
    qType.onchange = renderForm;
    renderForm();
    el("qAdd").onclick = function () {
      var t = qType.value;
      if (t === "scripture") {
        var ref = el("qRef").value.trim();
        if (!ref) return;
        api("/api/rooms/" + ROOM_CODE + "/queue", "POST", { action: "add", item_type: "scripture", data: { reference: ref } })
          .then(function () { closeModal(); switchTab("queue"); });
      } else if (t === "countdown") {
        var title = el("qCdTitle").value.trim();
        var secs = parseInt(el("qCdSecs").value) || 300;
        api("/api/rooms/" + ROOM_CODE + "/queue", "POST", { action: "add", item_type: "countdown", data: { title: title, seconds: secs } })
          .then(function () { closeModal(); switchTab("queue"); });
      }
    };
  }

  function openSongEditor(songId) {
    var body = '<div class="cc-field"><label>Title</label><input class="cc-input" id="sTitle"></div>' +
      '<div class="cc-field"><label>Author</label><input class="cc-input" id="sAuthor"></div>' +
      '<div class="cc-field"><label>Import (plain text or ChordPro)</label><textarea class="cc-input" id="sImport" placeholder="Paste lyrics here… [Verse 1]&#10;Amazing grace, how sweet the sound&#10;&#10;[Chorus]&#10;…" style="min-height:120px"></textarea></div>' +
      '<button class="cc-btn cc-btn--block" id="sParse" style="margin-bottom:12px">Parse Import</button>' +
      '<div id="sSections"></div>' +
      '<button class="cc-btn cc-btn--accent cc-btn--block" id="sSave" style="margin-top:12px">Save Song</button>' +
      '<button class="cc-btn cc-btn--danger cc-btn--block" id="sDelete" style="margin-top:8px;display:none">Delete Song</button>';
    openModal(songId ? "Edit Song" : "New Song", body);

    var sections = [];

    function renderSections() {
      var html = "";
      sections.forEach(function (s, i) {
        html += '<div class="cc-section-edit">' +
          '<div class="cc-section-edit__row">' +
          '<button class="cc-section-edit__move" data-sec-up="' + i + '" title="Move up">▲</button>' +
          '<button class="cc-section-edit__move" data-sec-down="' + i + '" title="Move down">▼</button>' +
          '<input class="cc-input" id="secLabel' + i + '" value="' + esc(s.label) + '" placeholder="Label">' +
          '<button class="cc-item__btn cc-item__btn--danger" data-sec-del="' + i + '">✕</button></div>' +
          '<textarea class="cc-input" id="secLyrics' + i + '" placeholder="Lyrics" style="min-height:60px">' + esc(s.lyrics) + '</textarea>' +
          '</div>';
      });
      html += '<button class="cc-btn cc-btn--ghost" id="sAddSec" style="margin-top:8px">+ Add Section</button>';
      el("sSections").innerHTML = html;
      el("sAddSec").onclick = function () { sections.push({ label: "Verse " + (sections.length + 1), lyrics: "" }); renderSections(); };
      el("sSections").querySelectorAll("[data-sec-del]").forEach(function (b) {
        b.onclick = function () { sections.splice(parseInt(b.dataset.secDel), 1); renderSections(); };
      });
      el("sSections").querySelectorAll("[data-sec-up]").forEach(function (b) {
        b.onclick = function () { moveSection(parseInt(b.dataset.secUp), -1); };
      });
      el("sSections").querySelectorAll("[data-sec-down]").forEach(function (b) {
        b.onclick = function () { moveSection(parseInt(b.dataset.secDown), 1); };
      });
      function moveSection(i, dir) {
        var j = i + dir;
        if (j < 0 || j >= sections.length) return;
        var tmp = sections[i]; sections[i] = sections[j]; sections[j] = tmp;
        renderSections();
      }
    }

    if (songId) {
      api("/api/songs/" + songId).then(function (res) {
        var s = res.song;
        el("sTitle").value = s.title || "";
        el("sAuthor").value = s.author || "";
        sections = (s.sections || []).map(function (sec) { return { label: sec.label, lyrics: sec.lyrics }; });
        renderSections();
        el("sDelete").style.display = "block";
        el("sDelete").onclick = function () {
          if (confirm("Delete this song?")) {
            api("/api/songs/" + songId + "/delete", "DELETE").then(function () { closeModal(); switchTab("songs"); });
          }
        };
      });
    } else {
      sections = [{ label: "Verse 1", lyrics: "" }];
      renderSections();
    }

    el("sParse").onclick = function () {
      var text = el("sImport").value;
      if (!text.trim()) return;
      api("/api/songs/import", "POST", { text: text, format: "auto" }).then(function (res) {
        sections = res.sections || [];
        renderSections();
      });
    };

    el("sSave").onclick = function () {
      var collected = [];
      for (var i = 0; i < sections.length; i++) {
        var lEl = el("secLabel" + i);
        var tEl = el("secLyrics" + i);
        if (lEl && tEl) {
          collected.push({ label: lEl.value || "Verse", lyrics: tEl.value });
        }
      }
      if (collected.length === 0) collected = sections;
      api("/api/songs/save", "POST", {
        id: songId,
        title: el("sTitle").value || "Untitled",
        author: el("sAuthor").value,
        raw_text: el("sImport").value,
        sections: collected,
      }).then(function () { closeModal(); switchTab("songs"); });
    };
  }

  function openAnnEditor(annId) {
    var body = '<div class="cc-field"><label>Title</label><input class="cc-input" id="aTitle"></div>' +
      '<div class="cc-field"><label>Body</label><textarea class="cc-input" id="aBody" placeholder="Announcement text…" style="min-height:120px"></textarea></div>' +
      '<button class="cc-btn cc-btn--accent cc-btn--block" id="aSave">Save Announcement</button>' +
      '<button class="cc-btn cc-btn--danger cc-btn--block" id="aDelete" style="margin-top:8px;display:none">Delete</button>';
    openModal(annId ? "Edit Announcement" : "New Announcement", body);

    if (annId) {
      api("/api/announcements/" + annId).then(function (res) {
        var a = res.announcement;
        el("aTitle").value = a.title || "";
        el("aBody").value = a.body || "";
        el("aDelete").style.display = "block";
        el("aDelete").onclick = function () {
          if (confirm("Delete this announcement?")) {
            api("/api/announcements/" + annId + "/delete", "DELETE").then(function () { closeModal(); switchTab("announcements"); });
          }
        };
      });
    }

    el("aSave").onclick = function () {
      api("/api/announcements/save", "POST", {
        id: annId,
        title: el("aTitle").value || "Untitled",
        body: el("aBody").value,
      }).then(function () { closeModal(); switchTab("announcements"); });
    };
  }

  // --- Tab switching ---
  function switchTab(tab) {
    activeTab = tab;
    document.querySelectorAll(".cc-tab").forEach(function (t) {
      t.classList.toggle("active", t.dataset.tab === tab);
    });
    renderTab();
  }

  document.querySelectorAll(".cc-tab").forEach(function (t) {
    t.onclick = function () { switchTab(t.dataset.tab); };
  });

  // --- Nav button handlers ---
  prevBtn.onclick = function () { cmd("prev"); };
  nextBtn.onclick = function () { cmd("next"); };
  blankBtn.onclick = function () {
    var willBlank = !(state && state.blank);
    cmd("blank", { blank: willBlank });
  };

  // Queue navigation (service order)
  qPrevBtn.onclick = function () {
    var qidx = queueIndex();
    if (qidx <= 0) return;
    cmd("goto_queue", { index: qidx - 1 });
  };
  qNextBtn.onclick = function () {
    var qidx = queueIndex();
    if (qidx < 0 || qidx >= queue.length - 1) return;
    cmd("goto_queue", { index: qidx + 1 });
  };
  cdBtn.onclick = openCountdownModal;

  // Slide strip clicks (section/slide jump)
  slideStrip.addEventListener("click", function (e) {
    var chip = e.target.closest("[data-slide]");
    if (chip) { cmd("goto_slide", { slideIndex: parseInt(chip.dataset.slide) }); }
  });

  // --- Init ---
  connect();
  renderTab();
  loadTranslations();

  function esc(s) {
    if (s == null) return "";
    return String(s).replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;").replace(/"/g, "&quot;");
  }
})();