"""
core/db.py — SQLite access layer. No Streamlit import here on purpose:
this module (and ingest.py, docx_export.py) must be testable and runnable
with plain `python3`, independent of whether streamlit is installed.
"""
import json
import sqlite3
import os

SCHEMA_PATH = os.path.join(os.path.dirname(__file__), "..", "db", "schema.sql")


def connect(db_path):
    os.makedirs(os.path.dirname(db_path) or ".", exist_ok=True)
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    return conn


def init_schema(conn):
    with open(SCHEMA_PATH, "r") as f:
        conn.executescript(f.read())
    conn.commit()


def get_setting(conn, key, default=None):
    row = conn.execute("SELECT value FROM settings WHERE key = ?", (key,)).fetchone()
    return row["value"] if row else default


def set_setting(conn, key, value):
    conn.execute(
        "INSERT INTO settings (key, value) VALUES (?, ?) "
        "ON CONFLICT(key) DO UPDATE SET value = excluded.value",
        (key, value),
    )
    conn.commit()


def list_regimes(conn):
    rows = conn.execute("SELECT * FROM regimes ORDER BY code").fetchall()
    out = []
    for r in rows:
        d = dict(r)
        d["sections"] = json.loads(d.pop("sections_json"))
        d["evidence_fields"] = json.loads(d.pop("evidence_fields_json"))
        out.append(d)
    return out


def get_regime(conn, code):
    row = conn.execute("SELECT * FROM regimes WHERE code = ?", (code,)).fetchone()
    if not row:
        return None
    d = dict(row)
    d["sections"] = json.loads(d.pop("sections_json"))
    d["evidence_fields"] = json.loads(d.pop("evidence_fields_json"))
    return d


def create_manuscript(conn, regime_code, title, claim, regime_statement,
                       evidence, blind, identifying_strings, created_at):
    cur = conn.execute(
        "INSERT INTO manuscripts "
        "(regime_code, title, claim, regime_statement, evidence_json, blind, "
        " identifying_strings_json, created_at, updated_at) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
        (
            regime_code, title, claim, regime_statement,
            json.dumps(evidence), int(blind), json.dumps(identifying_strings),
            created_at, created_at,
        ),
    )
    conn.commit()
    manuscript_id = cur.lastrowid

    regime = get_regime(conn, regime_code)
    for idx, sec in enumerate(regime["sections"]):
        conn.execute(
            "INSERT INTO manuscript_sections (manuscript_id, name, order_idx, hint, content, status) "
            "VALUES (?, ?, ?, ?, '', 'not_started')",
            (manuscript_id, sec["name"], idx, sec.get("hint")),
        )
    conn.commit()
    return manuscript_id


def get_manuscript(conn, manuscript_id):
    row = conn.execute("SELECT * FROM manuscripts WHERE id = ?", (manuscript_id,)).fetchone()
    if not row:
        return None
    d = dict(row)
    d["evidence"] = json.loads(d.pop("evidence_json") or "{}")
    d["identifying_strings"] = json.loads(d.pop("identifying_strings_json") or "[]")
    return d


def get_sections(conn, manuscript_id):
    rows = conn.execute(
        "SELECT * FROM manuscript_sections WHERE manuscript_id = ? ORDER BY order_idx",
        (manuscript_id,),
    ).fetchall()
    return [dict(r) for r in rows]


def update_section(conn, section_id, content):
    status = "drafted" if content.strip() else "not_started"
    conn.execute(
        "UPDATE manuscript_sections SET content = ?, status = ? WHERE id = ?",
        (content, status, section_id),
    )
    conn.commit()
