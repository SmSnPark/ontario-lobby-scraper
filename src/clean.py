import argparse
import json
import re
import sqlite3
import sys
from pathlib import Path

from models import ParsedRegistration

SCHEMA_PATH = Path(__file__).resolve().parent / "schema.sql"

INPUT_DIR = Path("data/parsed")
DB_PATH = Path("data/lobbyists.sqlite3")

#<registration_number>__<hash>.json, as written by src/scrape.py
FILENAME_PATTERN = re.compile(r"^(?P<registration_number>.+)__(?P<hash>[0-9a-f]{32})$")

WHITESPACE_PATTERN = re.compile(r"\s+")
SPACE_BEFORE_PUNCTUATION_PATTERN = re.compile(r"\s+([,;:])")


def normalize_text(s: str) -> str:
    """Collapse internal whitespace runs, drop space before punctuation, and trim ends."""

    s = WHITESPACE_PATTERN.sub(" ", s)
    s = SPACE_BEFORE_PUNCTUATION_PATTERN.sub(r"\1", s)
    return s.strip()


def parse_filename(path: Path) -> tuple[str, str]:
    """Return (registration_number, source_hash) encoded in path's filename."""

    match = FILENAME_PATTERN.match(path.stem)
    if not match:
        raise ValueError(
            f"Filename {path.name!r} doesn't match the expected "
            f"<registration_number>__<hash>.json pattern."
        )
    return match.group("registration_number"), match.group("hash")


def clean_lobbyists(record: ParsedRegistration) -> list[dict]:
    """Returns list of normalized names and positions of lobbyists"""
    return [
        {
            "name": normalize_text(l.name),
            "position": normalize_text(l.position) if l.position else None,
        }
        for l in record.lobbyists
    ]


def clean_subject_matters(record: ParsedRegistration) -> list[str]:
    """Normalizes sujet matters"""
    return [normalize_text(s) for s in record.subject_matters]


def clean_lobbying_activities(record: ParsedRegistration) -> list[dict]:
    """Noramlizes and returns list of lobbying activities"""
    return [
        {
            "category": normalize_text(a.category),
            "goal": normalize_text(a.goal),
            "targets": [normalize_text(t) for t in a.targets],
        }
        for a in record.lobbying_activities
    ]


def clean_government_funding(record: ParsedRegistration) -> list[dict]:
    """returns list of normalized government funding records"""
    return [
        {"source": normalize_text(g.source), "amount": g.amount}
        for g in record.government_funding
    ]


def init_db(db_path: Path) -> sqlite3.Connection:
    """Open (creating if needed) the cleaned SQLite database and apply schema.sql.
    """

    db_path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(db_path)
    conn.executescript(SCHEMA_PATH.read_text(encoding="utf-8"))
    return conn


def ingest_record(
    conn: sqlite3.Connection,
    record: ParsedRegistration,
    source_hash: str,
    source_file: str,
) -> None:
    """Insert one filing (and its parent registration, if new) as a single transaction, so a mid-file failure never leaves a half-ingested row."""

    with conn:
        conn.execute(
            """
            INSERT OR IGNORE INTO registrations
                (registration_number, lobbyist_type, initial_filing_date)
            VALUES (?, ?, ?)
            """,
            (
                record.registration_number,
                record.lobbyist_type,
                record.status.initial_filing_date.isoformat(),
            ),
        )

        existing = conn.execute(
            "SELECT lobbyist_type, initial_filing_date FROM registrations "
            "WHERE registration_number = ?",
            (record.registration_number,),
        ).fetchone()
        if existing != (
            record.lobbyist_type,
            record.status.initial_filing_date.isoformat(),
        ):
            print(
                f"WARNING {source_file}: registration {record.registration_number} "
                f"identity mismatch - stored {existing}, this filing has "
                f"({record.lobbyist_type!r}, "
                f"{record.status.initial_filing_date.isoformat()!r}). "
                f"Keeping the originally stored identity.",
                file=sys.stderr,
            )

        conn.execute(
            """
            INSERT OR REPLACE INTO filings (
                source_hash, registration_number, source_file,
                last_filed_date, org_name, org_address_raw, org_description,
                lobbyists, subject_matters, lobbying_activities, government_funding
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                source_hash,
                record.registration_number,
                source_file,
                record.status.last_filed_date.isoformat(),
                normalize_text(record.organization.name),
                normalize_text(record.organization.address),
                normalize_text(record.organization.description),
                json.dumps(clean_lobbyists(record)),
                json.dumps(clean_subject_matters(record)),
                json.dumps(clean_lobbying_activities(record)),
                json.dumps(clean_government_funding(record)),
            ),
        )


def ingest_directory(
    input_dir: Path,
    db_path: Path,
    force: bool = False,
) -> tuple[int, int, int]:
    """Ingest every parsed record JSON in input_dir into db_path.
    """

    conn = init_db(db_path)
    try:
        already_ingested = {
            row[0] for row in conn.execute("SELECT source_hash FROM filings")
        }

        succeeded = 0
        skipped = 0
        failed = 0

        for path in sorted(input_dir.glob("*.json")):
            try:
                registration_number, source_hash = parse_filename(path)

                if not force and source_hash in already_ingested:
                    skipped += 1
                    continue

                raw = json.loads(path.read_text(encoding="utf-8"))
                record = ParsedRegistration.model_validate(raw)

                if record.registration_number != registration_number:
                    raise ValueError(
                        f"filename registration number {registration_number!r} "
                        f"doesn't match parsed registration_number "
                        f"{record.registration_number!r}"
                    )

                ingest_record(conn, record, source_hash, path.name)
                already_ingested.add(source_hash)
                succeeded += 1
                print(f"  ingested {registration_number} ({record.lobbyist_type})")
            except Exception as exc:
                print(f"FAILED  {path.name}: {exc}", file=sys.stderr)
                failed += 1
                continue

        return succeeded, skipped, failed
    finally:
        conn.close()


def parse_args():
    parser = argparse.ArgumentParser(
        description="Clean and ingest parsed Ontario Lobbyist Registry JSON into SQLite."
    )
    parser.add_argument(
        "--force",
        action="store_true",
        help=(
            "Reprocess files even if their source_hash is already ingested, "
            "replacing the existing filing row. Use after a cleaning-logic "
            "change to reapply it to already-ingested files."
        ),
    )
    return parser.parse_args()


def main():
    args = parse_args()

    succeeded, skipped, failed = ingest_directory(INPUT_DIR, DB_PATH, force=args.force)

    print(
        f"\nIngested {succeeded} filings ({skipped} already up to date, "
        f"{failed} failed) into {DB_PATH}"
    )

    if failed:
        sys.exit(1)


if __name__ == "__main__":
    main()
