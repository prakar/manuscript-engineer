// SAFE RENUMBERING TEMPLATE.
// Use this pattern any time section numbers need to shift (inserting a new
// section, deleting one, reordering). NEVER do this with sequential
// find-replace -- see COOKBOOK.md Section B3 / TECH_NOTE Section 3 for why
// that silently double-shifts values.
//
// Before running: grep every occurrence of your target numbers in the real
// file and manually confirm each one is a genuine section/heading reference,
// not an unrelated coincidence (a citation year, a stray decimal, a page
// number). This script does the substitution; it does not know your content.

const fs = require("fs");
const path = require("path");
const FILE = path.join(__dirname, "build.js"); // <-- point this at your real file

// Define the mapping explicitly. Example: inserting a new Section 2 means
// everything from 2 onward shifts up by one.
const MAP = {
  "7": "8", "6": "7", "5": "6", "4": "5", "3": "4", "2": "3",
  // For sub-numbers too if needed, e.g.:
  // "3.4.1": "3.5.1", "3.4": "3.5", "3.5": "3.6", "3.6": "3.7",
};

function bump(numStr) {
  // For simple top-level shifts (single digit), direct lookup:
  if (MAP[numStr]) return MAP[numStr];
  // For "N.M" style, shift only the leading component if it's in range:
  const parts = numStr.split(".");
  if (MAP[parts[0]]) {
    parts[0] = MAP[parts[0]];
    return parts.join(".");
  }
  return numStr;
}

let src = fs.readFileSync(FILE, "utf8");
let changes = [];

// Heading declarations: H1("N. Title"), H1("N.M Title"), H2/H3 same.
src = src.replace(/(H[123]\(")(\d+(?:\.\d+){0,2})([.\s])/g, (full, prefix, num, sep) => {
  const newNum = bump(num);
  if (newNum !== num) changes.push(`heading: "${num}" -> "${newNum}"`);
  return prefix + newNum + sep;
});

// In-text cross-references: "Section N" or "Section N.M"
src = src.replace(/(Section )(\d+(?:\.\d+)?)/g, (full, prefix, num) => {
  const newNum = bump(num);
  if (newNum !== num) changes.push(`body ref: "Section ${num}" -> "Section ${newNum}"`);
  return prefix + newNum;
});

console.log(`${changes.length} substitutions:`);
changes.forEach(c => console.log("  " + c));
console.log("\nReview the list above BEFORE trusting it. If it looks right:");
console.log("uncomment the write line below and re-run.");

// fs.writeFileSync(FILE, src, "utf8");
// console.log("Written. Now run structural_lint.js immediately.");
