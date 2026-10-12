import argparse
import asyncio
import sys
from datetime import datetime
from pathlib import Path

import clean as clean_mod
import parse as parse_mod
import scrape as scrape_mod


async def run_scrape(max_records: int | None) -> int:
    conn = scrape_mod.init_cache(scrape_mod.CACHE_PATH)
    scrape_mod.OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    try:
        return await scrape_mod.scrape(conn, scrape_mod.OUTPUT_DIR, max_records=max_records)
    finally:
        conn.close()


def parse_args():
    parser = argparse.ArgumentParser(
        description="Run the full Ontario Lobbyist Registry pipeline: scrape -> parse -> clean."
    )
    parser.add_argument(
        "--max-records",
        type=int,
        default=None,
        help=(
            "Stop the scrape stage after this many new (uncached) records "
            "and run parse/clean over just those - an end-to-end smoke "
            "test (e.g. --max-records 10) instead of a full run."
        ),
    )
    parser.add_argument(
        "--force",
        action="store_true",
        help="Passed through to the clean stage - see src/clean.py --help.",
    )
    return parser.parse_args()


def main():
    args = parse_args()

    log_dir = Path("data/logs")
    log_dir.mkdir(parents=True, exist_ok=True)
    job_start = datetime.now()
    log_path = log_dir / f"driver-{job_start.strftime('%Y%m%d_%H%M%S')}.txt"

    original_stdout = sys.stdout
    original_stderr = sys.stderr
    log_file = log_path.open("w", encoding="utf-8")
    sys.stdout = scrape_mod.Tee(original_stdout, log_file)
    sys.stderr = scrape_mod.Tee(original_stderr, log_file)

    had_errors = False

    try:
        print("=== scrape ===")
        try:
            scraped = asyncio.run(run_scrape(args.max_records))
            print(f"scraped {scraped} new record(s)")
        except Exception as exc:
            had_errors = True
            print(f"scrape stage raised: {exc}", file=sys.stderr)
            print("continuing to parse/clean whatever was scraped before the failure")

        print("\n=== parse ===")
        parsed_ok, parsed_failed = parse_mod.parse_directory(
            parse_mod.INPUT_DIR, parse_mod.OUTPUT_DIR
        )
        print(f"parsed {parsed_ok} record(s) ({parsed_failed} failed)")
        had_errors = had_errors or bool(parsed_failed)

        print("\n=== clean ===")
        ingested, skipped, failed = clean_mod.ingest_directory(
            clean_mod.INPUT_DIR, clean_mod.DB_PATH, force=args.force
        )
        print(
            f"ingested {ingested} filing(s) "
            f"({skipped} already up to date, {failed} failed) into {clean_mod.DB_PATH}"
        )
        had_errors = had_errors or bool(failed)
    finally:
        sys.stdout = original_stdout
        sys.stderr = original_stderr
        log_file.close()

    if had_errors:
        sys.exit(1)


if __name__ == "__main__":
    main()
