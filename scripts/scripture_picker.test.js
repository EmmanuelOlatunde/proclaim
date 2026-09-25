/* Node test for scripture-picker.js resolveInput using the real index
   fixture generated from the server-side build_index().

   Run: node scripts/scripture_picker.test.js
*/
"use strict";
const assert = require("assert");
const picker = require("../presentation/static/presentation/js/scripture-picker.js");
const index = require("./index.fixture.json");

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

// 66 books in Bible order, OT/NT split + total verse count 31,102.
check("index has 66 books", index.length, 66);
const sums = { OT: 0, NT: 0 };
index.forEach((b) => { sums[b.testament] += b.chapters.reduce((a, c) => a + c, 0); });
check("total verses = 31102", sums.OT + sums.NT, 31102);
check("index books sorted by canonical order", index[0].name, "Genesis");
check("NT starts at Matthew", index.find((b) => b.name === "Matthew").testament, "NT");

// Completions.
function names(text) { return picker.resolveInput(text, index).map((b) => b.name); }

check('"jo" -> Job, Joel, John, Jonah, Joshua (alphabetical ties)',
  names("jo"), ["Job", "Joel", "John", "Jonah", "Joshua"]);

check('"ps" -> Psalms only', names("ps"), ["Psalms"]);
check('"1 jn" -> 1 John only', names("1 jn"), ["1 John"]);
check('"1 john 3:16" -> 1 John (chapter stripped)', names("1 john 3:16"), ["1 John"]);
check('"song" -> Song of Solomon (alias beats name prefix)', names("song"), ["Song of Solomon"]);
check('"gen 1" -> Genesis (chapter stripped)', names("gen 1"), ["Genesis"]);
check('"joh" -> John first then Jonas? -> John', names("joh"), ["John"]);
check('"zzz" -> none', names("zzz"), []);
check('"" -> none', names(""), []);
check('"2 jn" -> 2 John', names("2 jn"), ["2 John"]);

const psi = index.find((b) => b.name === "Psalms");
check("Psalms chapter 119 has 176 verses", psi.chapters[118], 176);
const joh = index.find((b) => b.name === "John");
check("John chapter 3 has 36 verses", joh.chapters[2], 36);
check("Genesis has 50 chapters", index.find((b) => b.name === "Genesis").chapters.length, 50);

// parse() resolution.
const parsed = picker.parse("john 3:16", index);
check('parse("john 3:16")', parsed, { book: "John", chapter: 3, verse: 16 });
check('parse("ps 119:1")', picker.parse("ps 119:1", index), { book: "Psalms", chapter: 119, verse: 1 });
check('parse("john 3")', picker.parse("john 3", index), { book: "John", chapter: 3, verse: 0 });
check('parse garbage', picker.parse("xyz", index), null);

process.exit(failures ? 1 : 0);