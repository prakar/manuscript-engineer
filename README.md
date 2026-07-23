# Manuscript Engineer — Streamlit Reference Implementation

Scaffolds a regime-correct, structurally-organized manuscript shell
through a step-by-step flow mirroring a SAGE/Elsevier submission system.

**What this does not do:** generate manuscript content, evaluate claims,
or replace domain judgment. Stated on the landing page, on the in-app
Instructions tab, and in every exported document's colophon.

## Quickstart (Codespaces)

```bash
pip install -r requirements.txt --break-system-packages
python3 ingest.py          # loads config/*.yaml into db/manuscript_engineer.sqlite3
streamlit run app.py
```

## Testing the flow — one scaffold, start to finish

Use this to smoke-test a fresh install, or after any code change. Regime:
C2 (empirical/evidence paper).

**Step 1 — Manuscript Details**
- Type of paper: `Empirical/evidence paper`
- Working title: `Telemetry-Capture Mediated Feature Annexation: A Replication Test`
- Central claim: `A claim written by Prasanna Varun Karmarkar, evaluating whether feature annexation events are contradicted in the majority of tracked cases.`
  (Deliberately puts an identifying name in the *claim field*, not a
  section — this is the specific case that once slipped through
  `docx_export.py`'s redaction before it was fixed to cover the title/
  claim/regime-statement fields, not just section content. If Step 5/6
  ever shows this name un-redacted, that fix has regressed.)
- Regime statement: leave the default, or use `This is an empirical paper, so its job is to establish what the dataset shows, not to explain why.`

**Step 2 — Evidence Intake**
- Evidence source: `annexation_evidence.db`
- Claim query: `SELECT COUNT(*) FROM events WHERE verification_status='postFA-collapseContradicted'`

Leave the claim-query field blank first and confirm Next stays disabled
with a field-unlock error shown — this is the C2 unlock rule firing, and
it's the one thing most worth testing after any change to
`core/unlock_rules.py`. Then fill it in and confirm Next enables.

**Step 3 — Masked Review**
- Check the box, then paste `Prasanna Varun Karmarkar` on one line and
  `prakar` on the next — substitute your own identifying strings in real
  use.

**Step 4 — Sections**
- Edit → Abstract → paste `Testing the scaffold pipeline end to end.` →
  Save. Confirm the badge flips to "✅ Drafted." Leave every other
  section untouched — this is the useful test, since Step 5 should then
  correctly report partial completion.

**Step 5 — Review & Verify**
- Confirm it shows 1/N drafted and the "not every section has content"
  warning.
- Below that, confirm the **Automatic Verification** panel runs on its
  own (no button click needed) — `st.status()` should show "Running
  automatic verification…", then a live log: document build, the
  structural check's narrative and result, the blind-content check's
  narrative and result. It should end "Verification complete — no
  issues found."
- The structural check passing is expected and not very informative on
  its own — this app's own docx export always writes every section in
  the regime's own order, so within the normal flow this check has
  little to catch. Its real value is (a) regression protection if that
  ever changes, and (b) the same check reused against external files
  once Admin's "hired gun" mode exists.
- The blind check passing here **is** the meaningful assertion — it's
  independently confirming the claim-field name from Step 1 actually got
  redacted, not just trusting that it did.
- Click "Re-run verification" and confirm it re-runs cleanly (cached
  result path also gets exercised this way).

**Step 6 — Download**
- Confirm the page shows "This document passed automatic verification"
  and a working download button — no override checkbox should appear.
- Open the downloaded `.docx` and confirm:
  - `Prasanna Varun Karmarkar` reads as `[REDACTED]` in **both** the
    Abstract *and* the "Central claim:" line near the top — the second
    one is the actual regression test.
  - The colophon states this is a scaffold and names the blind-mode
    caveat.

**Testing the failure path (optional, not exercisable via the UI alone):**
Within this app's own flow, both checks are close to unfailable by
construction — the structural check because `docx_export.py` always
emits the regime's own section list correctly, the blind check because
the redaction fix now covers everything the check inspects. To see
Step 6's failure state (red banner, findings list, "I understand the
risk" override checkbox), you'd need to force a failure — e.g. temporarily
edit `core/docx_export.py` to skip redacting the claim field, or call
`core/structural_check.py` directly:

