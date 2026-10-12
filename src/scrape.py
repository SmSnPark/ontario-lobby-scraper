import argparse
import asyncio
import hashlib
import json
import re
import sqlite3
import sys
from datetime import datetime
from pathlib import Path
from urllib.parse import urljoin

from playwright.async_api import (
    BrowserContext,
    Error as PlaywrightError,
    Page,
    async_playwright,
)


ROOT_URL = "https://lobbyist.oico.on.ca/Pages/Public/PublicSearch/"
HOMEPAGE = urljoin(ROOT_URL, "Default.aspx")

OUTPUT_DIR = Path("data/scrape")
CACHE_PATH = Path("data/seen.sqlite3")
LOG_DIR = Path("data/logs")

REQUEST_DELAY_MS = 2000 #tested to be the lowest possible without either breaking telerik response time or getting banned by the government of ontario

USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
)

BLOCKED_RESOURCE_TYPES = {"image", "font", "stylesheet"} #no noticeable improvements on scrape speed, but no detrimental effect found.

PAGE_SIZE = 50

PAGER_WINDOW_SIZE = 10


async def block_unnecessary_assets(context: BrowserContext) -> None:
    """Uses BLOCKED_RESOURCE_TYPES to block out bloat on page to optimize speeds"""
    async def _maybe_abort(route):
        if route.request.resource_type in BLOCKED_RESOURCE_TYPES:
            await route.abort()
        else:
            await route.continue_()

    await context.route("**/*", _maybe_abort)


