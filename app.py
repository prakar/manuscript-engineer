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
import hashlib
import io
import logging
import os
import sys

import streamlit as st
import yaml

from core import db, unlock_rules, docx_export, llm_keys, tool_runner, structural_check

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
TOOLS_DIR = os.path.join(BASE_DIR, "cookbook_tools")

# Single source of truth for what each automatic check does, in plain
# language. Referenced from the Step 5/6 verification gate; the Admin
# panel's Cookbook Tools cards currently have their own longer versions
# of this same content — worth reconciling into one shared source later,
# flagged rather than silently left to drift.
CHECK_NARRATIVES = {
    "structure": (
        "Reads every heading in the generated document and confirms each "
        "section your paper type expects is present, and in the right order. "
        "Does not check the *quality* of what's inside a section — only "
        "that the section exists where it should."
    ),
    "blind": (
        "Reads the actual text of every paragraph in the generated document "
        "and confirms none of your declared identifying strings (name, "
        "ORCID, institution) appear anywhere in the body. Does not check "
        "document metadata (author/company properties) or headers/footers "
        "— use Word's own 'Inspect Document' feature for those."
    ),
}


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
        "This tool builds a correctly-structured manuscript shell for your "
        "paper type, with automatic checks along the way. It does not "
        "generate your paper's content, evaluate your claims, or replace "
        "your own judgment — it builds the structure and checks it; you "
        "still have to be right."
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
            "No paper types loaded yet. Go to Admin (sidebar) "
            "and click 'Run Ingest' to load config/regimes.yaml."
        )
    else:
        st.success(f"{regime_count} paper types loaded and ready.")

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
        "What are you trying to show or prove? (one sentence)",
        value=st.session_state.get("claim", ""),
        height=80,
    )
    default_stmt = regime["regime_statement_template"]
    regime_statement = st.text_area(
        "Guiding statement for this paper — every section below will be checked against it",
        value=st.session_state.get("regime_statement", default_stmt),
        height=80,
    )

    col1, col2 = st.columns([1, 1])
    with col2:
        if st.button("Next: Supporting Details →", type="primary"):
            # Basic sanity check before letting the user proceed — an
            # empty title or claim isn't a hard block (unlike the
            # regime-specific field-unlock rules in Step 2), but warning
            # about it here is cheaper for the user than discovering a
            # blank title on the final downloaded document.
            if not title.strip():
                st.warning("Working title is empty — you can still continue, but consider filling it in.")
            if not claim.strip():
                st.warning("That field is empty — you can still continue, but consider filling it in.")

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
    st.header("Step 2 — Supporting Details")

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
            "The paper type you selected is no longer available. "
            "Please go back and choose again."
        )
        if st.button("← Back to Step 1"):
            st.session_state.step = 1
            st.rerun()
        return

    st.caption(f"Paper type: {regime['label']}")

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
        st.error(f"Could not check whether you're ready to continue: {e}")
        errors = [f"Internal error while checking: {e}"]

    if errors:
        st.error("A few things need attention before you can continue:")
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
            "This just tells the app which words to hide. Before you can "
            "download anything, the app automatically double-checks that "
            "none of these actually appear in the final document — see "
            "Step 5."
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
        "each section below is a component of the final document. Where a "
        "writing tip appears, treat it as a checklist item — nothing "
        "re-checks it for you later."
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
def _content_hash(manuscript, sections):
    """
    Cheap fingerprint of everything that affects the generated docx, so
    Step 5 only re-runs the verification gate when something actually
    changed (editing a section, toggling blind mode) rather than on every
    Streamlit rerun a click anywhere on the page triggers.
    """
    parts = [
        manuscript.get("title") or "",
        manuscript.get("claim") or "",
        manuscript.get("regime_statement") or "",
        str(manuscript.get("blind")),
        "|".join(manuscript.get("identifying_strings") or []),
    ]
    for s in sections:
        parts.append(f"{s['name']}:{s['content']}")
    return hashlib.sha256("\n".join(parts).encode("utf-8")).hexdigest()


