/* ============================================================
   ChurchCast — Control interface
   Mobile-first broadcast controller. WebSocket commands.
   ============================================================ */
(function () {
  "use strict";

  var ROOM_CODE = window.ROOM_CODE;
  var ws = null;
  var state = null;
  var activeTab = "queue";
  var queue = [];
  var countdownPreview = null;

  // --- DOM refs ---
  var el = function (id) { return document.getElementById(id); };
  var panel = el("panel");
  var statusDot = el("statusDot");
  var statusText = el("statusText");
  var liveTitle = el("liveTitle");
  var previewCanvas = el("previewCanvas");
  var previewLabel = el("previewLabel");
  var previewRef = el("previewRef");
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
          renderPreview();
          renderSlideStrip();
          renderNavState();
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
  function renderPreview() {
    if (!state) return;
    previewCanvas.classList.remove("blank");

    if (state.blank) {
      previewCanvas.classList.add("blank");
      previewLabel.textContent = "BLANKED";
      previewRef.textContent = "";
      previewText.textContent = "";
      previewImg.style.display = "none";
      countdownLabel.style.display = "none";
      countdownDisplay.style.display = "none";
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

    // slide number
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

  var previewCountdownTimer = null;
  function startPreviewCountdown(endTime) {
    if (previewCountdownTimer) clearTimeout(previewCountdownTimer);
    function tick() {
      var remaining = Math.max(0, endTime - Date.now());
      var secs = Math.floor(remaining / 1000);
      var m = Math.floor(secs / 60);
      var s = secs % 60;
      countdownDisplay.textContent = m + ":" + (s < 10 ? "0" : "") + s;
      if (remaining > 0) previewCountdownTimer = setTimeout(tick, 250);
    }
    tick();
  }

  // --- Slide strip ---
  function renderSlideStrip() {
    if (!state || !state.content) { slideStrip.innerHTML = ""; return; }
    var ct = state.contentType;
    var c = state.content;
    var slides = c.slides;
    if (!slides || slides.length <= 1) { slideStrip.innerHTML = ""; return; }
    var idx = state.slideIndex || 0;
    var html = "";
    for (var i = 0; i < slides.length; i++) {
      var label = slides[i].label || slides[i].reference || ("Slide " + (i + 1));
      var active = i === idx ? " active" : "";
      html += '<button class="cc-slide-chip' + active + '" data-slide="' + i + '">' +
        '<span class="cc-slide-chip__num">' + (i + 1) + '</span>' +
        esc(label) +
      "</button>";
    }
    slideStrip.innerHTML = html;
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

  // --- Tab rendering ---
  function renderTab() {
    switch (activeTab) {
      case "queue": renderQueue(); break;
      case "library": renderLibrary(); break;
      case "songs": renderSongs(); break;
      case "scripture": renderScripture(); break;
      case "images": renderImages(); break;
      case "announcements": renderAnnouncements(); break;
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
        for (var i = 0; i < queue.length; i++) {
          var q = queue[i];
          var label = queueItemLabel(q);
          var active = (state && state.queueIndex === i) ? " active" : "";
          html += '<div class="cc-item cc-queue-item' + active + '">' +
            '<span class="cc-queue-item__num">' + (i + 1) + '</span>' +
            '<div class="cc-item__body"><div class="cc-item__title">' + esc(label) + '</div>' +
            '<div class="cc-item__sub">' + esc(q.item_type) + '</div></div>' +
            '<div class="cc-item__actions">' +
              '<button class="cc-item__btn" data-queue-up="' + q.id + '">▲</button>' +
              '<button class="cc-item__btn" data-queue-down="' + q.id + '">▼</button>' +
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
    var html = '<div class="cc-panel__search"><input class="cc-input" id="songSearch" placeholder="Search songs…"></div>';
    html += '<button class="cc-btn cc-btn--block" id="newSongBtn" style="margin-bottom:12px">+ New Song</button>';
    html += '<div class="cc-panel__list" id="songList"></div>';
    panel.innerHTML = html;
    var search = el("songSearch");
    function doSearch() {
      api("/api/songs?q=" + encodeURIComponent(search.value)).then(function (res) {
        var songs = res.songs || [];
        var html = "";
        songs.forEach(function (s) {
          html += '<div class="cc-item">' +
            '<div class="cc-item__icon">♪</div>' +
            '<div class="cc-item__body"><div class="cc-item__title">' + esc(s.title) + '</div>' +
            '<div class="cc-item__sub">' + esc(s.author || "") + '</div></div>' +
            '<div class="cc-item__actions">' +
              '<button class="cc-item__btn" data-edit-song="' + s.id + '">Edit</button>' +
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
    doSearch;
    el("newSongBtn").onclick = function () { openSongEditor(null); };
    doSearch();
  }

  function renderScripture() {
    var html = '<div class="cc-ref-input"><input class="cc-input" id="refInput" placeholder="e.g. John 3:16 or Psalm 23"></div>';
    html += '<button class="cc-btn cc-btn--block" id="refGo" style="margin-bottom:12px">Present Scripture</button>';
    html += '<div id="refPreview"></div>';
    html += '<div style="margin-top:16px"><button class="cc-btn cc-btn--ghost" id="addScriptureQ">+ Add to Queue</button></div>';
    panel.innerHTML = html;
    var input = el("refInput");
    var preview = el("refPreview");
    function doPreview() {
      var ref = input.value.trim();
      if (!ref) { preview.innerHTML = ""; return; }
      api("/api/scripture/parse?ref=" + encodeURIComponent(ref)).then(function (res) {
        if (res.error) {
          preview.innerHTML = '<div class="cc-empty">' + esc(res.error) + "</div>";
          return;
        }
        var html = '<div class="cc-ref-results"><div class="cc-ref-result"><strong>' + esc(res.reference) + "</strong> — " +
          (res.slides ? res.slides.length : 0) + " slide(s)</div></div>";
        preview.innerHTML = html;
      });
    }
    input.oninput = doPreview;
    input.onkeydown = function (e) { if (e.key === "Enter") { cmd("present_scripture", { reference: input.value.trim() }); } };
    el("refGo").onclick = function () { cmd("present_scripture", { reference: input.value.trim() }); };
    el("addScriptureQ").onclick = function () {
      var ref = input.value.trim();
      if (!ref) return;
      api("/api/rooms/" + ROOM_CODE + "/queue", "POST", {
        action: "add", item_type: "scripture", data: { reference: ref }
      }).then(function () { switchTab("queue"); });
    };
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
              '<button class="cc-item__btn cc-item__btn--present" data-present-image="' + i.id + '">Present</button>' +
              '<button class="cc-item__btn cc-item__btn--danger" data-addq-image="' + i.id + '">+Q</button>' +
            '</div></div>';
        });
        if (!html) html = '<div class="cc-empty">No images uploaded yet.</div>';
        el("imgList").innerHTML = html;
        bindPresentButtons();
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
          '<div class="cc-section-edit__row"><input class="cc-input" id="secLabel' + i + '" value="' + esc(s.label) + '" placeholder="Label">' +
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
        if (!el("sTitle").value && sections.length > 0) {
          // try to extract title from first line
        }
        renderSections();
      });
    };

    el("sSave").onclick = function () {
      // collect sections from DOM
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

  // Slide strip clicks
  slideStrip.addEventListener("click", function (e) {
    var chip = e.target.closest("[data-slide]");
    if (chip) { cmd("goto_slide", { slideIndex: parseInt(chip.dataset.slide) }); }
  });

  // --- Init ---
  connect();
  renderTab();

  function esc(s) {
    if (s == null) return "";
    return String(s).replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;").replace(/"/g, "&quot;");
  }
})();
