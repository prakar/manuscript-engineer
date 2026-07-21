"""
app.py — Manuscript Engineer, Streamlit reference implementation.

Run: streamlit run app.py

Flow (mirrors SAGE/Elsevier submission-system screens):
  0. Landing
  1. Manuscript Details & Regime Selection
  2. Regime-Specific Evidence Intake (field-unlock enforced)
  3. Masked Review Configuration (optional)
  4. Manuscript Sections (Abstract, Introduction, ... each with Edit)
  5. Review & Verify
  6. Download

Sidebar: Admin (password-gated) — Run Ingest, view DB, edit LLM keys.
"""
import datetime
import io
import os

import streamlit as st
import yaml

from core import db, unlock_rules, docx_export

BASE_DIR = os.path.dirname(__file__)
CONFIG_DIR = os.path.join(BASE_DIR, "config")
SETTINGS_PATH = os.path.join(CONFIG_DIR, "settings.yaml")
LLM_CONFIG_PATH = os.path.join(CONFIG_DIR, "llm_config.yaml")


def load_settings():
    with open(SETTINGS_PATH) as f:
        return yaml.safe_load(f)


@st.cache_resource
def get_conn():
    settings = load_settings()
    db_path = os.path.join(BASE_DIR, settings["database"]["path"])
    conn = db.connect(db_path)
    db.init_schema(conn)
    return conn


def footer():
    settings = load_settings()
    text = settings["attribution"]["footer_text"].format(**settings["author"])
    st.markdown("---")
    st.caption(text)
    st.caption(settings["attribution"]["repo_url"])


# ---------------------------------------------------------------- Landing
def page_landing(settings):
    st.title(settings["app"]["title"])
    st.subheader(settings["app"]["tagline"])
    st.write(
        "This tool scaffolds a regime-correct, structurally-checked "
        "manuscript shell. It does not generate your paper's content, "
        "evaluate your claims, or replace domain judgment — it scaffolds "
        "and verifies; you still have to be right."
    )
    conn = get_conn()
    regime_count = len(db.list_regimes(conn))
    if regime_count == 0:
        st.warning(
            "No regimes loaded in the database yet. Go to Admin (sidebar) "
            "and click 'Run Ingest' to load config/regimes.yaml."
        )
    else:
        st.success(f"{regime_count} manuscript regimes loaded and ready.")
    if st.button("Start a new manuscript →", type="primary"):
        st.session_state.step = 1
        st.rerun()


# ------------------------------------------------------- Step 1: Details
def page_step1(conn):
    st.header("Step 1 — Manuscript Details")
    regimes = db.list_regimes(conn)
    if not regimes:
        st.error("No regimes loaded. Go to Admin → Run Ingest first.")
        return

    labels = [f"{r['label']}" for r in regimes]
    codes = [r["code"] for r in regimes]
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
            st.session_state.regime_idx = idx
            st.session_state.regime_code = regime["code"]
            st.session_state.title = title
            st.session_state.claim = claim
            st.session_state.regime_statement = regime_statement
            st.session_state.step = 2
            st.rerun()


# --------------------------------------------------- Step 2: Evidence
def page_step2(conn):
    st.header("Step 2 — Regime-Specific Evidence Intake")
    regime = db.get_regime(conn, st.session_state.regime_code)
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

    errors = unlock_rules.check(regime["code"], evidence)
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
            st.session_state.step = 3
            st.rerun()


# --------------------------------------------------- Step 3: Blind review
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

    col1, col2 = st.columns([1, 1])
    with col1:
        if st.button("← Back", key="b3back"):
            st.session_state.step = 2
            st.rerun()
    with col2:
        if st.button("Next: Sections →", type="primary", key="b3next"):
            st.session_state.blind = blind
            st.session_state.identifying_strings = identifying_strings
            st.session_state.step = 4
            st.rerun()