def _build_docx_bytes(manuscript, sections, footer_text):
    """Builds the manuscript docx and returns raw bytes (not a BytesIO —
    bytes survive being stashed in st.session_state across reruns more
    predictably than a stateful file-like object)."""
    doc = docx_export.export_manuscript(
        manuscript, sections, footer_text,
        blind=manuscript["blind"], identifying_strings=manuscript["identifying_strings"],
    )
    buf = io.BytesIO()
    doc.save(buf)
    return buf.getvalue()


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
        manuscript = db.get_manuscript(conn, mid)
        sections = db.get_sections(conn, mid)
    except Exception as e:
        logger.exception(f"Failed to load manuscript/sections for review, manuscript {mid}")
        st.error(f"Could not load manuscript data: {e}")
        return

    drafted = [s for s in sections if s["status"] == "drafted"]
    st.write(f"{len(drafted)} / {len(sections)} sections drafted.")
    for s in sections:
        icon = "✅" if s["status"] == "drafted" else "⬜"
        st.write(f"{icon} {s['name']}")

    if len(drafted) != len(sections):
        st.warning(
            "Not every section has content yet. Verification below still "
            "runs — it checks section presence and order, not whether "
            "content exists — but you can go back and fill more in first."
        )

    st.subheader("Automatic Verification")
    st.caption(
        "This runs every time before you can download, no matter how the "
        "document was built. See Instructions for a plain-language "
        "explanation of what these checks do and don't catch."
    )

    try:
        settings = load_settings()
        footer_text = settings["attribution"]["footer_text"].format(**settings["author"])
    except Exception as e:
        logger.exception("Failed to load settings/footer for verification build.")
        st.error(f"Could not prepare verification: {e}")
        return

    current_hash = _content_hash(manuscript, sections)
    cached_hash = st.session_state.get("verified_hash")
    needs_run = (cached_hash != current_hash) or ("verified_docx_bytes" not in st.session_state)

    if needs_run:
        with st.status("Running automatic verification…", expanded=True) as status:
            status.write("Building manuscript document from current section content…")
            try:
                docx_bytes = _build_docx_bytes(manuscript, sections, footer_text)
            except Exception as e:
                logger.exception(f"Failed to build docx for verification, manuscript {mid}")
                status.update(label="Verification failed — could not build document.", state="error")
                st.error(f"Could not build the document to verify: {e}")
                return
            status.write("Document built.")

            all_findings = []
            overall_ok = True

            status.write("---")
            status.write("**Structural check** — " + CHECK_NARRATIVES["structure"])
            expected_names = [s["name"] for s in sections]
            tmp_path = os.path.join(BASE_DIR, "db", f"_verify_tmp_{mid}.docx")
            try:
                with open(tmp_path, "wb") as f:
                    f.write(docx_bytes)
                ok, findings = structural_check.check_structure(tmp_path, expected_names)
            except Exception as e:
                logger.exception(f"Structural check failed to run for manuscript {mid}")
                ok, findings = False, [f"Structural check could not run: {e}"]

            overall_ok = overall_ok and ok
            if findings:
                for f_line in findings:
                    status.write(("⚠️ " if f_line.startswith("Note:") else "❌ ") + f_line)
            else:
                status.write("✅ No structural issues found.")
            all_findings.extend(findings)

            if manuscript["blind"]:
                status.write("---")
                status.write("**Blind-content check** — " + CHECK_NARRATIVES["blind"])
                try:
                    ok2, findings2 = structural_check.check_blind(tmp_path, manuscript["identifying_strings"])
                except Exception as e:
                    logger.exception(f"Blind check failed to run for manuscript {mid}")
                    ok2, findings2 = False, [f"Blind check could not run: {e}"]
                overall_ok = overall_ok and ok2
                if findings2:
                    for f_line in findings2:
                        status.write("❌ " + f_line)
                else:
                    status.write("✅ No identifying strings found.")
                all_findings.extend(findings2)

            try:
                os.remove(tmp_path)
            except OSError:
                pass

            st.session_state.verified_hash = current_hash
            st.session_state.verified_docx_bytes = docx_bytes
            st.session_state.verified_ok = overall_ok
            st.session_state.verified_findings = all_findings

            logger.info(f"Verification for manuscript {mid}: ok={overall_ok}, findings={len(all_findings)}")

            if overall_ok:
                status.update(label="Verification complete — no issues found.", state="complete")
            else:
                status.update(label=f"Verification complete — {len(all_findings)} issue(s) found.", state="error")
    else:
        if st.session_state.get("verified_ok"):
            st.success("Verification complete — no issues found. (Cached — nothing changed since last run.)")
        else:
            st.error(f"Verification found {len(st.session_state.get('verified_findings', []))} issue(s). (Cached — nothing changed since last run.)")
            for f_line in st.session_state.get("verified_findings", []):
                st.write(("⚠️ " if f_line.startswith("Note:") else "❌ ") + f_line)
        if st.button("Re-run verification", key="rerun_verify"):
            st.session_state.pop("verified_hash", None)
            st.rerun()

    col1, col2 = st.columns([1, 1])
    with col1:
        if st.button("← Back", key="b5back"):
            st.session_state.step = 4
            st.rerun()
    with col2:
        if st.button("Next: Download →", type="primary", key="b5next"):
            logger.info(f"Manuscript {mid} proceeding to download step (verified_ok={st.session_state.get('verified_ok')})")
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
    except Exception as e:
        logger.exception(f"Failed to load manuscript for download, id={mid}")
        st.error(f"Could not load manuscript data: {e}")
        return

    if manuscript is None:
        st.error(f"Manuscript id={mid} was not found in the database.")
        return

    docx_bytes = st.session_state.get("verified_docx_bytes")
    verified_ok = st.session_state.get("verified_ok")

    if docx_bytes is None:
        # Shouldn't normally happen — Step 5 always runs verification
        # before this page is reachable via its own Next button — but a
        # stale session (e.g. reloaded mid-flow) could land here without
        # it. Send back to Step 5 rather than silently building an
        # unverified file.
        st.warning("This manuscript hasn't been verified yet in this session.")
        if st.button("← Go to Step 5 to verify", type="primary"):
            st.session_state.step = 5
            st.rerun()
        return

    fname = "manuscript_draft_BLINDED.docx" if manuscript["blind"] else "manuscript_draft.docx"

    if verified_ok:
        st.success("This document passed automatic verification.")
        st.download_button(
            "⬇ Download manuscript draft (.docx)",
            data=docx_bytes,
            file_name=fname,
            mime="application/vnd.openxmlformats-officedocument.wordprocessingml.document",
            type="primary",
        )
        logger.info(f"Manuscript {mid} downloaded (verified_ok=True)")
    else:
        findings = st.session_state.get("verified_findings", [])
        st.error(
            f"This document did NOT pass automatic verification "
            f"({len(findings)} issue(s) found). Downloading it means "
            "delivering something with a known structure or blind-review "
            "problem."
        )
        for f_line in findings:
            st.write(("⚠️ " if f_line.startswith("Note:") else "❌ ") + f_line)
        override = st.checkbox(
            "I understand the risk and want to download anyway (not recommended)."
        )
        if override:
            st.download_button(
                "⬇ Confirm download (unverified)",
                data=docx_bytes,
                file_name=fname,
                mime="application/vnd.openxmlformats-officedocument.wordprocessingml.document",
            )
            logger.warning(f"Manuscript {mid} downloaded WITH override despite verified_ok=False ({len(findings)} findings)")

    st.caption(
        "This is a structured starting point, not a finished manuscript — "
        "every section still needs your real writing."
    )

    with st.expander("If you keep editing this file somewhere else later"):
        st.write(
            "The checks above only cover the file exactly as downloaded "
            "right now. If you continue editing it in a different tool or "
            "pipeline afterward, re-run these same checks on the final "
            "version before you actually submit it — Admin can run them "
            "against any file, not just ones built here."
        )
        if st.button("→ Re-check the structure of a later version", key="jump_lint_step6"):
            st.session_state.nav_radio = "Admin"
            st.session_state.admin_jump_to = "structural_lint.js"
            st.rerun()
        if manuscript["blind"]:
            if st.button("→ Re-check that names stay hidden in a later version", key="jump_blind_step6"):
                st.session_state.nav_radio = "Admin"
                st.session_state.admin_jump_to = "blind_check_template.js"
                st.rerun()

    if st.button("← Back", key="b6back"):
        st.session_state.step = 5
        st.rerun()


