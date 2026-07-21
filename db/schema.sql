-- schema.sql
-- Mirrors the FAO/QHE pattern: config lives in YAML, gets ingested into
-- these tables by Admin -> Run Ingest. The app reads from the DB at
-- runtime, never directly from YAML, so ingest is a real, visible step
-- (and re-running it after a YAML edit is how changes take effect).

CREATE TABLE IF NOT EXISTS settings (
  key   TEXT PRIMARY KEY,
  value TEXT
);

CREATE TABLE IF NOT EXISTS regimes (
  code                    TEXT PRIMARY KEY,
  label                   TEXT NOT NULL,
  description             TEXT,
  regime_statement_template TEXT,
  sections_json           TEXT NOT NULL,   -- [{name, hint}, ...] in build order
  evidence_fields_json     TEXT NOT NULL,   -- [{key, label, type, required}, ...]
  unlock_rule             TEXT
);

CREATE TABLE IF NOT EXISTS manuscripts (
  id                     INTEGER PRIMARY KEY AUTOINCREMENT,
  regime_code            TEXT NOT NULL REFERENCES regimes(code),
  title                  TEXT,
  claim                  TEXT,
  regime_statement       TEXT,
  evidence_json          TEXT,             -- answers to that regime's evidence_fields
  blind                  INTEGER DEFAULT 0,
  identifying_strings_json TEXT,
  created_at             TEXT,
  updated_at             TEXT
);

CREATE TABLE IF NOT EXISTS manuscript_sections (
  id             INTEGER PRIMARY KEY AUTOINCREMENT,
  manuscript_id  INTEGER NOT NULL REFERENCES manuscripts(id),
  name           TEXT NOT NULL,
  order_idx      INTEGER NOT NULL,
  hint           TEXT,
  content        TEXT DEFAULT '',
  status         TEXT DEFAULT 'not_started'  -- not_started | drafted
);
