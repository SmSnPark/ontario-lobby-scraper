# ontario-lobbyist-scraper

A scrape → parse → ingest pipeline for the [Ontario Lobbyist Registry](https://lobbyist.oico.on.ca/Pages/Public/PublicSearch/), the public record of lobbying registrations filed with the Office of the Integrity Commissioner of Ontario. It turns the registry's web pages into a validated, queryable SQLite database.

The pipeline has three stages. Each runs on its own, so a crash in one never forces the others to re-run:

1. **Scrape** (`src/scrape.py`) drives the registry's "Recent 30 Day View" in a headless browser and writes one JSON file per registration (metadata + detail-page HTML) to `data/scrape/`. A content-hash cache (`data/seen.sqlite3`) skips records already harvested on previous runs.
2. **Parse** (`src/parse.py`) extracts a typed schema from each scraped record, validates it against the Pydantic models in `src/models.py`, and writes the result to `data/parsed/`.
3. **Clean** (`src/clean.py`) normalizes text fields and ingests each record into `data/lobbyists.sqlite3`. Ingestion is idempotent: files whose content hash is already in the database are skipped.

## Extracted schema

Each registration yields:

- `registration_number`
- `lobbyist_type` (Consultant Lobbyist, In-house Organization, or In-house Persons & Partnerships)
- `status.initial_filing_date`, `status.last_filed_date`
- `organization` (name, address, description)
- `lobbyists`
- `subject_matters`
- `lobbying_activities` (category, goal, targets)
- `government_funding`

See `src/models.py` for the Pydantic models and `src/schema.sql` for the database tables.

## Requirements

- Python 3.10+
- Chromium (installed through Playwright)

## Setup

```sh
pip install -r requirements.txt
playwright install chromium
```

## Usage

The driver runs all three stages in one process. Point a cron job or Windows Task Scheduler at it for unattended runs:

```sh
python src/driver.py --max-records 5   # quick end-to-end run
python src/driver.py                   # every record from the past 30 days
```

> The registry is a Telerik/ASP.NET WebForms app and is slow to navigate. Use `--max-records 5` or lower when trying the tool out.

Or run each stage on its own (for example, to inspect `data/scrape/` before parsing it):

```sh
python src/scrape.py   # writes data/scrape/, cache at data/seen.sqlite3
python src/parse.py    # reads data/scrape/, writes data/parsed/
python src/clean.py    # reads data/parsed/, ingests into data/lobbyists.sqlite3
```

### Options

| Flag              | Applies to         | Effect                                                                                                |
| ----------------- | ------------------ | ----------------------------------------------------------------------------------------------------- |
| `--max-records N` | `scrape`, `driver` | Stop after scraping N new (uncached) records. Already-seen rows are skipped and don't count toward N. |
| `--force`         | `clean`, `driver`  | Reprocess files even if they were already ingested (for example, after changing the cleaning logic).  |

Each driver run writes a log to `data/logs/`. To browse the output, open `data/lobbyists.sqlite3` in any SQLite client and sort by ingestion date to see the newest rows.

## Evaluation

```sh
python eval/run_eval.py
```

The eval runs offline. It sends 11 cached real registrations (`eval/cases/`) through the actual parse and clean code and compares the output with hand-verified expected values (`eval/expected/`). A case passes only if every schema field matches exactly after normalization. Results are written to `eval/results.md`.

The cases were chosen to cover quirks in the registry's human-entered data:

- subject matters joined with `;` in some records and with `<br>` in others
- funding tables with real currency formatting
- records listing more than 55 lobbyists

`eval/build_expected.py` builds the expected values with a second, independent XPath extraction.

Current accuracy: **10/11**. The failing case is a known bug, listed under [Known limitations](#known-limitations).

### How the registry publishes

The registry has no release schedule. Lobbyists file or amend registrations whenever they choose, and the province publishes them on an irregular schedule. A new filing shows up as a new row in the "Recent Active" search, but it isn't guaranteed to land at the top: amendment date is the only available sort, and filings are sometimes published a day late. If you run the driver hourly, the delay from a filing appearing to a validated row in SQLite is about an hour, set by the publishing schedule rather than by this tool.

## Known limitations

- **Placeholder subject matter.** The registry renders "no subject matter selected" as `-`. `parse_subject_matters` in `src/parse.py` filters that placeholder out of lobbying-activity targets but not out of subject matters, so such records parse to `["-"]` instead of `[]`.
- **Rows shift during a run.** The search results are a live, stateful grid sorted by amendment date, not a stable, page-addressable snapshot. If a registration is filed or amended while a scrape is running, rows behind it move down the list. Content-hash dedup prevents duplicates, but a row the scraper hadn't reached yet can be pushed past the run's window and skipped until the next run. Watching the first page for changes doesn't work because new filings don't always land on top, and row totals can stay the same while rows enter and leave the view. In practice, running the scraper more often is the mitigation.
- **Stages process whole directories.** Parse and clean act on everything in `data/scrape/` and `data/parsed/`, not just the files from the current run, and nothing removes processed files. Clean skips records that are already ingested, but parse re-parses every file on every run. Clear those folders occasionally to keep runs fast.

## Disclaimer

This project is not affiliated with or endorsed by the Office of the Integrity Commissioner of Ontario. It collects publicly available registry data.

## License

[MIT](LICENSE)