# ======================================================================
# INSTRUCTIONS
# ======================================================================
def page_instructions(conn, settings):
    """
    A standalone tab, not a step in the manuscript flow. Forked by
    audience: a researcher trying to use the tool and someone evaluating
    or extending it want fundamentally different content, and burying
    both under one linear page meant the first reader had to wade through
    design rationale and test scripts meant for the second. README.md
    remains the canonical, fuller version of the evaluator-facing content
    (design rationale, full test path) — the evaluator branch here points
    to it rather than duplicating it, so the two can't quietly drift apart.
    """
    st.header("Instructions")

    audience = st.radio(
        "Who's reading this right now?",
        ["I'm writing a paper", "I'm evaluating this tool"],
        horizontal=True,
        key="instructions_audience",
    )

    if audience == "I'm writing a paper":
        st.write(
            "This tool builds a correctly-structured, section-by-section "
            "starting document for your paper, and automatically checks "
            "it before you download. It does not write your paper's "
            "content, evaluate your claims, or replace your own judgment."
        )

        st.subheader("How the flow works")
        st.markdown(
            "1. **Manuscript Details** — title, what you're trying to "
            "show, paper type.\n"
            "2. **Supporting Details** — fields specific to your paper "
            "type; some types require you to fill in a specific thing "
            "before you can continue (see the reference table below).\n"
            "3. **Masked Review** — optional; lists names/words that "
            "should be hidden if your venue requires anonymous review.\n"
            "4. **Sections** — every section your paper type needs, each "
            "editable on its own, with a done/not-done status.\n"
            "5. **Review & Verify** — automatic checks run here, before "
            "you can download.\n"
            "6. **Download** — the .docx file."
        )

        st.subheader("Paper types — reference")
        st.write(
            "What each paper type means, and — once you pick one in "
            "Step 1 — the writing tips you'll see while drafting each of "
            "its sections. Click a tab to switch types."
        )
        try:
            regimes = db.list_regimes(conn)
        except Exception as e:
            logger.exception("Failed to load regimes for Instructions tab.")
            st.error(f"Could not load the paper-type table: {e}")
            regimes = []

        if regimes:
            tabs = st.tabs([r["label"] for r in regimes])
            for tab, r in zip(tabs, regimes):
                with tab:
                    st.write(r["description"])
                    sec_names = " → ".join(s["name"] for s in r["sections"])
                    st.caption("Sections, in order:")
                    st.write(sec_names)
                    hints = [s for s in r["sections"] if s.get("hint")]
                    if hints:
                        st.caption("Writing tips shown while drafting these sections:")
                        for s in hints:
                            st.markdown(f"- **{s['name']}**: {s['hint']}")
                    if r.get("unlock_rule"):
                        st.caption("This type requires one extra thing before you can continue past Step 2 (see the form for details).")

        st.subheader("What this tool does — and where its responsibility ends")
        st.error(
            "**The hand-off, stated plainly:** this tool's job ends at a "
            "correctly-structured, clearly-labeled starting document. "
            "Everything after that — whether the claim is true, whether "
            "the evidence actually supports it, whether the writing is "
            "any good, whether the citations are real — is your "
            "responsibility, not this tool's. Nothing in this app reads "
            "or evaluates the *content* you type into a section; it only "
            "tracks whether a section has content at all."
        )
        st.markdown(
            "**Concretely, this tool does:**\n"
            "- Ask which type of paper you're writing, and hold every "
            "section to that type's own stated job.\n"
            "- Stop you at the small number of things that are genuinely "
            "checkable (a required field being empty, an item missing its "
            "required follow-up).\n"
            "- Show (not enforce) writing tips at the section they apply "
            "to.\n"
            "- Produce a downloadable .docx with every section labeled, "
            "and placeholders anywhere content is missing.\n"
            "- Automatically double-check the document's structure and, "
            "if you used masked review, that nothing identifying leaked "
            "through — every time, before you can download.\n\n"
            "**This tool does not:**\n"
            "- Generate, draft, or suggest manuscript content of any "
            "kind.\n"
            "- Evaluate whether a claim, a query result, or a citation is "
            "correct.\n"
            "- Check document metadata (author/company file properties) "
            "or headers/footers for identifying content — use Word's own "
            "'Inspect Document' feature for those.\n"
            "- Call any AI/LLM. The API-key settings exist for a future "
            "feature; nothing in this version uses them."
        )

    else:  # "I'm evaluating this tool"
        st.subheader("Intent and purpose")
        st.write(
            "A cluster of recent tools checks the *content* of a finished "
            "manuscript — does a citation exist, does it support the "
            "claim attributed to it, do numbers agree across sections. "
            "None of them help with actually *building* the manuscript in "
            "the first place, especially when it's written iteratively, "
            "with an AI tool making structural edits across many "
            "rounds.\n\n"
            "This tool helps with the building, not the writing: it gives "
            "you a correctly-structured section-by-section shell for your "
            "paper type, runs automatic checks, and surfaces the specific "
            "habits the Manuscript Engineering Cookbook found useful for "
            "avoiding recurring mistakes in AI-assisted manuscript "
            "construction."
        )

        st.subheader("Design")
        st.write(
            "Three ideas drive the design, in order of how much they "
            "shape the UI:\n\n"
            "1. **Different kinds of papers need different checks.** A "
            "methodology paper's most common mistake (the worked example "
            "swallowing the actual method) isn't a systematic review's "
            "most common mistake (search results quietly shaping the "
            "inclusion criteria after the fact). One generic template "
            "can't serve both well.\n"
            "2. **Some checks are enforced, some are just shown.** Step 2 "
            "blocks you from continuing for the small number of things "
            "that can be checked mechanically (is this field filled in, "
            "does every item have the follow-up it needs). The writing "
            "tips shown while you draft each section are the opposite "
            "case — genuine judgment calls nothing can automatically "
            "verify, so they're shown as reminders, not enforced.\n"
            "3. **Settings live outside the code.** Every paper type, "
            "section, hint, and personalization detail is in "
            "`config/*.yaml`, loaded into the database by Admin → Run "
            "Ingest. Adding an eighth paper type doesn't require touching "
            "any Python."
        )

        st.subheader("Target audience")
        st.write(
            "Individual researchers and small teams writing a manuscript "
            "iteratively, especially with an AI tool making structural "
            "edits across many rounds — the exact situation where "
            "mistakes like silently dropped sections, false 'all good' "
            "checks, and botched renumbering actually happen. Not aimed "
            "at journals, publishers, or institutional workflows — the "
            "single shared admin password and lack of multi-user login "
            "make that explicit (see Known Gaps in the README)."
        )

        st.subheader("Full test path")
        st.write(
            "The complete step-by-step test path — including the "
            "specific case that once let an identifying name slip past "
            "redaction — lives in **README.md, 'Testing the flow'**. Kept "
            "there rather than duplicated here, since a page and a "
            "README describing the same test independently is exactly "
            "the kind of drift this project tries to avoid elsewhere "
            "(see CHECK_NARRATIVES in the code, or the earlier tool-copy "
            "drift between the Cookbook zip and the live CEA tools)."
        )
        st.caption(
            "Note: within this app's own flow, both automatic checks are "
            "close to impossible to fail by construction. They earn their "
            "keep mainly on outside files brought in through Admin, not "
            "on this app's own well-formed output."
        )

        st.subheader("Where this fits in the broader project")
        st.write(
            "This is a reference implementation of the Manuscript "
            "Engineering Cookbook — a set of practices for the "
            "*construction* of a manuscript (not its content), developed "
            "against a real, ongoing paper and generalized to seven "
            "distinct paper types. See the repository README for the "
            "Cookbook itself and the tools this app wraps."
        )


