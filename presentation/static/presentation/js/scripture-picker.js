/* ============================================================
   ChurchCast — Scripture picker
   Pure client-side book resolver + cached 66-book index.

   resolveInput(text, index) is a pure function (no DOM, no fetch) so it
   runs in a browser AND in plain node for tests.

   The full index comes from GET /api/scripture/index:
     [{ name, testament: "OT"|"NT", aliases: [...], chapters: [verse counts] }]
   ============================================================ */
(function (root, factory) {
  if (typeof module === "object" && module.exports) {
    module.exports = factory();
  } else {
    root.BiblePicker = factory();
  }
})(typeof self !== "undefined" ? self : this, function () {
  "use strict";

  var RECENT_KEY = "churchcast.scripture.recent";
  var MODE_KEY = "churchcast.scripture.mode";
  var TESTAMENT_KEY = "churchcast.scripture.testament";
  var MAX_RECENT = 8;

  function norm(s) {
    return String(s || "").toLowerCase().replace(/\./g, "").replace(/\s+/g, "");
  }

  // Strip a trailing chapter/verse piece so "john3:16", "john 3", "ps23"
  // all reduce to the book probe "john" / "ps".
  function stripReferenceSuffix(s) {
    return s.replace(/:\d+(-\d+)?$/, "").replace(/\d+-\d+$/, "").replace(/\d+$/, "");
  }

  /* Rank a book against a normalized probe. Scores:
       5  exact alias           3  name prefix       1  word-initial
       4  exact name            2  alias prefix
     0 means no match. */
  function rank(name, aliasKeys, probe) {
    var keyName = norm(name);
    if (keyName === probe) return 4;
    if (keyName.lastIndexOf(probe, 0) === 0) return 3;
    if (aliasKeys.indexOf(probe) >= 0) return 5;
    var best = 0;
    for (var i = 0; i < aliasKeys.length; i++) {
      if (aliasKeys[i].lastIndexOf(probe, 0) === 0) best = 2;
    }
    if (!best) {
      var initials = name.toLowerCase().split(/\s+/).map(function (w) { return w.charAt(0); }).join("");
      if (initials.lastIndexOf(probe, 0) === 0) best = 1;
    }
    return best;
  }

  /* resolveInput(text, index) -> up to 6 matching book entries.
     Ties break alphabetically by canonical name (matches the expected
     "jo" -> Job, Joel, John, Jonah, Joshua order). */
  function resolveInput(text, index) {
    if (!text || !index) return [];
    var raw = String(text).trim().toLowerCase().replace(/\s+/g, "");
    if (!raw) return [];

    // Leading digits belong to numbered books ("1jn", "1 john", "3john").
    var m = /^(\d+)(.*)$/.exec(raw);
    var probe = stripReferenceSuffix(m ? m[1] + m[2] : raw);
    if (!probe) return [];

    var scored = [];
    for (var i = 0; i < index.length; i++) {
      var bk = index[i];
      var aliasKeys = (bk.aliases || []).map(norm);
      var sc = rank(bk.name, aliasKeys, probe);
      if (sc) scored.push({ score: sc, book: bk });
    }
    scored.sort(function (a, b) {
      if (b.score !== a.score) return b.score - a.score;
      return a.book.name < b.book.name ? -1 : a.book.name > b.book.name ? 1 : 0;
    });
    return scored.slice(0, 6).map(function (s) { return s.book; });
  }

  /* Resolve a typed reference string to {book, chapter, verse} or null.
     Works entirely off the index (no network). Used to detect a complete
     "John 3:16" from the input before tapping a chip. */
  function parse(text, index) {
    if (!text || !index) return null;
    var m = /^\s*(\d*\s?[A-Za-z][a-z.]*(\s+[A-Za-z]+)*?)\s*(\d+)?\s*[:.]?\s*(\d+)?\s*-?\s*(\d+)?\s*$/.exec(String(text));
    if (!m) return null;
    var bookText = m[1];
    var matches = resolveInput(bookText, index);
    if (!matches.length) return null;
    var book = matches[0];
    var chapter = m[3] ? parseInt(m[3], 10) : 0;
    var verse = m[4] ? parseInt(m[4], 10) : 0;
    if (chapter && chapter > book.chapters.length) return null;
    if (verse && (chapter === 0 || verse > book.chapters[chapter - 1])) return null;
    return { book: book.name, chapter: chapter, verse: verse };
  }

  function getRecent(defaults) {
    try {
      var raw = JSON.parse(localStorage.getItem(RECENT_KEY)) || [];
      return raw.slice(0, MAX_RECENT);
    } catch (e) { return defaults || []; }
  }

  function pushRecent(entry) {
    var rec = getRecent();
    rec = rec.filter(function (r) { return !(r.ref && entry.ref && r.ref === entry.ref); });
    rec.unshift(entry);
    try { localStorage.setItem(RECENT_KEY, JSON.stringify(rec.slice(0, MAX_RECENT))); } catch (e) {}
  }

  function getMode(def) {
    try { return localStorage.getItem(MODE_KEY) || def; } catch (e) { return def; }
  }
  function setMode(m) {
    try { localStorage.setItem(MODE_KEY, m); } catch (e) {}
  }
  function getTestament(def) {
    try { return localStorage.getItem(TESTAMENT_KEY) || def; } catch (e) { return def; }
  }
  function setTestament(t) {
    try { localStorage.setItem(TESTAMENT_KEY, t); } catch (e) {}
  }

  return {
    resolveInput: resolveInput,
    parse: parse,
    norm: norm,
    getRecent: getRecent,
    pushRecent: pushRecent,
    getMode: getMode,
    setMode: setMode,
    getTestament: getTestament,
    setTestament: setTestament
  };
});