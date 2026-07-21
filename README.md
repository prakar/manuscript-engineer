# Manuscript Engineer — Streamlit Reference Implementation

Same tool as `manuscript-engineer` (the CLI/Node version), rebuilt as a
Streamlit app with a SQLite backend, following the config → ingest →
database pattern used in the Feature Annexation Observatory and QHE apps.

**What this does:** scaffolds a regime-correct, structurally-organized
manuscript shell through a step-by-step flow mirroring a typical SAGE/
Elsevier submission system. **What this does not do:** generate your
paper's content, evaluate your claims, or replace domain judgment — see
the landing page and every section's colophon note for this boundary
stated explicitly.

## Quickstart (Codespaces)

```bash
pip install -r requirements.txt --break-system-packages
python3 ingest.py          # loads config/*.yaml into db/manuscript_engineer.sqlite3
streamlit run app.py
```

This environment had no network access to install `streamlit` itself, so
the Streamlit UI (`app.py`) is syntax-checked but not runtime-tested here.
Every module it depends on (`core/db.py`, `core/unlock_rules.py`,
`core/docx_export.py`, `ingest.py`) has no Streamlit import and **was**
exercised end-to-end locally, including the blind-mode redaction path —
run `streamlit run app.py` in Codespaces first thing and report back
anything that doesn't render as expected.

## Personalization — everything externalized

Nothing personal is hardcoded in `app.py`. Edit these, then re-run ingest:

- **`config/settings.yaml`** — your name, ORCID, affiliation, footer
  attribution text, app title/tagline, admin password, DB path.
- **`config/regimes.yaml`** — the 7 manuscript regimes: sections, drafting-
  time hints (A9–A15), evidence-intake fields, field-unlock rule name. Add
  an 8th regime, rename a section, or change a hint here — no code change
  needed.
- **`config/llm_config.yaml`** — API keys, editable via the Admin panel's
  text-config box. Not used by v0 (no AI-generated content); present for
  future drafting-assistance features.

## The ingest pattern

Matches FAO/QHE: the app never reads YAML directly at runtime — it reads
the SQLite database, which is only populated by `ingest.py`. Edit a YAML
file, then either run `python3 ingest.py` from the command line or click
**Admin → Run Ingest** in the app. This makes config changes a deliberate,
visible, re-runnable step rather than something the app silently picks up.

## Admin panel

Sidebar → Admin, password from `config/settings.yaml` (`admin.password`,
default `changeme` — **change this before deploying anywhere non-local**).

- **Run Ingest** — reloads regimes/settings from YAML into the DB.
- **Current regimes in DB** — quick sanity view.
- **LLM API keys** — text-config editor for `config/llm_config.yaml`.

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

## Known gaps / next iteration

- Blind-mode redaction in `docx_export.py` is a scaffold convenience
  (string replace on section content) — **not** equivalent to
  `blind_check_template.js`'s check against real rendered XML. Stated in
  the exported doc's colophon; worth deciding whether the web app should
  eventually shell out to the actual Node blind-check tool instead of
  approximating it in Python.
- No multi-user auth beyond the single admin password (matches the FAO/QHE
  reference-implementation pattern — not a real auth system).
- A9–A15 hints are shown as read-only notes during editing; nothing
  currently enforces them the way field-unlock rules do (matches the CLI
  version's own design choice — these are judgment calls, not lint-able).
