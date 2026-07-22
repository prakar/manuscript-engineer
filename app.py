"""
app.py — Manuscript Engineer (Streamlit reference implementation)
====================================================================

WHAT THIS FILE IS
------------------
This is the entire user-facing surface of the tool. It is deliberately a
single file, not because a bigger app shouldn't be split up, but because
this is a reference implementation meant to be read start-to-finish by
someone deciding whether to adapt it — splitting it across a dozen small
files would optimize for a kind of "production cleanliness" this project
doesn't need yet, at the cost of the one thing it does need: a stranger
being able to open one file and see the whole shape of the thing.

WHAT THIS FILE IS NOT
----------------------
It does not generate manuscript content. It does not evaluate whether a
claim is true. Every place that might look like "the tool is writing your
paper for you" is actually just scaffolding: a labeled blank, a TODO, a
structural check. That boundary is stated on the landing page and again
in the exported document's colophon, on purpose, more than once — see the
Manuscript Engineering Cookbook's own C4 rule (existence claims should
not smuggle in adjacent claims they haven't earned) applied reflexively
to this tool's own marketing copy.

HOW TO READ THIS FILE
-----------------------
Top to bottom, it follows the same order a user moves through the app:
setup and shared helpers, then Landing, then Steps 1 through 6 in order,
then Admin, then the main() dispatcher at the bottom that ties navigation
together. If you're debugging a specific screen, jump to the function
named page_stepN or page_<name> — each one is self-contained.

LOGGING
-------
Every state-changing operation (DB writes, file writes, ingest runs) logs
at INFO. Anything that fails and is caught logs at ERROR with the full
exception, then surfaces a plain-language message to the user via
st.error — the log is for you (or for whoever's debugging a deployment
later), the st.error is for the person using the app right now, and the
two audiences want different levels of detail.
"""

import datetime
import io
import logging
import os
import sys

import streamlit as st
import yaml

from core import db, unlock_rules, docx_export, llm_keys

