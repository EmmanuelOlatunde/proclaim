/* Node test for splitSongSlides (the browser function in
   presentation/static/presentation/js/song-slides.js) using the SAME fixture
   file the server-side test consumes, so the browser and the server
   (presentation/consumer.py split_song_slides) cannot drift apart.

   Run: node scripts/song_slides.test.js
*/
"use strict";
const assert = require("assert");
const path = require("path");
const fs = require("fs");
const { splitSongSlides } = require("../presentation/static/presentation/js/song-slides.js");
const fixture = JSON.parse(
  fs.readFileSync(path.join(__dirname, "../presentation/data/song_slides_fixtures.json"), "utf8")
);

let failures = 0;
function check(label, actual, expected) {
  try {
    assert.deepStrictEqual(actual, expected);
    console.log("ok  - " + label);
  } catch (e) {
    failures++;
    console.error("FAIL - " + label);
    console.error("  expected: " + JSON.stringify(expected));
    console.error("  actual:   " + JSON.stringify(actual));
  }
}

fixture.cases.forEach((c) => {
  check("song_slides: " + c.name, splitSongSlides(c.lyrics), c.expected);
});

if (failures) {
  console.error(failures + " failure(s)");
  process.exit(1);
}
console.log("all song_slides fixture cases passed");