```bash
python3 -c "
from core import structural_check
ok, findings = structural_check.check_structure('some.docx', ['Abstract', 'Missing Section'])
print(ok, findings)
"
```

## Personalization — everything externalized

Nothing personal is hardcoded in `app.py`. Edit these, then re-run ingest:

- **`config/settings.yaml`** — name, ORCID, affiliation, footer
  attribution text, app title/tagline, admin password, DB path.
- **`config/regimes.yaml`** — the 7 manuscript regimes: sections, drafting-
  time hints (A9–A15), evidence-intake fields, field-unlock rule name. Add
  an 8th regime, rename a section, or change a hint here — no code change
  needed.
- **`config/llm_config.yaml`** — API keys, editable via the Admin panel's
  text-config box. See "API keys" below for the full resolution story.
  Not used by v0 (no AI-generated content); present for future
  drafting-assistance features.

## The ingest pattern

The app reads only from the SQLite database, never directly from YAML.
Edit a YAML file, then either run `python3 ingest.py` or click
**Admin → Run Ingest** in the app. Config changes take effect on ingest,
not on save.

## API keys — file or environment variable

`core/llm_keys.py` resolves each key with a fixed precedence:

1. **Environment variable** (`ANTHROPIC_API_KEY`, `OPENAI_API_KEY`,
   `GEMINI_API_KEY`) — checked first, always wins if set.
2. **`config/llm_config.yaml`** — checked second, used only if no
   environment variable is set for that key.
3. **Unset** — if neither is present.

```bash
export OPENAI_API_KEY="sk-..."
streamlit run app.py
```

Admin → LLM API keys shows, per key, which source it resolved from. An
exported environment variable is never overwritten by the file — editing
and saving `llm_config.yaml` in Admin has no effect on a key that's
already set in the environment. This is documentation only for now: no
code path in v0 actually calls out to any of these keys (see "What this
tool does and doesn't do" below).

## Admin panel

Sidebar → Admin, password from `config/settings.yaml` (`admin.password`,
default `changeme` — **change this before deploying anywhere non-local**;
it's a plaintext single shared gate, not real per-user auth).

- **Run Ingest** — reloads regimes/settings from YAML into the DB.
- **Current regimes in DB** — sanity view.
- **LLM API keys** — resolution-source display + text-config editor for
  `config/llm_config.yaml`.

## UI flow

| Step | Mirrors | Enforces |
|---|---|---|
| 0. Landing | Submission portal home | — |
| 1. Manuscript Details | "Enter manuscript info" | Regime selection, title, claim |
| 2. Evidence Intake | "Additional information" | Field-unlock rules (blocks Next until satisfied) |
| 3. Masked Review | "Blind review preferences" | Identifying-strings intake if required |
| 4. Sections | "Manuscript files" / "Attach files" | Per-section Edit, status badges |
| 5. Review & Verify | "Review and submit" | Completion check before download |
| 6. Download | "Confirmation" | .docx scaffold download |

An in-app **Instructions** tab covers intent, audience, the full regime
table, and the do/don't boundary — same content as this README's relevant
sections, formatted for someone using the tool rather than reading its
source.

## Known gaps

- Blind-mode redaction in `docx_export.py` is a string-replace on section
  content — not equivalent to `blind_check_template.js`'s check against
  real rendered XML. Stated in the exported doc's colophon. A later
  iteration should shell out to the actual Node blind-check tool instead
  of approximating it in Python.
- No multi-user auth beyond the single admin password.
- A9–A15 hints display as read-only notes during editing. Nothing
  enforces them the way field-unlock rules do — they are judgment calls,
  not lint-able conditions.
- SQLite connection uses `check_same_thread=False` to survive Streamlit's
  rerun model. Permissive, not concurrency-safe — fine for single-user
  use, not for concurrent users. Fix before any multi-user deployment:
  per-request connections, or WAL mode.
- LLM key resolution (`core/llm_keys.py`) is wired and tested but not
  called by any feature yet — there is currently nothing to gracefully
  degrade, because nothing invokes an LLM.