# --------------------------------------------------------------------
# Logging setup
# --------------------------------------------------------------------
# Two handlers: one to stderr (visible in the Codespaces/terminal running
# `streamlit run app.py`), one to a rotating-free plain log file so a
# session's history survives after the terminal scrolls away. Streamlit
# re-executes this whole module on every interaction, so we guard against
# adding duplicate handlers on every rerun — without the guard, each
# button click would double the number of log lines written per event.
logger = logging.getLogger("manuscript_engineer")
if not logger.handlers:
    logger.setLevel(logging.INFO)

    formatter = logging.Formatter(
        "%(asctime)s [%(levelname)s] %(name)s: %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )

    stream_handler = logging.StreamHandler(sys.stderr)
    stream_handler.setFormatter(formatter)
    logger.addHandler(stream_handler)

    LOG_DIR = os.path.join(os.path.dirname(__file__), "logs")
    try:
        os.makedirs(LOG_DIR, exist_ok=True)
        file_handler = logging.FileHandler(os.path.join(LOG_DIR, "app.log"))
        file_handler.setFormatter(formatter)
        logger.addHandler(file_handler)
    except OSError as e:
        # If the filesystem is read-only (some hosting platforms mount it
        # that way) we still want the app to run — just without a log
        # file. Fall back to stream-only logging rather than crashing the
        # whole app over something this non-essential.
        logger.warning(f"Could not open log file ({e}); logging to stderr only.")

logger.info("app.py module loaded / Streamlit rerun triggered.")

# --------------------------------------------------------------------
# Paths — resolved once, relative to this file, so the app works the same
# whether it's launched from this directory or from somewhere else via a
# process manager.
# --------------------------------------------------------------------
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
CONFIG_DIR = os.path.join(BASE_DIR, "config")
SETTINGS_PATH = os.path.join(CONFIG_DIR, "settings.yaml")
LLM_CONFIG_PATH = os.path.join(CONFIG_DIR, "llm_config.yaml")


# --------------------------------------------------------------------
# Settings loader
# --------------------------------------------------------------------
def load_settings():
    """
    Reads config/settings.yaml fresh every call (deliberately not cached —
    this file is small, read often, and the cost of re-reading it is
    trivial compared to the cost of an admin editing it and the app not
    noticing). Raises a clear Streamlit error and stops execution if the
    file is missing or malformed, rather than letting a cryptic KeyError
    surface from somewhere deep in page_landing().
    """
    try:
        with open(SETTINGS_PATH, "r") as f:
            settings = yaml.safe_load(f)
    except FileNotFoundError:
        logger.error(f"settings.yaml not found at {SETTINGS_PATH}")
        st.error(
            f"Configuration file missing: {SETTINGS_PATH}\n\n"
            "This file is required — it holds your name, ORCID, admin "
            "password, and app title. Restore it from config/settings.yaml "
            "in the repository before continuing."
        )
        st.stop()
    except yaml.YAMLError as e:
        logger.error(f"settings.yaml is not valid YAML: {e}")
        st.error(f"config/settings.yaml has a YAML syntax error:\n\n{e}")
        st.stop()

    if not settings or "app" not in settings or "author" not in settings:
        logger.error("settings.yaml loaded but is missing required top-level keys.")
        st.error(
            "config/settings.yaml is missing required sections (expected "
            "at least 'app' and 'author'). Check it against the version "
            "in the repository."
        )
        st.stop()

    return settings


# --------------------------------------------------------------------
# Database connection
# --------------------------------------------------------------------
@st.cache_resource
def get_conn():
    """
    One SQLite connection, cached and reused across reruns within a
    session. check_same_thread=False (set in core/db.py) is what makes
    this safe against Streamlit's rerun model, which does not guarantee
    the same OS thread handles every rerun — see core/db.py's own comment
    for the full explanation and its limits (permissive, not a real
    concurrency guarantee; fine for one user, not for many at once).

    If the database file can't be opened or the schema can't be applied,
    this is unrecoverable for the whole app — every page needs a working
    connection — so we log the full exception and stop rendering rather
    than letting each page fail separately with a less informative error.
    """
    try:
        settings = load_settings()
        db_path = os.path.join(BASE_DIR, settings["database"]["path"])
        conn = db.connect(db_path)
        db.init_schema(conn)
        logger.info(f"Database connected and schema ensured at {db_path}")
        return conn
    except Exception as e:
        logger.exception("Failed to initialize database connection.")
        st.error(
            "Could not connect to the database. This usually means the "
            "db/ directory isn't writable, or the schema file is missing. "
            f"Details: {e}"
        )
        st.stop()


# --------------------------------------------------------------------
# Footer — rendered on every page
# --------------------------------------------------------------------
def footer():
    """
    Attribution footer, built from settings.yaml so the text itself is
    never hardcoded here. If the format string in settings.yaml doesn't
    match the fields available in the 'author' section (e.g. someone
    added a {orcid} placeholder but removed the orcid key), we catch that
    specifically rather than letting a raw KeyError traceback appear at
    the bottom of every single page.
    """
    try:
        settings = load_settings()
        text = settings["attribution"]["footer_text"].format(**settings["author"])
        st.markdown("---")
        st.caption(text)
        st.caption(settings["attribution"]["repo_url"])
    except KeyError as e:
        logger.error(f"Footer template references a missing settings field: {e}")
        st.markdown("---")
        st.caption(
            "[Footer configuration error — check config/settings.yaml's "
            f"attribution.footer_text template. Missing field: {e}]"
        )


# ======================================================================
# STEP 0 — Landing
# ======================================================================
def page_landing(settings):
    """
    The front door. Two jobs: state the tool's boundary in plain language
    before anyone starts filling in a form (so the claim about what this
    tool does and doesn't do isn't buried in a README nobody reads), and
    check that Admin -> Run Ingest has actually been run at least once —
    a fresh clone with an empty database is a confusing first experience
    if the app just silently shows an empty dropdown on Step 1 instead of
    explaining why.
    """
    st.title(settings["app"]["title"])
    st.subheader(settings["app"]["tagline"])
    st.write(
        "This tool scaffolds a regime-correct, structurally-checked "
        "manuscript shell. It does not generate your paper's content, "
        "evaluate your claims, or replace domain judgment — it scaffolds "
        "and verifies; you still have to be right."
    )

    conn = get_conn()
    try:
        regime_count = len(db.list_regimes(conn))
    except Exception as e:
        logger.exception("Failed to count regimes on landing page.")
        st.error(f"Could not read regimes from the database: {e}")
        regime_count = 0

    if regime_count == 0:
        st.warning(
            "No regimes loaded in the database yet. Go to Admin (sidebar) "
            "and click 'Run Ingest' to load config/regimes.yaml."
        )
    else:
        st.success(f"{regime_count} manuscript regimes loaded and ready.")

    if st.button("Start a new manuscript →", type="primary", disabled=(regime_count == 0)):
        logger.info("User started a new manuscript from the landing page.")
        st.session_state.step = 1
        st.rerun()


# ======================================================================
# STEP 1 — Manuscript Details & Regime Selection
# ======================================================================
def page_step1(conn):
    st.header("Step 1 — Manuscript Details")

    try:
        regimes = db.list_regimes(conn)
    except Exception as e:
        logger.exception("Failed to load regimes for Step 1.")
        st.error(f"Could not load regimes: {e}")
        return

    if not regimes:
        st.error("No regimes loaded. Go to Admin → Run Ingest first.")
        return

    labels = [r["label"] for r in regimes]
    idx = st.selectbox(
        "Type of paper",
        options=range(len(regimes)),
        format_func=lambda i: labels[i],
        index=st.session_state.get("regime_idx", 0),
    )
    regime = regimes[idx]
    st.caption(regime["description"])

    title = st.text_input("Working title", value=st.session_state.get("title", ""))
    claim = st.text_area(
        "Central claim, in one sentence",
        value=st.session_state.get("claim", ""),
        height=80,
    )
    default_stmt = regime["regime_statement_template"]
    regime_statement = st.text_area(
        "Regime statement — every later section gets checked against this",
        value=st.session_state.get("regime_statement", default_stmt),
        height=80,
    )

    col1, col2 = st.columns([1, 1])
    with col2:
        if st.button("Next: Evidence Intake →", type="primary"):
            # Basic sanity check before letting the user proceed — an
            # empty title or claim isn't a hard block (unlike the
            # regime-specific field-unlock rules in Step 2), but warning
            # about it here is cheaper for the user than discovering a
            # blank title on the final downloaded document.
            if not title.strip():
                st.warning("Working title is empty — you can still continue, but consider filling it in.")
            if not claim.strip():
                st.warning("Central claim is empty — you can still continue, but consider filling it in.")

            st.session_state.regime_idx = idx
            st.session_state.regime_code = regime["code"]
            st.session_state.title = title
            st.session_state.claim = claim
            st.session_state.regime_statement = regime_statement
            logger.info(f"Step 1 complete: regime={regime['code']}, title='{title}'")
            st.session_state.step = 2
            st.rerun()


# ======================================================================
# STEP 2 — Regime-Specific Evidence Intake
# ======================================================================
def page_step2(conn):
    st.header("Step 2 — Regime-Specific Evidence Intake")

    try:
        regime = db.get_regime(conn, st.session_state.regime_code)
    except Exception as e:
        logger.exception("Failed to load regime details for Step 2.")
        st.error(f"Could not load regime details: {e}")
        return

    if regime is None:
        # Defensive: this would mean session_state has a regime_code that
        # no longer exists in the DB — e.g. someone re-ran ingest with an
        # edited regimes.yaml that dropped a regime mid-session.
        st.error(
            "The selected regime no longer exists in the database (was it "
            "removed from regimes.yaml and re-ingested mid-session?). "
            "Please go back to Step 1 and choose again."
        )
        if st.button("← Back to Step 1"):
            st.session_state.step = 1
            st.rerun()
        return

    st.caption(f"Regime: {regime['label']}")

    evidence = st.session_state.get("evidence", {})
    for field in regime["evidence_fields"]:
        key = field["key"]
        label = field["label"] + (" *" if field.get("required") else "")
        if field["type"] == "textarea":
            evidence[key] = st.text_area(label, value=evidence.get(key, ""), height=100, key=f"ev_{key}")
        else:
            evidence[key] = st.text_input(label, value=evidence.get(key, ""), key=f"ev_{key}")
    st.session_state.evidence = evidence

    # Field-unlock enforcement: this regime's generative discipline (e.g.
    # C7's "state your evidence base before drafting anything else") is
    # structural here, not a suggestion — the Next button below is
    # literally disabled while errors exist, not just warned about.
    try:
        errors = unlock_rules.check(regime["code"], evidence)
    except Exception as e:
        logger.exception(f"unlock_rules.check raised for regime {regime['code']}")
        st.error(f"Could not evaluate this regime's field-unlock rules: {e}")
        errors = [f"Internal error evaluating unlock rules: {e}"]

    if errors:
        st.error("This regime's generative discipline blocks progress until:")
        for e in errors:
            st.markdown(f"- {e}")

    col1, col2 = st.columns([1, 1])
    with col1:
        if st.button("← Back"):
            st.session_state.step = 1
            st.rerun()
    with col2:
        if st.button("Next: Masked Review →", type="primary", disabled=bool(errors)):
            logger.info(f"Step 2 complete for regime {regime['code']}, evidence keys: {list(evidence.keys())}")
            st.session_state.step = 3
            st.rerun()


# ======================================================================
# STEP 3 — Masked Review Configuration
# ======================================================================
def page_step3():
    st.header("Step 3 — Masked Review Configuration")
    blind = st.checkbox(
        "Target venue requires masked/double-anonymous review",
        value=st.session_state.get("blind", False),
    )
    identifying_strings = st.session_state.get("identifying_strings", "")
    if blind:
        identifying_strings = st.text_area(
            "Identifying strings that must never appear in the blinded output "
            "(name, ORCID, institution, self-citation phrases) — one per line",
            value=identifying_strings,
            height=100,
        )
        st.caption(
            "Redaction here is a scaffold convenience only. Before real "
            "submission, verify the actual rendered artifact with "
            "blind_check_template.js — this checkbox does not substitute "
            "for that check."
        )
        if blind and not identifying_strings.strip():
            # Not a hard block (Step 2's unlock_rules pattern is reserved
            # for regime-generative discipline) — but worth a visible
            # nudge, since checking the box and leaving the list empty
            # would silently produce a blind-mode document that redacts
            # nothing.
            st.warning(
                "Masked review is checked but no identifying strings are "
                "listed — the blinded output will have nothing redacted."
            )

    col1, col2 = st.columns([1, 1])
    with col1:
        if st.button("← Back", key="b3back"):
            st.session_state.step = 2
            st.rerun()
    with col2:
        if st.button("Next: Sections →", type="primary", key="b3next"):
            st.session_state.blind = blind
            st.session_state.identifying_strings = identifying_strings
            logger.info(f"Step 3 complete: blind={blind}, identifying_strings_count={len(identifying_strings.splitlines())}")
            st.session_state.step = 4
            st.rerun()


# ======================================================================
# STEP 4 — Manuscript Sections
# ======================================================================
def _ensure_manuscript(conn):
    """
    Creates the manuscript row (and its section rows) exactly once per
    session, the first time Step 4 is reached — not earlier, because
    Steps 1-3 can still be revised via the Back button up to this point,
    and there's no reason to write to the database for a manuscript the
    user might still abandon or restart before ever seeing the section
    editor.
    """
    if st.session_state.get("manuscript_id"):
        return st.session_state.manuscript_id

    identifying_list = [
        s.strip() for s in st.session_state.get("identifying_strings", "").splitlines() if s.strip()
    ]

    try:
        mid = db.create_manuscript(
            conn,
            st.session_state.regime_code,
            st.session_state.title,
            st.session_state.claim,
            st.session_state.regime_statement,
            st.session_state.evidence,
            st.session_state.get("blind", False),
            identifying_list,
            datetime.datetime.now(datetime.timezone.utc).isoformat(),
        )
        logger.info(f"Created manuscript id={mid} regime={st.session_state.regime_code}")
    except Exception as e:
        logger.exception("Failed to create manuscript row.")
        st.error(f"Could not save your manuscript details to the database: {e}")
        st.stop()

    st.session_state.manuscript_id = mid
    return mid


def page_step4(conn):
    st.header("Step 4 — Manuscript Sections")
    st.caption(
        "Mirrors the manuscript-files screen of a typical submission system: "
        "each section below is a component of the final document."
    )

    mid = _ensure_manuscript(conn)

    try:
        sections = db.get_sections(conn, mid)
    except Exception as e:
        logger.exception(f"Failed to load sections for manuscript {mid}")
        st.error(f"Could not load manuscript sections: {e}")
        return

    editing_id = st.session_state.get("editing_section_id")

    for sec in sections:
        with st.container(border=True):
            c1, c2, c3 = st.columns([3, 1, 1])
            c1.markdown(f"**{sec['name']}**")
            badge = "✅ Drafted" if sec["status"] == "drafted" else "⬜ Not started"
            c2.caption(badge)
            if c3.button("Edit", key=f"edit_{sec['id']}"):
                st.session_state.editing_section_id = sec["id"]
                st.rerun()

            if editing_id == sec["id"]:
                if sec.get("hint"):
                    st.info(sec["hint"])
                new_content = st.text_area(
                    f"Content — {sec['name']}",
                    value=sec["content"],
                    height=200,
                    key=f"content_{sec['id']}",
                )
                cc1, cc2 = st.columns([1, 1])
                if cc1.button("Save", key=f"save_{sec['id']}", type="primary"):
                    try:
                        db.update_section(conn, sec["id"], new_content)
                        logger.info(f"Saved section '{sec['name']}' (id={sec['id']}) for manuscript {mid}")
                    except Exception as e:
                        logger.exception(f"Failed to save section id={sec['id']}")
                        st.error(f"Could not save this section: {e}")
                    else:
                        st.session_state.editing_section_id = None
                        st.rerun()
                if cc2.button("Cancel", key=f"cancel_{sec['id']}"):
                    st.session_state.editing_section_id = None
                    st.rerun()

    col1, col2 = st.columns([1, 1])
    with col1:
        if st.button("← Back", key="b4back"):
            st.session_state.step = 3
            st.rerun()
    with col2:
        if st.button("Next: Review & Verify →", type="primary", key="b4next"):
            st.session_state.step = 5
            st.rerun()


# ======================================================================
# STEP 5 — Review & Verify
# ======================================================================
def page_step5(conn):
    st.header("Step 5 — Review & Verify")
    mid = st.session_state.get("manuscript_id")

    if mid is None:
        # Reaching Step 5 without a manuscript_id means someone jumped
        # steps via session_state manipulation or a bug elsewhere — worth
        # a clear message rather than a downstream KeyError.
        st.error("No manuscript found for this session. Please start over from Step 1.")
        if st.button("← Start over"):
            st.session_state.step = 0
            st.rerun()
        return

    try:
        sections = db.get_sections(conn, mid)
    except Exception as e:
        logger.exception(f"Failed to load sections for review, manuscript {mid}")
        st.error(f"Could not load manuscript sections: {e}")
        return

    drafted = [s for s in sections if s["status"] == "drafted"]
    st.write(f"{len(drafted)} / {len(sections)} sections drafted.")
    for s in sections:
        icon = "✅" if s["status"] == "drafted" else "⬜"
        st.write(f"{icon} {s['name']}")

    ready = len(drafted) == len(sections)
    if not ready:
        st.warning(
            "Not every section has content yet. You can still download the "
            "scaffold with TODO placeholders, or go back and fill more in."
        )

    col1, col2 = st.columns([1, 1])
    with col1:
        if st.button("← Back", key="b5back"):
            st.session_state.step = 4
            st.rerun()
    with col2:
        if st.button("Next: Download →", type="primary", key="b5next"):
            logger.info(f"Manuscript {mid} proceeding to download ({len(drafted)}/{len(sections)} sections drafted)")
            st.session_state.step = 6
            st.rerun()


# ======================================================================
# STEP 6 — Download
# ======================================================================
def page_step6(conn, settings):
    st.header("Step 6 — Download")
    mid = st.session_state.get("manuscript_id")

    if mid is None:
        st.error("No manuscript found for this session. Please start over from Step 1.")
        if st.button("← Start over", key="b6restart"):
            st.session_state.step = 0
            st.rerun()
        return

    try:
        manuscript = db.get_manuscript(conn, mid)
        sections = db.get_sections(conn, mid)
    except Exception as e:
        logger.exception(f"Failed to load manuscript/sections for export, id={mid}")
        st.error(f"Could not load manuscript data for export: {e}")
        return

    if manuscript is None:
        st.error(f"Manuscript id={mid} was not found in the database.")
        return

    try:
        footer_text = settings["attribution"]["footer_text"].format(**settings["author"])
    except KeyError as e:
        logger.error(f"Footer template error during export: {e}")
        footer_text = "[Footer configuration error — see logs]"

    try:
        doc = docx_export.export_manuscript(
            manuscript, sections, footer_text,
            blind=manuscript["blind"], identifying_strings=manuscript["identifying_strings"],
        )
        buf = io.BytesIO()
        doc.save(buf)
        buf.seek(0)
    except Exception as e:
        logger.exception(f"Failed to export docx for manuscript {mid}")
        st.error(
            "Could not generate the .docx file. This usually means "
            f"python-docx is missing or misconfigured. Details: {e}"
        )
        return

    fname = "manuscript_BLINDED_scaffold.docx" if manuscript["blind"] else "manuscript_scaffold.docx"
    logger.info(f"Manuscript {mid} exported successfully as {fname}")

    st.download_button(
        "⬇ Download manuscript scaffold (.docx)",
        data=buf,
        file_name=fname,
        mime="application/vnd.openxmlformats-officedocument.wordprocessingml.document",
        type="primary",
    )
    st.caption(
        "This is a scaffold, not a finished manuscript. Run "
        "structural_lint.js (and blind_check_template.js if applicable) "
        "from the Manuscript Engineering Cookbook before any real delivery."
    )
    if st.button("← Back", key="b6back"):
        st.session_state.step = 5
        st.rerun()


# ======================================================================
# ADMIN
# ======================================================================
def page_admin(settings):
    st.header("Admin")

    if not st.session_state.get("admin_authed"):
        pw = st.text_input("Admin password", type="password")
        if st.button("Log in"):
            # Deliberately simple: this reference implementation treats
            # Admin as a single shared gate, not real per-user auth. See
            # README's "Known gaps" — worth replacing before any
            # non-local deployment.
            if pw == settings["admin"]["password"]:
                st.session_state.admin_authed = True
                logger.info("Admin login succeeded.")
                st.rerun()
            else:
                logger.warning("Admin login attempt failed (incorrect password).")
                st.error("Incorrect password.")
        return

    st.success("Logged in.")

    # ---- Run Ingest ----
    st.subheader("Run Ingest")
    st.caption(
        "Loads config/settings.yaml and config/regimes.yaml into the "
        "database. Re-run any time you edit those files."
    )
    if st.button("Run Ingest now", type="primary"):
        try:
            import ingest
            log_lines = ingest.run_ingest()
            for line in log_lines:
                st.write(line)
                logger.info(f"[ingest] {line}")
            get_conn.clear()  # force a fresh connection/read on next page load
            st.success("Ingest complete.")
        except FileNotFoundError as e:
            logger.exception("Ingest failed — a config file was not found.")
            st.error(f"Ingest failed: a config file was not found. {e}")
        except yaml.YAMLError as e:
            logger.exception("Ingest failed — a config file has invalid YAML.")
            st.error(f"Ingest failed: invalid YAML in a config file. {e}")
        except Exception as e:
            logger.exception("Ingest failed with an unexpected error.")
            st.error(f"Ingest failed: {e}")

    # ---- Current regimes ----
    st.subheader("Current regimes in DB")
    try:
        conn = get_conn()
        for r in db.list_regimes(conn):
            st.write(f"**{r['code']}** — {r['label']} ({len(r['sections'])} sections)")
    except Exception as e:
        logger.exception("Failed to list regimes in Admin panel.")
        st.error(f"Could not list regimes: {e}")

    # ---- LLM API keys ----
    st.subheader("LLM API keys")
    st.caption(
        "Resolution order: environment variable first, config/llm_config.yaml "
        "second. Not used by v0 (no AI-generated content) — present for "
        "future drafting-assistance features."
    )
    try:
        resolved = llm_keys.resolve_all(LLM_CONFIG_PATH)
        for key_name, (value, source) in resolved.items():
            if source == "env":
                st.write(
                    f"**{key_name}** — set via environment variable "
                    f"(`{llm_keys._ENV_VAR_NAMES[key_name]}`), overrides the file."
                )
            elif source == "file":
                st.write(f"**{key_name}** — set via config/llm_config.yaml.")
            else:
                st.write(f"**{key_name}** — not set.")
    except Exception as e:
        logger.exception("Failed to resolve LLM keys in Admin panel.")
        st.error(f"Could not resolve LLM key configuration: {e}")

    st.caption(
        "Edit the file value below (has no effect on any key currently "
        "set via environment variable):"
    )
    try:
        with open(LLM_CONFIG_PATH) as f:
            current = f.read()
    except FileNotFoundError:
        logger.warning(f"llm_config.yaml not found at {LLM_CONFIG_PATH}; starting from empty.")
        current = ""

    edited = st.text_area("llm_config.yaml", value=current, height=150)
    if st.button("Save LLM config"):
        try:
            yaml.safe_load(edited)  # validate before writing — don't save unparseable YAML
        except yaml.YAMLError as e:
            logger.error(f"Rejected LLM config save — invalid YAML: {e}")
            st.error(f"Not saved — this isn't valid YAML: {e}")
        else:
            try:
                with open(LLM_CONFIG_PATH, "w") as f:
                    f.write(edited)
                logger.info("llm_config.yaml saved via Admin panel.")
                st.success("Saved.")
            except OSError as e:
                logger.exception("Failed to write llm_config.yaml.")
                st.error(f"Could not write the file: {e}")


# ======================================================================
# MAIN — navigation dispatcher
# ======================================================================
def main():
    """
    The whole app is a single linear step machine, held in
    st.session_state.step (0-6), plus a sidebar toggle into an Admin view
    that sits outside the step flow entirely. Streamlit re-runs this
    function from scratch on every interaction; st.session_state is what
    survives between those reruns within one browser session.
    """
    try:
        settings = load_settings()
    except SystemExit:
        # load_settings() calls st.stop() on fatal config errors, which
        # raises SystemExit under the hood in some Streamlit versions —
        # nothing more to do here, the error is already on screen.
        return

    st.set_page_config(page_title=settings["app"]["title"], layout="centered")

    if "step" not in st.session_state:
        st.session_state.step = 0
        logger.info("New session started.")

    with st.sidebar:
        st.markdown(f"### {settings['app']['title']}")
        nav = st.radio("Navigate", ["Manuscript", "Admin"], index=0)

    conn = get_conn()

    if nav == "Admin":
        page_admin(settings)
        footer()
        return

    step = st.session_state.step
    step_pages = {
        0: lambda: page_landing(settings),
        1: lambda: page_step1(conn),
        2: lambda: page_step2(conn),
        3: page_step3,
        4: lambda: page_step4(conn),
        5: lambda: page_step5(conn),
        6: lambda: page_step6(conn, settings),
    }

    page_fn = step_pages.get(step)
    if page_fn is None:
        # Defensive: st.session_state.step somehow holds a value outside
        # 0-6 — shouldn't happen through normal navigation, but a stale
        # session after a code change (e.g. this file used to have more
        # steps) could produce it. Reset rather than showing a blank page.
        logger.warning(f"Unknown step value in session_state: {step!r}; resetting to 0.")
        st.session_state.step = 0
        page_landing(settings)
    else:
        page_fn()

    footer()


if __name__ == "__main__":
    main()