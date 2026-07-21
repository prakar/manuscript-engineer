#!/usr/bin/env python3
"""
ingest.py — moves config/settings.yaml and config/regimes.yaml into the
SQLite database. This is the same pattern used in FAO and QHE: config is
authored in YAML for a human to edit, and ingest is a distinct, visible,
re-runnable step that loads it into the DB the app actually reads from.

Run directly:  python3 ingest.py
Or via the Admin panel's "Run Ingest" button (calls run_ingest()).

Idempotent: safe to re-run after editing YAML; regimes are upserted by
code, settings are upserted by key.
"""
import json
import os
import sys
import yaml

sys.path.insert(0, os.path.dirname(__file__))
from core import db  # noqa: E402

BASE_DIR = os.path.dirname(__file__)
CONFIG_DIR = os.path.join(BASE_DIR, "config")


def load_yaml(name):
    with open(os.path.join(CONFIG_DIR, name), "r") as f:
        return yaml.safe_load(f)


def run_ingest(db_path=None, verbose=True):
    settings = load_yaml("settings.yaml")
    regimes_doc = load_yaml("regimes.yaml")

    db_path = db_path or settings["database"]["path"]
    db_path = os.path.join(BASE_DIR, db_path) if not os.path.isabs(db_path) else db_path

    conn = db.connect(db_path)
    db.init_schema(conn)

    log = []

    # --- settings: flatten and upsert as key/value ---
    flat = _flatten(settings)
    for k, v in flat.items():
        db.set_setting(conn, k, str(v))
    log.append(f"Ingested {len(flat)} settings keys.")

    # --- regimes: upsert by code ---
    count = 0
    for regime in regimes_doc["regimes"]:
        conn.execute(
            "INSERT INTO regimes (code, label, description, regime_statement_template, "
            "sections_json, evidence_fields_json, unlock_rule) VALUES (?, ?, ?, ?, ?, ?, ?) "
            "ON CONFLICT(code) DO UPDATE SET "
            "label=excluded.label, description=excluded.description, "
            "regime_statement_template=excluded.regime_statement_template, "
            "sections_json=excluded.sections_json, "
            "evidence_fields_json=excluded.evidence_fields_json, "
            "unlock_rule=excluded.unlock_rule",
            (
                regime["code"],
                regime["label"],
                regime.get("description", ""),
                regime.get("regime_statement_template", ""),
                json.dumps(regime["sections"]),
                json.dumps(regime["evidence_fields"]),
                regime.get("unlock_rule"),
            ),
        )
        count += 1
    conn.commit()
    log.append(f"Ingested {count} regimes.")

    conn.close()
    if verbose:
        for line in log:
            print(line)
    return log


def _flatten(d, prefix=""):
    out = {}
    for k, v in d.items():
        key = f"{prefix}.{k}" if prefix else k
        if isinstance(v, dict):
            out.update(_flatten(v, key))
        else:
            out[key] = v
    return out


if __name__ == "__main__":
    run_ingest()
