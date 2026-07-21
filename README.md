# Manuscript Engineer — Streamlit Reference Implementation

Scaffolds a regime-correct, structurally-organized manuscript shell
through a step-by-step flow mirroring a SAGE/Elsevier submission system.

**What this does not do:** generate manuscript content, evaluate claims,
or replace domain judgment. Stated on the landing page and in every
exported document's colophon.

## Quickstart (Codespaces)

```bash
pip install -r requirements.txt --break-system-packages
python3 ingest.py          # loads config/*.yaml into db/manuscript_engineer.sqlite3
streamlit run app.py
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
  text-config box. Not used by v0 (no AI-generated content); present for
  future drafting-assistance features.

## The ingest pattern

The app reads only from the SQLite database, never directly from YAML.
Edit a YAML file, then either run `python3 ingest.py` or click
**Admin → Run Ingest** in the app. Config changes take effect on ingest,
not on save.

## Admin panel

Sidebar → Admin, password from `config/settings.yaml` (`admin.password`,
default `changeme` — change before any non-local deployment).

- **Run Ingest** — reloads regimes/settings from YAML into the DB.
- **Current regimes in DB** — sanity view.
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