# ======================================================================
# ADMIN
# ======================================================================
def page_admin(settings):
    st.header("Admin")

    if not st.session_state.get("admin_authed"):
        st.caption(
            "Default password is set in `config/settings.yaml` — change "
            "it there before putting this app anywhere beyond your own "
            "machine."
        )
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

    # ---- Cookbook Tools ----
    st.subheader("Cookbook Tools")
    if st.session_state.get("admin_jump_to"):
        jump_target = st.session_state.pop("admin_jump_to")
        st.info(f"Jumped here from Instructions → {jump_target}")

    st.caption(
        "These tools verify a manuscript's *construction*, not its content "
        "— see the Instructions tab for the full explanation. This app's "
        "own docx export (Step 6) does not produce a build.js, so most of "
        "these operate on a directory you point them at — typically the "
        "Node CLI's `manuscript-output/` folder, not this app's own output."
    )

    if not tool_runner.node_available():
        st.warning(
            "`node` was not found on PATH in this environment. "
            "structural_lint.js and blind_check_template.js require "
            "Node.js — install it, or run these tools directly in a "
            "terminal instead."
        )

    # --- structural_lint.js ---
    with st.container(border=True):
        st.markdown("#### structural_lint.js")
        st.write(
            "**What it does:** static analysis of a `build.js` file. "
            "Generic — works on any project's build.js as-is, no per-"
            "project editing required first."
        )
        st.markdown(
            "**Checks:** every `buildX()` function is registered in "
            "`SECTIONS` (and vice versa) · heading number sequence (H1s, "
            "sub-numbers) · in-text Section/Appendix references resolve to "
            "a real heading · paren balance · orphaned function bodies "
            "(the anchor-drop signature) · Figure/Table references resolve "
            "to a real caption."
        )
        st.markdown(
            "**Caution:** regex-based, not a real parser — it can't tell "
            "a commented-out `SECTIONS` entry from a live one. The bundled "
            "reference template itself fails this check for exactly that "
            "reason (its example section list has commented-out entries "
            "the regex still picks up). A clean pass means no *detected* "
            "structural break, not proof of correctness."
        )
        target_dir_lint = st.text_input(
            "Directory containing your build.js",
            value=st.session_state.get("lint_target_dir", ""),
            key="lint_target_dir_input",
            placeholder="/workspaces/manuscript-engineer/manuscript-output",
        )
        if st.button("Run structural_lint.js", key="run_lint"):
            st.session_state.lint_target_dir = target_dir_lint
            if not target_dir_lint.strip():
                st.error("Enter a directory first.")
            else:
                ok, out, err = tool_runner.run_structural_lint(
                    os.path.join(TOOLS_DIR, "structural_lint.js"),
                    target_dir_lint.strip(),
                )
                logger.info(f"structural_lint.js run against {target_dir_lint}: success={ok}")
                if ok:
                    st.success("No structural errors found.")
                else:
                    st.error("structural_lint.js found problems, or could not run:")
                if out:
                    st.code(out, language=None)
                if err:
                    st.code(err, language=None)

    # --- blind_check_template.js ---
    with st.container(border=True):
        st.markdown("#### blind_check_template.js")
        st.write(
            "**What it does:** regenerates your blinded document fresh and "
            "reads the actual rendered docx XML — real content, not source "
            "code — checking a declared list of identifying strings is "
            "genuinely absent."
        )
        st.markdown(
            "**Checks:** author name, ORCID, institution, and any other "
            "declared identifying phrase do not appear anywhere in the "
            "real rendered `manuscript_BLINDED.docx`."
        )
        st.markdown(
            "**Caution:** this is a *different kind* of check from "
            "structural_lint.js (content-leak vs. code-structure) — run "
            "both, neither substitutes for the other. It is NOT generic: "
            "it must already be customized for your project (identifying "
            "strings, build command, output path). The Node CLI's "
            "generate.js fills these in automatically when you choose "
            "masked review during classification — this button runs "
            "whatever's already sitting in the target directory, it does "
            "not fill in a template for you. This app's own Step 3 "
            "redaction is a separate, weaker convenience — not "
            "equivalent to this check."
        )
        target_dir_blind = st.text_input(
            "Directory containing your customized blind_check_template.js",
            value=st.session_state.get("blind_target_dir", ""),
            key="blind_target_dir_input",
            placeholder="/workspaces/manuscript-engineer/manuscript-output",
        )
        if st.button("Run blind_check_template.js", key="run_blind"):
            st.session_state.blind_target_dir = target_dir_blind
            if not target_dir_blind.strip():
                st.error("Enter a directory first.")
            else:
                ok, out, err = tool_runner.run_blind_check(target_dir_blind.strip())
                logger.info(f"blind_check_template.js run against {target_dir_blind}: success={ok}")
                if ok:
                    st.success("No identifying strings found in the blinded output.")
                else:
                    st.error("blind_check_template.js found a leak, or could not run:")
                if out:
                    st.code(out, language=None)
                if err:
                    st.code(err, language=None)

    # --- citation_verification_template.py ---
    with st.container(border=True):
        st.markdown("#### citation_verification_template.py")
        st.write(
            "**What it does:** structured CrossRef lookup per citation — "
            "title/journal/year matching against what your manuscript "
            "claims. Not a text search; catches mismatches automatically."
        )
        st.markdown(
            "**Checks:** for each citation with a DOI, an exact CrossRef "
            "lookup confirms title/journal/year match what you expect. "
            "For citations without a DOI, a bibliographic search is run "
            "(less reliable — verify matches by eye)."
        )
        st.markdown(
            "**Caution:** does **not** check volume/page numbers — a real "
            "page-range error once passed a clean run undetected for "
            "exactly this reason; always manually diff those against your "
            "citation log. Requires network access to CrossRef and the "
            "`requests` package. Not generic: `CITATIONS` must already be "
            "filled in with your paper's real references before running."
        )
        target_dir_cite = st.text_input(
            "Directory containing your filled-in citation_verification_template.py",
            value=st.session_state.get("cite_target_dir", ""),
            key="cite_target_dir_input",
            placeholder="/workspaces/manuscript-engineer/manuscript-output",
        )
        if st.button("Run citation_verification_template.py", key="run_cite"):
            st.session_state.cite_target_dir = target_dir_cite
            if not target_dir_cite.strip():
                st.error("Enter a directory first.")
            else:
                ok, out, err = tool_runner.run_citation_verification(target_dir_cite.strip())
                logger.info(f"citation_verification_template.py run against {target_dir_cite}: success={ok}")
                if ok:
                    st.success("No mismatches detected (see caution above — volume/page not checked).")
                else:
                    st.error("citation_verification_template.py found mismatches, or could not run:")
                if out:
                    st.code(out, language=None)
                if err:
                    st.code(err, language=None)

    # --- safe_renumber_template.js — reference only, deliberately no Run button ---
    with st.container(border=True):
        st.markdown("#### safe_renumber_template.js")
        st.write(
            "**What it does:** single-pass renumbering via an explicit "
            "mapping (e.g. `{\"3\": \"4\", \"2\": \"3\"}`), avoiding the "
            "double-shift bug sequential find-replace causes."
        )
        st.markdown(
            "**Checks:** nothing automatically — it performs the "
            "substitution you specify, it does not verify your mapping is "
            "correct or that every match is a genuine section reference "
            "rather than a coincidence (a citation year, a stray decimal)."
        )
        st.markdown(
            "**Caution — no Run button here, deliberately:** every other "
            "tool on this page is safe to invoke against a directory you "
            "name, because either it's fully generic (structural_lint.js) "
            "or it fails loudly if not yet customized (blind_check, "
            "citation_verification). This one is different: run unedited, "
            "it silently applies its own example mapping "
            "(`{\"7\":\"8\", \"6\":\"7\", ...}`) to whatever build.js it finds "
            "— a wrong-but-successful run, not a loud failure. Per the "
            "Cookbook: grep every occurrence of your target numbers by "
            "hand and confirm each is a real reference before running "
            "this. Copy it into your project, edit the `MAP`, then run it "
            "yourself from a terminal:"
        )
        st.code("node safe_renumber_template.js", language="bash")
        with st.expander("View reference source"):
            try:
                with open(os.path.join(TOOLS_DIR, "safe_renumber_template.js")) as f:
                    st.code(f.read(), language="javascript")
            except FileNotFoundError:
                st.error("Bundled reference copy not found — check cookbook_tools/.")


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
        if "nav_radio" not in st.session_state:
            st.session_state.nav_radio = "Manuscript"
        nav = st.radio("Navigate", ["Manuscript", "Instructions", "Admin"], key="nav_radio")

    conn = get_conn()

    if nav == "Instructions":
        page_instructions(conn, settings)
        footer()
        return

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