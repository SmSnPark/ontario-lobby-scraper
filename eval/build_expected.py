"""
Deliberately does NOT import src/parse.py or src/clean.py

eval/expected/*.json is already committed.
"""
import json
import re
from pathlib import Path

from lxml import etree

CASES_DIR = Path(__file__).resolve().parent / "cases"
EXPECTED_DIR = Path(__file__).resolve().parent / "expected"

WHITESPACE_PATTERN = re.compile(r"\s+")
SPACE_BEFORE_PUNCT = re.compile(r"\s+([,;:])")


def normalize_text(s: str) -> str:
    s = WHITESPACE_PATTERN.sub(" ", s)
    s = SPACE_BEFORE_PUNCT.sub(r"\1", s)
    return s.strip()


def txt(el):
    if el is None:
        return None
    return normalize_text("".join(el.itertext()))


def first_text(tree, xpath):
    els = tree.xpath(xpath)
    return txt(els[0]) if els else None


def first_attr(tree, xpath):
    vals = tree.xpath(xpath)
    return normalize_text(vals[0]) if vals else None


TARGET_GROUPS = ("MinistersOfficesItems", "MinistriesItems", "MPPItems", "AgenciesItems")


def build(raw: dict) -> dict:
    tree = etree.HTML(raw["html"])
    is_org = raw["lobbyist_type"] != "Consultant Lobbyist"

    if is_org:
        org_name = first_text(tree, "//span[contains(@id,'FirmNameValue')]")
        addr_parts = [
            first_text(tree, "//span[contains(@id,'Street2Value')]"),
            first_text(tree, "//span[contains(@id,'City2Value')]"),
            first_text(tree, "//span[contains(@id,'State2Value')]"),
            first_text(tree, "//span[contains(@id,'Zip2Value')]"),
            first_text(tree, "//span[contains(@id,'Country2Value')]"),
        ]
        org_description = first_text(
            tree, "//span[contains(@id,'OrganizationBusinessPreview')]"
        ) or ""
    else:
        org_name = first_attr(tree, "//input[contains(@id,'ClientName')]/@value")
        addr_parts = [
            first_attr(tree, "//input[contains(@id,'Street')]/@value"),
            first_attr(tree, "//input[contains(@id,'City')]/@value"),
            first_attr(tree, "//input[contains(@id,'txtState')]/@value"),
            first_attr(tree, "//input[contains(@id,'Zip')]/@value"),
            first_attr(tree, "//input[contains(@id,'Country')]/@value"),
        ]
        org_description = first_text(
            tree, "//span[contains(@id,'ClientBusinessContent')]"
        ) or ""

    org_address = normalize_text(", ".join(p for p in addr_parts if p))

    if is_org:
        lobbyists = []
        for row in tree.xpath("//table[contains(@id,'EmployeeNameAndTitle')]/tbody/tr"):
            cells = row.xpath("./td")
            if len(cells) == 1:
                break
            lobbyists.append(
                {"name": txt(cells[0]), "position": txt(cells[1]) or None}
            )
    else:
        first = first_text(tree, "//span[contains(@id,'FirstNameValue')]") or ""
        last = first_text(tree, "//span[contains(@id,'LastNameValue')]") or ""
        lobbyists = [{"name": normalize_text(f"{first} {last}"), "position": None}]

    subject_matters = []
    for el in tree.xpath(
        "//span[contains(@id,'lblSubjectMatter') and not(contains(@id,'PageTitle')) and not(contains(@id,'Other'))]"
    ):
        for node in el.xpath("./text()"):
            for part in str(node).split(";"):
                part = normalize_text(part)
                if part and part != "-":
                    subject_matters.append(part)

    lobbying_activities = []
    for answer in tree.xpath("//b/span[contains(@id,'Answer')]"):
        if txt(answer) != "Yes":
            continue
        answer_id = answer.get("id")
        key_match = re.search(r"lbl(\w+)Answer$", answer_id)
        if not key_match:
            continue
        key = key_match.group(1)
        cat_el = answer.xpath("./ancestor::b/preceding-sibling::span")
        category = txt(cat_el[0]) if cat_el else None
        goal_els = tree.xpath(f"//span[contains(@id,'uc{key}_txtGoalDescription')]")
        goal = txt(goal_els[0]) if goal_els else ""
        group_filter = " or ".join(f"contains(@id,'{g}')" for g in TARGET_GROUPS)
        cells = tree.xpath(f"//td[contains(@id,'uc{key}_cell') and ({group_filter})]")
        targets = []
        for cell in cells:
            for node in cell.xpath("./text()"):
                t = normalize_text(str(node))
                if t and t != "-" and t not in targets:
                    targets.append(t)
        lobbying_activities.append({"category": category, "goal": goal, "targets": targets})

    government_funding = []
    for row in tree.xpath("//table[contains(@id,'GridFundingDetails')]/tbody/tr"):
        cells = row.xpath("./td")
        if len(cells) == 1:
            continue
        source = txt(cells[0])
        amount_raw = txt(cells[1])
        amount = float(amount_raw.replace("$", "").replace(",", "").strip())
        government_funding.append({"source": source, "amount": amount})

    initial_filing_date = first_text(tree, "//span[contains(@id,'InitialFilingDateValue')]")

    return {
        "registration_number": raw["registration_number"],
        "lobbyist_type": raw["lobbyist_type"],
        "status": {
            "initial_filing_date": initial_filing_date,
            "last_filed_date": raw["filed_on"],
        },
        "organization": {
            "name": org_name,
            "address": org_address,
            "description": org_description,
        },
        "lobbyists": lobbyists,
        "subject_matters": subject_matters,
        "lobbying_activities": lobbying_activities,
        "government_funding": government_funding,
    }


if __name__ == "__main__":
    EXPECTED_DIR.mkdir(parents=True, exist_ok=True)
    for path in sorted(CASES_DIR.glob("*.json")):
        raw = json.loads(path.read_text(encoding="utf-8"))
        expected = build(raw)
        out_path = EXPECTED_DIR / path.name
        out_path.write_text(
            json.dumps(expected, indent=2, ensure_ascii=False),
            encoding="utf-8",
        )
        print("wrote", out_path)