# --------------------------------------------------- Step 4: Sections
def _ensure_manuscript(conn):
    if st.session_state.get("manuscript_id"):
        return st.session_state.manuscript_id
    ids = [s.strip() for s in st.session_state.get("identifying_strings", "").splitlines() if s.strip()]
    mid = db.create_manuscript(
        conn,
        st.session_state.regime_code,
        st.session_state.title,
        st.session_state.claim,
        st.session_state.regime_statement,
        st.session_state.evidence,
        st.session_state.get("blind", False),
        ids,
        datetime.datetime.now(datetime.timezone.utc).isoformat(),
    )
    st.session_state.manuscript_id = mid
    return mid


def page_step4(conn):
    st.header("Step 4 — Manuscript Sections")
    st.caption(
        "Mirrors the manuscript-files screen of a typical submission system: "
        "each section below is a component of the final document."
    )
    mid = _ensure_manuscript(conn)
    sections = db.get_sections(conn, mid)

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
                    db.update_section(conn, sec["id"], new_content)
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


# --------------------------------------------------- Step 5: Review
def page_step5(conn):
    st.header("Step 5 — Review & Verify")
    mid = st.session_state.manuscript_id
    sections = db.get_sections(conn, mid)

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
            st.session_state.step = 6
            st.rerun()


# --------------------------------------------------- Step 6: Download
def page_step6(conn, settings):
    st.header("Step 6 — Download")
    mid = st.session_state.manuscript_id
    manuscript = db.get_manuscript(conn, mid)
    sections = db.get_sections(conn, mid)

    footer_text = settings["attribution"]["footer_text"].format(**settings["author"])
    doc = docx_export.export_manuscript(
        manuscript, sections, footer_text,
        blind=manuscript["blind"], identifying_strings=manuscript["identifying_strings"],
    )
    buf = io.BytesIO()
    doc.save(buf)
    buf.seek(0)

    fname = "manuscript_BLINDED_scaffold.docx" if manuscript["blind"] else "manuscript_scaffold.docx"
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


# --------------------------------------------------- Admin
def page_admin(settings):
    st.header("Admin")
    if not st.session_state.get("admin_authed"):
        pw = st.text_input("Admin password", type="password")
        if st.button("Log in"):
            if pw == settings["admin"]["password"]:
                st.session_state.admin_authed = True
                st.rerun()
            else:
                st.error("Incorrect password.")
        return

    st.success("Logged in.")

    st.subheader("Run Ingest")
    st.caption(
        "Loads config/settings.yaml and config/regimes.yaml into the "
        "database. Re-run any time you edit those files."
    )
    if st.button("Run Ingest now", type="primary"):
        import ingest
        log = ingest.run_ingest()
        for line in log:
            st.write(line)
        get_conn.clear()
        st.success("Ingest complete.")

    st.subheader("Current regimes in DB")
    conn = get_conn()
    for r in db.list_regimes(conn):
        st.write(f"**{r['code']}** — {r['label']} ({len(r['sections'])} sections)")

    st.subheader("LLM API keys")
    st.caption(
        "Stored in config/llm_config.yaml as plaintext, local to this "
        "deployment. Not used by v0 (no AI-generated content) — present "
        "for future drafting-assistance features."
    )
    with open(LLM_CONFIG_PATH) as f:
        current = f.read()
    edited = st.text_area("llm_config.yaml", value=current, height=150)
    if st.button("Save LLM config"):
        yaml.safe_load(edited)  # validate before writing
        with open(LLM_CONFIG_PATH, "w") as f:
            f.write(edited)
        st.success("Saved.")


# --------------------------------------------------------------- Main
def main():
    settings = load_settings()
    st.set_page_config(page_title=settings["app"]["title"], layout="centered")

    if "step" not in st.session_state:
        st.session_state.step = 0

    with st.sidebar:
        st.markdown(f"### {settings['app']['title']}")
        nav = st.radio("Navigate", ["Manuscript", "Admin"], index=0)

    conn = get_conn()

    if nav == "Admin":
        page_admin(settings)
        footer()
        return

    step = st.session_state.step
    if step == 0:
        page_landing(settings)
    elif step == 1:
        page_step1(conn)
    elif step == 2:
        page_step2(conn)
    elif step == 3:
        page_step3()
    elif step == 4:
        page_step4(conn)
    elif step == 5:
        page_step5(conn)
    elif step == 6:
        page_step6(conn, settings)

    footer()


if __name__ == "__main__":
    main()
