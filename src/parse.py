import json
import re
import sys
from pathlib import Path

from marker import Marker
from models import (
    GovernmentFunding,
    Lobbyist,
    LobbyingActivity,
    Organization,
    ParsedRegistration,
    RegistrationStatus,
)

INPUT_DIR = Path("data/scrape")
OUTPUT_DIR = Path("data/parsed")


def span_id(root: Marker, id_str: str) -> str:
    """Extract text from any given span"""
    return root.txt(f"//span[contains(@id, '{id_str}')]")


def inp_id(root: Marker, id_str: str) -> str:
    '''Extravt id of HTML input field'''
    inp = root.first(f"//input[contains(@id, '{id_str}')]")
    if inp.etree.xpath("./@value"):
        return inp.attr("value")
    else:
        return ""


def parse_organization(root: Marker, is_org: bool) -> Organization:
    """Parse all organizations related to Lobbyist, and their info (addr and contact)"""
    if is_org:
        name = span_id(root, "FirmNameValue")
        address = ", ".join(
            part
            for part in [
                span_id(root, "Street2Value"),
                span_id(root, "City2Value"),
                span_id(root, "State2Value"),
                span_id(root, "Zip2Value"),
                span_id(root, "Country2Value"),
            ]
            if part
        )
        #PP registrations have neither span at all. description will legitimately come back empty for that variant.
        description = span_id(root, "OrganizationBusinessPreview")
    else:
        name = inp_id(root, "ClientName")
        address = ", ".join(
            part
            for part in [
                inp_id(root, "Street"),
                inp_id(root, "City"),
                inp_id(root, "txtState"),
                inp_id(root, "Zip"),
                inp_id(root, "Country"),
            ]
            if part
        )
        description = span_id(root, "ClientBusinessContent")
    return Organization(name=name, address=address, description=description)


def parse_lobbyists(root: Marker, is_org: bool) -> list[Lobbyist]:
    """Parse all lobbyists related to filing, and their info (addr and contact)"""
    if not is_org:
        name = " ".join(
            [span_id(root, "FirstNameValue"), span_id(root, "LastNameValue")]
        )
        return [Lobbyist(name=name)]

    lobbyists = []
    for row in root.all("//table[contains(@id, 'EmployeeNameAndTitle')]/tbody/tr"):
        cells = row.all("./td")
        if len(cells) == 1:
            #"There are no records available" placeholder row.
            break
        lobbyists.append(Lobbyist(name=cells[0].txt(), position=cells[1].txt()))
    return lobbyists


def parse_subject_matters(root: Marker) -> list[str]:
    '''Extract all subject matters inputted byb lobbyist'''
    matches = root.all(
        "//span[contains(@id, 'lblSubjectMatter')"
        " and not(contains(@id, 'PageTitle'))"
        " and not(contains(@id, 'Other'))]"
    )
    if not matches:
        return []
    values = matches[0].etree.xpath("./text()")
    subjects = []
    for value in values:
        for part in value.split(";"):
            part = part.strip()
            if part:
                subjects.append(part)
    return subjects


CATEGORY_KEY_PATTERN = re.compile(r"lbl(\w+)Answer$")

TARGET_GROUPS = (
    "MinistersOfficesItems",
    "MinistriesItems",
    "MPPItems",
    "AgenciesItems",
)


def parse_lobbying_activities(root: Marker) -> list[LobbyingActivity]:
    '''Parse out all lobbying activities and subheaders inside HTML'''
    activities = []
    for answer in root.all("//b/span[contains(@id, 'Answer')]"):
        if answer.txt() != "Yes":
            continue

        answer_id = answer.attr("id")
        key_match = CATEGORY_KEY_PATTERN.search(answer_id)
        if not key_match:
            continue
        key = key_match.group(1)

        category = answer.first("./ancestor::b/preceding-sibling::span").txt()

        goal_matches = root.all(f"//span[contains(@id, 'uc{key}_txtGoalDescription')]")
        goal = goal_matches[0].txt() if goal_matches else ""

        group_filter = " or ".join(
            f"contains(@id,'{group}')" for group in TARGET_GROUPS
        )
        target_cells = root.all(
            f"//td[contains(@id, 'uc{key}_cell') and ({group_filter})]"
        )
        targets = []
        for cell in target_cells:
            for text in cell.etree.xpath("./text()"):
                text = text.strip()
                # "-" is the site's literal placeholder for an empty target-group cell, not real data.
                if text and text != "-" and text not in targets:
                    targets.append(text)

        activities.append(
            LobbyingActivity(category=category, goal=goal, targets=targets)
        )
    return activities


def parse_government_funding(root: Marker) -> list[GovernmentFunding]:
    '''Fetch government funding header table from HTML'''
    funding = []
    for row in root.all("//table[contains(@id, 'GridFundingDetails')]/tbody/tr"):
        cells = row.all("./td")
        if len(cells) == 1:
            #"There are no records available" placeholder row.
            break
        funding.append(GovernmentFunding(source=cells[0].txt(), amount=cells[1].txt()))
    return funding


def parse_record(raw: dict) -> ParsedRegistration:
    '''Compile parsed registration for cleaning'''
    root = Marker(raw["html"])
    is_org = raw["lobbyist_type"] != "Consultant Lobbyist"

    return ParsedRegistration(
        registration_number=raw["registration_number"],
        lobbyist_type=raw["lobbyist_type"],
        status=RegistrationStatus(
            initial_filing_date=span_id(root, "InitialFilingDateValue"),
            last_filed_date=raw["filed_on"],
        ),
        organization=parse_organization(root, is_org),
        lobbyists=parse_lobbyists(root, is_org),
        subject_matters=parse_subject_matters(root),
        lobbying_activities=parse_lobbying_activities(root),
        government_funding=parse_government_funding(root),
    )


def parse_directory(input_dir: Path, output_dir: Path) -> tuple[int, int]:
    """Parse every scraped record JSON in input_dir, writing validated output to output_dir. """

    output_dir.mkdir(parents=True, exist_ok=True)

    succeeded = 0
    failed = 0

    for path in sorted(input_dir.glob("*.json")):
        raw = json.loads(path.read_text(encoding="utf-8"))

        try:
            record = parse_record(raw)
        except Exception as exc:
            print(f"FAILED  {path.name}: {exc}", file=sys.stderr)
            failed += 1
            continue

        output_path = output_dir / path.name
        output_path.write_text(
            json.dumps(record.model_dump(mode="json"), indent=2, ensure_ascii=False),
            encoding="utf-8",
        )
        succeeded += 1
        print(f"  parsed {record.registration_number} ({record.lobbyist_type})")

    return succeeded, failed


def main():
    succeeded, failed = parse_directory(INPUT_DIR, OUTPUT_DIR)

    print(f"\nParsed {succeeded} records ({failed} failed) to {OUTPUT_DIR}")

    if failed:
        sys.exit(1)


if __name__ == "__main__":
    main()
