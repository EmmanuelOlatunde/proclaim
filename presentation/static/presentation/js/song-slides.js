/* Split lyric text into slide-sized chunks.

   A section stays on ONE slide whenever it is maxLines lines or fewer.
   Longer sections are cut at line boundaries into balanced chunks (a
   10-line section becomes 5+5, never 6+4). Blank lines separate paragraphs
   and are split first. Line bytes are preserved unchanged.

   Mirrors split_song_slides() in presentation/consumer.py — keep the two
   implementations identical (see presentation/data/song_slides_fixtures.json).

   Usable from the browser (attaches window.splitSongSlides) and from Node
   (module.exports), so the same function is exercised by
   scripts/song_slides.test.js and the Django/Kivy test suite.
 */
(function (global) {
  "use strict";
  function splitSongSlides(text, maxLines) {
    maxLines = maxLines || 6;
    if (text == null) return [];
    text = String(text);
    var paras = [];
    var cur = [];
    text.split("\n").forEach(function (ln) {
      if (ln.replace(/[ \t\u00A0]/g, "") === "") {
        if (cur.length) { paras.push(cur); cur = []; }
      } else {
        cur.push(ln);
      }
    });
    if (cur.length) paras.push(cur);
    if (!paras.length) return [text];
    var slides = [];
    paras.forEach(function (para) {
      var n = para.length;
      if (n <= maxLines) { slides.push(para.join("\n")); return; }
      var chunks = Math.ceil(n / maxLines);
      var base = Math.floor(n / chunks);
      var rem = n % chunks;
      var idx = 0;
      for (var c = 0; c < chunks; c++) {
        var size = base + (c < rem ? 1 : 0);
        slides.push(para.slice(idx, idx + size).join("\n"));
        idx += size;
      }
    });
    return slides;
  }
  if (typeof module !== "undefined" && module.exports) {
    module.exports = { splitSongSlides: splitSongSlides };
  } else {
    global.splitSongSlides = splitSongSlides;
  }
})(typeof window !== "undefined" ? window : (typeof globalThis !== "undefined" ? globalThis : this));