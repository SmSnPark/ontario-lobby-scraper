"""
"Correct" = every one of the 8 ParsedRegistration fields
    - registration_number,
    - lobbyist_type, 
    - status.initial_filing_date, 
    - status.last_filed_date,
    - organization, 
    - lobbyists,
    - subject_matters, 
    - lobbying_activities,
    - government_funding
exactly matches the expected value, after the same normalize_text cleanup the real pipeline applies before writing to SQLite.

Usage: python eval/run_eval.py
"""
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
CASES_DIR = Path(__file__).resolve().parent / "cases"
EXPECTED_DIR = Path(__file__).resolve().parent / "expected"
RESULTS_PATH = Path(__file__).resolve().parent / "results.md"

sys.path.insert(0, str(ROOT / "src"))
import clean as clean_main
import parse as parse_main


def run_pipeline(raw: dict) -> dict:
    """Raw scraped JSON -> parse_record() -> clean's own normalization -> the same field shape that ends up in SQLite."""
    record = parse_main.parse_record(raw)
    return {
        "registration_number": record.registration_number,
        "lobbyist_type": record.lobbyist_type,
        "status": {
            "initial_filing_date": record.status.initial_filing_date.isoformat(),
            "last_filed_date": record.status.last_filed_date.isoformat(),
        },
        "organization": {
            "name": clean_main.normalize_text(record.organization.name),
            "address": clean_main.normalize_text(record.organization.address),
            "description": clean_main.normalize_text(record.organization.description),
        },
        "lobbyists": clean_main.clean_lobbyists(record),
        "subject_matters": clean_main.clean_subject_matters(record),
        "lobbying_activities": clean_main.clean_lobbying_activities(record),
        "government_funding": clean_main.clean_government_funding(record),
    }


def diff_field(name: str, actual, expected) -> list[str]:
    """Return a list of human-readable mismatch descriptions for one field (empty if it matches)."""
    if name == "subject_matters":
        if set(actual) != set(expected):
            return [
                f"subject_matters: got {actual!r}, expected {expected!r}"
            ]
        return []

    if name == "lobbying_activities":
        if len(actual) != len(expected):
            return [
                f"lobbying_activities: {len(actual)} categories, expected {len(expected)}"
            ]
        problems = []
        for i, (a, e) in enumerate(zip(actual, expected)):
            if a["category"] != e["category"]:
                problems.append(
                    f"lobbying_activities[{i}].category: got {a['category']!r}, expected {e['category']!r}"
                )
            if a["goal"] != e["goal"]:
                problems.append(f"lobbying_activities[{i}].goal mismatch")
            if set(a["targets"]) != set(e["targets"]):
                missing = set(e["targets"]) - set(a["targets"])
                extra = set(a["targets"]) - set(e["targets"])
                problems.append(
                    f"lobbying_activities[{i}].targets: missing={missing!r} extra={extra!r}"
                )
        return problems

    if actual != expected:
        return [f"{name}: got {actual!r}, expected {expected!r}"]
    return []


FIELDS = [
    "registration_number",
    "lobbyist_type",
    "status",
    "organization",
    "lobbyists",
    "subject_matters",
    "lobbying_activities",
    "government_funding",
]


def run() -> tuple[list[dict], int, int]:
    results = []
    passed = 0
    failed = 0

    for path in sorted(EXPECTED_DIR.glob("*.json")):
        case_path = CASES_DIR / path.name
        raw = json.loads(case_path.read_text(encoding="utf-8"))
        expected = json.loads(path.read_text(encoding="utf-8"))

        try:
            actual = run_pipeline(raw)
        except Exception as exc:
            results.append(
                {"case": path.name, "status": "FAIL", "problems": [f"pipeline raised: {exc}"]}
            )
            failed += 1
            continue

        problems = []
        for field in FIELDS:
            problems.extend(diff_field(field, actual[field], expected[field]))

        if problems:
            failed += 1
            results.append({"case": path.name, "status": "FAIL", "problems": problems})
        else:
            passed += 1
            results.append({"case": path.name, "status": "PASS", "problems": []})

    return results, passed, failed


def write_results_md(results: list[dict], passed: int, failed: int) -> None:
    total = passed + failed
    accuracy = passed / total if total else 0.0

    lines = [
        "# Eval results",
        "",
        f"**Accuracy: {passed}/{total} cases passed ({accuracy:.0%})**",
        "",
        "Regenerate with `python eval/run_eval.py`.",
        "",
        "**\"Correct\"** = every one of the schema's 8 top-level fields "
        "(`registration_number`, `lobbyist_type`, `initial_filing_date`, "
        "`last_filed_date`, `organization`, `lobbyists`, `subject_matters`, "
        "`lobbying_activities`, `government_funding`) exactly matches "
        "`eval/expected/*.json` after the same normalization the pipeline "
        "itself applies before writing to SQLite (i.e. what would actually "
        "ship, not the raw parse-stage intermediate).",
        "",
        "| Case | Result |",
        "| --- | --- |",
    ]
    for r in results:
        lines.append(f"| `{r['case']}` | {r['status']} |")

    lines.append("")
    lines.append("## Failure detail")
    lines.append("")
    any_fail = False
    for r in results:
        if r["status"] == "FAIL":
            any_fail = True
            lines.append(f"### `{r['case']}`")
            for p in r["problems"]:
                lines.append(f"- {p}")
            lines.append("")
    if not any_fail:
        lines.append("(none)")

    RESULTS_PATH.write_text("\n".join(lines) + "\n", encoding="utf-8")


def main():
    results, passed, failed = run()
    total = passed + failed

    for r in results:
        print(f"{r['status']:5} {r['case']}")
        for p in r["problems"]:
            print(f"        {p}")

    print(f"\n{passed}/{total} cases passed ({passed / total:.0%})" if total else "no cases found")

    write_results_md(results, passed, failed)
    print(f"Wrote {RESULTS_PATH}")


if __name__ == "__main__":
    main()