def init_cache(db_path: Path) -> sqlite3.Connection:
    """Initialiize cache sqlite db locally"""
    db_path.parent.mkdir(parents=True, exist_ok=True)

    conn = sqlite3.connect(db_path)
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS seen_hashes (
            hash TEXT PRIMARY KEY,
            first_seen TEXT NOT NULL
        )
        """
    )
    conn.commit()

    return conn


def is_seen(conn: sqlite3.Connection, row_hash: str) -> bool:
    cursor = conn.execute(
        "SELECT 1 FROM seen_hashes WHERE hash = ?",
        (row_hash,),
    )

    return cursor.fetchone() is not None


def mark_seen(conn: sqlite3.Connection, row_hash: str) -> None:
    conn.execute(
        "INSERT OR IGNORE INTO seen_hashes (hash, first_seen) VALUES (?, ?)",
        (row_hash, datetime.now().isoformat()),
    )
    conn.commit()


async def get_results(
    page: Page,
) -> None:

    await page.locator(
        "xpath=//input[@id='BodyContent_ucQuickSearch_btnRecentActive']"
    ).click()

    await page.wait_for_load_state("domcontentloaded")


async def get_number_of_records(page: Page) -> int:
    """Retrieve total number of records on page"""
    html = await page.content()

    if "no records available" in html.lower():
        return 0

    #"1 - 10 of 123 items" eg
    info = page.locator(
        "xpath=//div[@class='rgWrap rgInfoPart']/strong"
    )

    if await info.count():
        text = await info.first.text_content()

        if text:
            match = re.search(r"\d[\d,]*", text)
            if match:
                return int(match.group().replace(",", ""))

    #fallback: count result rows
    rows = page.locator("xpath=//tr[contains(@id, '__')]")
    return await rows.count()


PAGE_SIZE_COMBOBOX_ID = (
    "ctl00_BodyContent_ucSearchResults_gridSearchResults_"
    "GridRegistrationList_ctl00_ctl03_ctl01_PageSizeComboBox"
)

LOADING_PANEL_SELECTOR = "xpath=//div[contains(@id, 'RadAjaxLoadingPanel')]"


async def wait_for_loading_panel_hidden(page: Page, timeout: int = 30000) -> None:
    """Backup wait for RadAjaxLoadingPanel during pagination"""
    #RadAjaxLoadingPanel overlay would bug out on playwright UA that isn't chromium based, causing this to timeout if using ff or webkit

    panel = page.locator(LOADING_PANEL_SELECTOR).first
    await panel.wait_for(state="hidden", timeout=timeout)


async def set_page_size(page: Page) -> None:
    """Set the results grid to PAGE_SIZE rows per page via its Telerik RadComboBox control.
    """

    arrow = page.locator(f"xpath=//a[@id='{PAGE_SIZE_COMBOBOX_ID}_Arrow']")
    dropdown = page.locator(
        f"xpath=//div[@id='{PAGE_SIZE_COMBOBOX_ID}_DropDown']"
    )

    await arrow.click()
    await dropdown.wait_for(state="visible", timeout=10000)

    option = dropdown.locator(
        f"xpath=.//li[normalize-space(text())='{PAGE_SIZE}']"
    )
    await option.wait_for(state="visible", timeout=10000)
    await option.click()

    await wait_for_loading_panel_hidden(page)
    await page.wait_for_timeout(5000)


async def get_page_numbers(page: Page) -> list[str]:
    '''Retrieve page number list from curr page'''
    links = page.locator(
        "xpath=//a[contains(@href, 'GridRegistrationList')]/span"
    )

    pages = []

    for i in range(await links.count()):
        text = await links.nth(i).text_content()

        if text and text.strip().isdigit():
            pages.append(text.strip())

    return pages


async def get_row_signature(page: Page, page_index: int) -> str:
    '''Retrieve Registration Number from table view'''
    #registration number of registry is confirmed to be singleton (can be amended multiple times tho)
    row = page.locator(
        "xpath=//tr[substring(@id, string-length(@id) - "
        f"string-length('__{page_index}') + 1) = '__{page_index}']"
    )
    cells = row.locator("td")

    if await cells.count() < 6:
        return ""

    return (await cells.nth(5).text_content() or "").strip()


class PaginationTimeout(RuntimeError):
    """Pager click didn't do anything / page looks stuck.
    Split out from RuntimeError so retry logic can target this one case instead of catching everything and hoping for the best.
    """


async def wait_for_grid_refresh(
    page: Page,
    previous_signature: str,
    attempts: int = 30,
    interval_ms: int = 300,
) -> None:
    """
    Poll until the grid's first row shows a new, non-empty registration.
    makes sure the old page content isn't lingering
    """

    for _ in range(attempts):
        try:
            current = await get_row_signature(page, 0)
        except PlaywrightError:
            current = ""

        if current and current != previous_signature:
            return

        await page.wait_for_timeout(interval_ms)

    raise PaginationTimeout(
        f"Results grid still shows the same first row "
        f"(registration number {previous_signature!r}) after waiting "
        f"{attempts * interval_ms / 1000:.1f}s for pagination to take effect."
    )


async def wait_for_page_numbers_change(
    page: Page,
    previous_page_numbers: list[str],
    attempts: int = 30,
    interval_ms: int = 300,
) -> None:
    """Poll until the pager's visible page-number window changes.
    Neither this reveal click nor the numbered-page click below has a reliable network event to wait on so we poll for those instead of trying to synchronize on the network layer.
    """

    for _ in range(attempts):
        try:
            current = await get_page_numbers(page)
        except PlaywrightError:
            current = []

        if current and current != previous_page_numbers:
            return

        await page.wait_for_timeout(interval_ms)

    raise PaginationTimeout(
        f"Pager still shows the same page-number window "
        f"{previous_page_numbers!r} after waiting "
        f"{attempts * interval_ms / 1000:.1f}s for the reveal click to take effect."
    )


DO_POSTBACK_OVERRIDE_JS = """
() => {
    window.__doPostBack = function __doPostBack(
        eventTarget,
        eventArgument
    ) {
        var theForm = document.forms['form1'];
        theForm.__EVENTTARGET.value = eventTarget;
        theForm.__EVENTARGUMENT.value = eventArgument;
        theForm.submit();
    }
}
"""

MAX_PAGINATION_REVEALS = 5


async def go_to_results_page(page: Page, desired_page: int) -> None:
    """ Navigate forward to desired_page by repeatedly revealing further pager number windows (via the "next range" ellipsis) until desired_page is directly clickable, then clicking it.
    """

    window_start = (desired_page - 1) % PAGER_WINDOW_SIZE == 0
    required_reveals = (
        (desired_page - 1) // PAGER_WINDOW_SIZE + (0 if window_start else 1)
    )
    max_attempts = max(MAX_PAGINATION_REVEALS, required_reveals + 3)

    for _ in range(max_attempts):
        try:
            previous_signature = await get_row_signature(page, 0)

            await page.evaluate(DO_POSTBACK_OVERRIDE_JS)

            await page.wait_for_load_state("networkidle") #dropping this can improve speeds but risks of proceeding bfore all rows loaded

            page_numbers = await get_page_numbers(page)

            if not window_start and str(desired_page) in page_numbers:
                target_anchor = page.locator(
                    "xpath=//a[contains(@href, 'GridRegistrationList')]"
                    f"[.//span[normalize-space(text())='{desired_page}']]"
                )
                await target_anchor.first.click()
                await wait_for_grid_refresh(page, previous_signature)

                # grid visibly keeps settling for ~5s after a postback well past what wait_for_grid_refresh's to see change
                await page.wait_for_timeout(5000)
                return

            anchors = page.locator(
                "xpath=//a[contains(@href, 'GridRegistrationList')]"
            )
            count = await anchors.count()

            if not 11 <= count <= 12:
                raise RuntimeError(
                    f"Unexpected pagination state: "
                    f"desired_page={desired_page}, "
                    f"page_numbers={page_numbers}, "
                    f"button_count={count}"
                )

            current_window_last = max(
                (int(n) for n in page_numbers), default=0
            )
            reveal_reaches_target = (
                window_start and current_window_last + 1 == desired_page
            )

            await anchors.nth(count - 1).click()

            if reveal_reaches_target:
                await wait_for_grid_refresh(page, previous_signature)
                await page.wait_for_timeout(5000)
                return

            await wait_for_page_numbers_change(page, page_numbers) #flag for full load complete
            await page.wait_for_timeout(5000)
        except (PlaywrightError, PaginationTimeout) as exc:
            #playwrighterror: when scraper interacted with page while nav in flight 
            #paginationtimeout: page clicks can happen as site rebuilds dom, losing that click
            print(
                f"    (pagination retry toward page {desired_page}: "
                f"{exc})"
            )
            await page.wait_for_timeout(200)

    raise RuntimeError(
        f"Could not reach page {desired_page} after "
        f"{max_attempts} pagination window reveals."
    )


async def extract_registration(page: Page) -> dict:
    """Extract the metadata available on table view before traversing into record detail page"""
    prefix = "BodyContent_ucRegistrationSubmit_ProfileInformation_"

    filed_on = await page.locator(
        f"#{prefix}lblFiledonDateValue"
    ).text_content()

    body_html = await page.locator("body").inner_html() #inner_text() is altenrative

    return {
        "filed_on": (
            filed_on.strip()
            if filed_on
            else None
        ),
        "html": body_html,  
    }


async def crawl_record(
    page: Page,
    record_index: int,
    conn: sqlite3.Connection,
) -> dict | None:
    """Clicks on one row, opens, and harvests relevent HTML, """

    page_index = record_index % PAGE_SIZE
    row = page.locator(
        "xpath=//tr[substring(@id, string-length(@id) - "
        f"string-length('__{page_index}') + 1) = '__{page_index}']"
    )

    cells = row.locator("td")

    if await cells.count() < 6:
        raise RuntimeError(
            f"Could not find expected result row for index {record_index}"
        )

    #metadata directly living on table rows before detail page
    lobbyist = (await cells.nth(0).text_content() or "").strip()
    last_amendment_date = (await cells.nth(1).text_content() or "").strip()
    client_name = (await cells.nth(2).text_content() or "").strip()
    company_organization = (await cells.nth(3).text_content() or "").strip()
    lobbyist_type = (await cells.nth(4).text_content() or "").strip()
    registration_number = (await cells.nth(5).text_content() or "").strip()

    if not registration_number:
        print(
            f"  Skipping record {record_index + 1}: "
            f"empty registration number"
        )
        return None

    row_hash = hashlib.md5(
        "".join(
            [
                lobbyist,
                last_amendment_date,
                client_name,
                company_organization,
                lobbyist_type,
                registration_number,
            ]
        ).encode("utf-8")
    ).hexdigest()

    if is_seen(conn, row_hash):
        print(
            f"  Skipping record {record_index + 1} "
            f"({registration_number}): already seen (hash={row_hash})"
        )
        return None

    endpoint_map = { #url id for each type of lobbyist set by govt of ontario
        "Consultant Lobbyist": "ConsultantLobbyist",
        "In-house Organization": "Organization",
        "In-house Persons & Partnerships": "PersonAndPartnerships",
    }

    if lobbyist_type not in endpoint_map:
        raise ValueError(
            f"Unknown lobbyist type: {lobbyist_type!r}"
        )

    endpoint = endpoint_map[lobbyist_type]

    results_url = page.url

    await row.click()
    
    detail_url_pattern = re.compile(
        "|".join(re.escape(name) for name in endpoint_map.values())
        + r"Preview\.aspx"
    )

    await page.wait_for_url(
        detail_url_pattern,
        wait_until="domcontentloaded",
    )

    expected_url = urljoin(ROOT_URL, f"{endpoint}Preview.aspx")

    if page.url != expected_url:
        raise RuntimeError(
            f"Row read as {lobbyist_type!r} (expected to land on "
            f"{expected_url}) but navigated to {page.url} instead - "
            f"the results grid likely changed between reading this "
            f"row and clicking it (pagination race)."
        )

    data = await extract_registration(page)

    #bring in data from table before
    data["lobbyist"] = lobbyist
    data["last_amendment_date"] = last_amendment_date
    data["client_name"] = client_name
    data["company_organization"] = company_organization
    data["lobbyist_type"] = lobbyist_type
    data["registration_number"] = registration_number
    data["hash"] = row_hash

    await page.locator(
        "xpath=//input[@id='BodyContent_ucRegistrationSubmit_btnBackToResultsTop']"
    ).click()

    await page.wait_for_url(
        results_url,
        wait_until="domcontentloaded",
    )

    return data


def write_record(output_dir: Path, record: dict) -> None:
    """
    Writes each record as individual jsons for storage. Filename is <registration_number>__<hash>.json because of multiple filings under one registration number
    """

    safe_registration_number = re.sub(
        r"[^A-Za-z0-9_-]",
        "_",
        record["registration_number"],
    )

    record_path = output_dir / f"{safe_registration_number}__{record['hash']}.json"

    with record_path.open(
        "w",
        encoding="utf-8",
    ) as f:
        json.dump(
            record,
            f,
            indent=2,
            ensure_ascii=False,
        )


RECOVERY_RETRY_DELAYS = [5, 15, 30, 60, 60]


async def recover_to_page(page: Page, page_number: int) -> None:
    """
    Recover from a failed pagination or record-crawl step by going back to the homepage, redoing the search, and re-navigating to page_number.
    """

    last_exc: Exception | None = None

    for attempt, delay in enumerate([0] + RECOVERY_RETRY_DELAYS):
        if delay:
            print(
                f"  recover_to_page: retrying in {delay}s "
                f"(attempt {attempt + 1}) after: {last_exc}"
            )
            await asyncio.sleep(delay)

        try:
            await page.context.clear_cookies()
            await page.goto(HOMEPAGE)
            await page.wait_for_load_state("domcontentloaded")
            await get_results(page)
            await set_page_size(page)

            if page_number != 1:
                await go_to_results_page(page, page_number)

            return
        except (PlaywrightError, RuntimeError) as exc:
            last_exc = exc

    raise RuntimeError(
        f"recover_to_page: could not recover to page {page_number} "
        f"after {len(RECOVERY_RETRY_DELAYS)} retries: {last_exc}"
    ) from last_exc


async def scrape(
    conn: sqlite3.Connection,
    output_dir: Path,
    max_records: int | None = None,
) -> int:
    """Handles initialization, traversal, extraction, looped across all pages until end of results hit or --max-records hit"""
    async with async_playwright() as playwright:
        browser = await playwright.chromium.launch(
            headless=True
        )

        context = await browser.new_context(
            user_agent=USER_AGENT,
            viewport={
                "width": 1280,
                "height": 720,
            },
        )

        context.set_default_timeout(60000)
        context.set_default_navigation_timeout(120000)

        await block_unnecessary_assets(context)

        page = await context.new_page()

        print("Opening registry...")
        await page.goto(HOMEPAGE)
        await page.wait_for_load_state("domcontentloaded")

        await get_results(
            page
        )

        total_records = await get_number_of_records(page)

        print(f"Found {total_records} records.")

        if total_records == 0:
            await browser.close()
            return 0

        await set_page_size(page)

        total_pages = (total_records + PAGE_SIZE - 1) // PAGE_SIZE

        scraped_count = 0
        browser_page = 1
        reached_limit = False

        for page_number in range(1, total_pages + 1):
            if reached_limit:
                break

            print(
                f"\nPage {page_number}/{total_pages}"
            )

            if browser_page != page_number:
                crossing_window = (
                    (page_number - 1) // PAGER_WINDOW_SIZE
                    != (browser_page - 1) // PAGER_WINDOW_SIZE
                )

                try:
                    if crossing_window:
                        await recover_to_page(page, page_number)
                    else:
                        await go_to_results_page(
                            page,
                            page_number,
                        )
                except Exception as exc:
                    print(
                        f"  ERROR navigating to page "
                        f"{page_number}: {exc}"
                    )
                    await recover_to_page(page, page_number)
                browser_page = page_number

            records_on_page = min(
                PAGE_SIZE,
                total_records - ((page_number - 1) * PAGE_SIZE),
            )

            for i in range(records_on_page):
                absolute_index = (
                    (page_number - 1) * PAGE_SIZE + i
                )

                print(
                    f"  Record "
                    f"{absolute_index + 1}/{total_records}"
                )

                try:
                    record = await crawl_record(
                        page,
                        absolute_index,
                        conn,
                    )

                    if record is not None:
                        record["scraped_at"] = datetime.now().isoformat()
                        write_record(output_dir, record)
                        mark_seen(conn, record["hash"])
                        scraped_count += 1

                        label = record["client_name"] or record["company_organization"]
                        print(
                            f"    -> scraped {record['registration_number']} "
                            f"({record['lobbyist_type']}): {label}"
                        )

                        if max_records is not None and scraped_count >= max_records:
                            print(
                                f"\nReached --max-records {max_records}; "
                                f"stopping."
                            )
                            reached_limit = True
                            break

                        await page.wait_for_timeout(REQUEST_DELAY_MS)


                        #pagination is server-side postback state, not part of the URL
                        # resets grid to page 1 rather than restoring the page we were on
                        browser_page = 1
                        await set_page_size(page)

                        if page_number != 1:
                            try:
                                await go_to_results_page(
                                    page,
                                    page_number,
                                )
                            except Exception as exc:
                                print(
                                    f"  ERROR navigating to page "
                                    f"{page_number}: {exc}"
                                )
                                await recover_to_page(page, page_number)
                            browser_page = page_number

                except Exception as exc:
                    print(
                        f"  ERROR on record "
                        f"{absolute_index + 1}: {exc}"
                    )
                    await recover_to_page(page, page_number)
                    browser_page = page_number

        await browser.close()

        return scraped_count


class Tee:
    """duplicates writes to log directory"""

    def __init__(self, *streams):
        self._streams = streams

    def write(self, data: str) -> None:
        for stream in self._streams:
            stream.write(data)

    def flush(self) -> None:
        for stream in self._streams:
            stream.flush()


def parse_args():
    parser = argparse.ArgumentParser(
        description="Scrape the Ontario Lobbyists Registry."
    )

    parser.add_argument(
        "--max-records",
        type=int,
        default=None,
        help=(
            "Stop after scraping this many new (uncached) records."
        ),
    )

    return parser.parse_args()


def main():
    args = parse_args()

    conn = init_cache(CACHE_PATH)
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)

    LOG_DIR.mkdir(parents=True, exist_ok=True)
    job_start = datetime.now()
    log_path = LOG_DIR / f"scrape-{job_start.strftime('%Y%m%d_%H%M%S')}.txt"

    original_stdout = sys.stdout
    original_stderr = sys.stderr
    log_file = log_path.open("w", encoding="utf-8")
    sys.stdout = Tee(original_stdout, log_file)
    sys.stderr = Tee(original_stderr, log_file)

    try:
        try:
            scraped_count = asyncio.run(
                scrape(conn, OUTPUT_DIR, max_records=args.max_records)
            )
        finally:
            conn.close()

        print(
            f"\nSaved {scraped_count} records to "
            f"{OUTPUT_DIR}"
        )
    finally:
        sys.stdout = original_stdout
        sys.stderr = original_stderr
        log_file.close()


if __name__ == "__main__":
    main()