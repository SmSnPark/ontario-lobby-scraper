-- Ontario Lobbyist Registry: schema for ingesting data/parsed/*.json into
-- SQL for analysis.

-- Two tables:
--   registrations: one row per registration_number - the stable identity
--                  (type, initial filing date don't change across
--                  amendments).

--   filings:       one row per parsed JSON snapshot - the registry
--                  re-emits a registration with a new source_hash each
--                  time it's amended, so this is effectively a filing
--                  history, not just a "current state" table. Repeating
--                  groups (lobbyists, subject_matters, lobbying_activities,
--                  government_funding) are stored as JSON text columns
--                  rather than normalized into child tables - query them
--                  with SQLite's json1 functions (json_each, json_extract).

CREATE TABLE IF NOT EXISTS registrations (
    registration_number   TEXT PRIMARY KEY,
    lobbyist_type         TEXT NOT NULL CHECK (lobbyist_type IN (
                               'Consultant Lobbyist',
                               'In-house Organization',
                               'In-house Persons & Partnerships'
                           )),
    initial_filing_date   DATE NOT NULL
);

CREATE TABLE IF NOT EXISTS filings (
    filing_id             INTEGER PRIMARY KEY,
    -- Scrape-time content hash from the source filename (<registration_number>__<hash>.json). 
    -- Already unique on its own (registration_number is one of its hash inputs)
    source_hash           TEXT NOT NULL UNIQUE,
    registration_number   TEXT NOT NULL REFERENCES registrations(registration_number),
    source_file           TEXT NOT NULL,
    last_filed_date       DATE NOT NULL,
    org_name              TEXT NOT NULL,
    org_address_raw       TEXT NOT NULL,
    org_description       TEXT NOT NULL DEFAULT '',
    lobbyists             TEXT NOT NULL,   -- JSON: [{"name": "...", "position": "..." | null}]
    subject_matters       TEXT NOT NULL,   -- JSON: ["...", ...]
    lobbying_activities   TEXT NOT NULL,   -- JSON: [{"category": "...", "goal": "...", "targets": ["...", ...]}]
    government_funding    TEXT NOT NULL,   -- JSON: [{"source": "...", "amount": 0.0}]
    ingested_at           TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE INDEX IF NOT EXISTS idx_filings_registration
    ON filings(registration_number, last_filed_date);
