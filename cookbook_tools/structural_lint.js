// Structural lint for build.js — run after EVERY edit, before any PDF
// verification. Static analysis of the source file itself; catches
// structural errors a syntax checker alone won't (node -c only proves
// the file parses, not that it's internally consistent).
const fs = require("fs");
const path = require("path");

const SRC = path.join(__dirname, "build.js");
const src = fs.readFileSync(SRC, "utf8");
let errors = [];
let warnings = [];

// --- Check 1: every buildX function is registered in SECTIONS ---
const definedFns = [...src.matchAll(/^function (build\w+)\(/gm)].map(m => m[1]);
const sectionsMatch = src.match(/const SECTIONS = \[([\s\S]*?)\];/);
const registeredFns = sectionsMatch
  ? [...sectionsMatch[1].matchAll(/(\w+),?/g)].map(m => m[1]).filter(Boolean)
  : [];
for (const fn of definedFns) {
  if (fn === "buildAppendix" || fn === "buildResults") continue; // handled below explicitly, skip generic warn
}
for (const fn of definedFns) {
  if (!registeredFns.includes(fn)) {
    errors.push(`Function ${fn}() is defined but NOT in the SECTIONS array — it will never render.`);
  }
}
for (const fn of registeredFns) {
  if (!definedFns.includes(fn)) {
    errors.push(`SECTIONS array references ${fn}() but no such function is defined.`);
  }
}

// --- Check 2: heading number sequence, extracted from H1/H2/H3 calls ---
const headingCalls = [...src.matchAll(/H[123]\("([^"]+)"\)/g)].map(m => m[1]);
const numbered = headingCalls
  .map(h => h.match(/^(\d+(?:\.\d+)*)[.\s]/))
  .filter(Boolean)
  .map(m => m[1]);
// H1s should be 1,2,3... in order (Appendix excluded, doesn't use this pattern)
const h1s = numbered.filter(n => !n.includes("."));
for (let i = 0; i < h1s.length; i++) {
  if (parseInt(h1s[i], 10) !== i + 1) {
    errors.push(`H1 sequence broken: expected "${i + 1}" at position ${i}, found "${h1s[i]}". Full H1 sequence: [${h1s.join(", ")}]`);
    break;
  }
}
// Sub-numbers (2.1, 2.2, 2.3...) should increment by 1 within each parent
const byParent = {};
for (const n of numbered) {
  const parts = n.split(".");
  if (parts.length < 2) continue;
  const parent = parts[0];
  const sub = parseInt(parts[1], 10);
  byParent[parent] = byParent[parent] || [];
  byParent[parent].push(sub);
}
for (const [parent, subs] of Object.entries(byParent)) {
  // collapse consecutive duplicates: a 3-level heading (e.g. 2.4.1) parses
  // to the same sub-number as its 2-level parent (2.4) and immediately
  // follows it — that's expected nesting, not a sequence break.
  const collapsed = subs.filter((v, i) => i === 0 || v !== subs[i - 1]);
  for (let i = 0; i < collapsed.length; i++) {
    if (collapsed[i] !== i + 1) {
      warnings.push(`Section ${parent}.x numbering may be broken: got sequence [${collapsed.join(", ")}] under section ${parent}`);
      break;
    }
  }
}

// --- Check 3: every in-text "Section X.Y" or "Appendix A.N" reference resolves ---
const bodyRefs = [...src.matchAll(/Section (\d+(?:\.\d+)?)/g)].map(m => m[1]);
const allHeadingNumbers = new Set(numbered);
for (const ref of new Set(bodyRefs)) {
  if (!allHeadingNumbers.has(ref)) {
    errors.push(`In-text reference "Section ${ref}" does not match any actual heading. Existing headings: [${[...allHeadingNumbers].join(", ")}]`);
  }
}
const appendixRefs = [...src.matchAll(/Appendix A\.(\d+)/g)].map(m => m[1]);
const appendixHeadings = [...src.matchAll(/H2\("A\.(\d+)/g)].map(m => m[1]);
for (const ref of new Set(appendixRefs)) {
  if (!appendixHeadings.includes(ref)) {
    errors.push(`In-text reference "Appendix A.${ref}" does not match any actual Appendix subsection. Existing: [${appendixHeadings.join(", ")}]`);
  }
}

// --- Check 4: bracket/paren balance sanity (belt-and-suspenders on top of node -c) ---
const opens = (src.match(/\(/g) || []).length;
const closes = (src.match(/\)/g) || []).length;
if (opens !== closes) {
  errors.push(`Paren imbalance: ${opens} "(" vs ${closes} ")" — likely an orphaned token from an incomplete edit.`);
}

// --- Check 5: orphaned function body -- catches a specific, recurring
// bug: a str_replace insertion using "function buildX() {" as an anchor,
// then failing to re-include that line, leaving "const c = [];" (or
// similar body-start code) sitting directly after a function's closing
// brace with no new "function ...() {" in between.
const lines = src.split("\n");
for (let i = 0; i < lines.length - 1; i++) {
  if (/^\}\s*$/.test(lines[i])) {
    // scan forward past blank lines and comments to the next real line
    let j = i + 1;
    while (j < lines.length && (/^\s*$/.test(lines[j]) || /^\s*\/\//.test(lines[j]))) j++;
    if (j < lines.length && /^\s*const c = \[\];/.test(lines[j])) {
      errors.push(`Orphaned function body at line ${j + 1}: "const c = [];" appears directly after a closing brace (line ${i + 1}) with no "function ...() {" declaration between them -- almost certainly a dropped anchor line from an insertion edit.`);
    }
  }
}

// --- Check 6: Figure/Table cross-references resolve to a real caption ---
const figTableRefs = [...src.matchAll(/(Figure|Table) (\d+)/g)].map(m => `${m[1]} ${m[2]}`);
const figTableCaptions = [...src.matchAll(/"(Figure|Table) (\d+)\./g)].map(m => `${m[1]} ${m[2]}`);
for (const ref of new Set(figTableRefs)) {
  if (!figTableCaptions.includes(ref)) {
    errors.push(`In-text reference "${ref}" has no matching caption (no ""${ref}."" found as a caption string).`);
  }
}

// --- Report ---
console.log(`Checked ${definedFns.length} functions, ${headingCalls.length} headings, ${bodyRefs.length + appendixRefs.length} cross-references.\n`);
if (warnings.length) {
  console.log("WARNINGS (review, not necessarily broken):");
  warnings.forEach(w => console.log("  - " + w));
  console.log("");
}
if (errors.length) {
  console.log("ERRORS (must fix before proceeding):");
  errors.forEach(e => console.log("  - " + e));
  process.exit(1);
} else {
  console.log("No structural errors found.");
  process.exit(0);
}
