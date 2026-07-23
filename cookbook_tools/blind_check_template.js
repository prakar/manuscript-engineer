// BLIND-MODE LEAK CHECK TEMPLATE.
// structural_lint.js (see structural_lint.js in this same folder) checks
// code STRUCTURE -- it has no concept of "identifying content" and will
// never tell you a masked-review document actually failed to mask anything.
// This is a different kind of check: not static source analysis, but
// verification of the REAL, freshly-generated artifact's actual text
// content. Never trust that a build did what you intended without reading
// the output it actually produced -- the same discipline applies to
// content leaks as to structural corruption.
//
// PREREQUISITE: your build script must already support a masked-review /
// "blinded" mode -- e.g. a BLIND environment variable toggling whether
// author name, affiliation, ORCID, and any self-identifying phrases are
// included. This tool does not create that toggle; it verifies it worked.
//
// Fill in IDENTIFYING_STRINGS and the two path constants below, then run
// this any time your blinded document is regenerated and before every
// delivery or upload of it.

const { execSync } = require("child_process");
const fs = require("fs");

// --- Fill in every string that must NEVER appear in the blinded output ---
// Deliberately explicit and manually maintained, not inferred, so it's
// always obvious what is and isn't being checked.
const IDENTIFYING_STRINGS = [
  "Full Author Name",
  "Author",              // last name alone, catches partial citations too
  "0000-0000-0000-0000", // ORCID
  "Institution Name",
  // Add any self-identifying phrase patterns specific to your manuscript,
  // e.g. "the author's prior work on X (manuscript under review)".
];

// --- Fill in your build command and expected output path ---
const BUILD_COMMAND = "BLIND=1 node build.js"; // however your script triggers blind mode
const BLINDED_OUTPUT_PATH = "./manuscript_BLINDED.docx"; // wherever your script actually writes it

console.log("Regenerating blinded output fresh (never trust a stale file)...");
execSync(BUILD_COMMAND, { stdio: "inherit" });

if (!fs.existsSync(BLINDED_OUTPUT_PATH)) {
  console.error(`ERROR: expected output ${BLINDED_OUTPUT_PATH} was not created. Check BUILD_COMMAND and BLINDED_OUTPUT_PATH above.`);
  process.exit(1);
}

// Read the actual docx XML content -- the real stored text, not a rendering.
let text;
try {
  const raw = execSync(`unzip -p "${BLINDED_OUTPUT_PATH}" word/document.xml`).toString("utf8");
  text = raw.replace(/<[^>]+>/g, " ");
} catch (e) {
  console.error("Could not read docx content. Is `unzip` available, and is BLINDED_OUTPUT_PATH a valid .docx?");
  process.exit(1);
}

let leaks = [];
for (const s of IDENTIFYING_STRINGS) {
  if (text.includes(s)) leaks.push(s);
}

console.log(`\nChecked ${IDENTIFYING_STRINGS.length} identifying strings against the actual generated blinded document.\n`);
if (leaks.length) {
  console.log("LEAK DETECTED -- do not upload this file:");
  leaks.forEach(l => console.log(`  - "${l}" found in blinded output`));
  process.exit(1);
} else {
  console.log("Clean. No identifying strings found in the blinded output.");
  process.exit(0);
}
