# Eval results

**Accuracy: 10/11 cases passed (91%)**

Regenerate with `python eval/run_eval.py`.

**"Correct"** = every one of the schema's 8 top-level fields (`registration_number`, `lobbyist_type`, `initial_filing_date`, `last_filed_date`, `organization`, `lobbyists`, `subject_matters`, `lobbying_activities`, `government_funding`) exactly matches `eval/expected/*.json` after the same normalization the pipeline itself applies before writing to SQLite (i.e. what would actually ship, not the raw parse-stage intermediate).

| Case | Result |
| --- | --- |
| `CL0048-19990121204033__e69046a185805bcd88276f2baa776dde.json` | PASS |
| `CL0061-20220728028524__b178ce104f2bfbaf3125c9fee1c232fb.json` | PASS |
| `CL0083-20151223016768__7a036c822cf2252ef79941058d646c5d.json` | PASS |
| `CL0083-20260811037391__890edb2c3494e3b27d9ff59132b2270d.json` | PASS |
| `CL10021-20220422028232__a50c26346c8ed7a5cb05ad0269626618.json` | FAIL |
| `CL10825-20250904035015__039b141744fd01dcd2ca07086a7de01e.json` | PASS |
| `OL0020-19990727163001__d8531e04af1d0a569454075d2f886bee.json` | PASS |
| `OL0034-19990217115459__bd7b8fa9dccae153bf62f0707e603655.json` | PASS |
| `PP0729-20160811017743__4994764cd06c72d94cb0b3b4b604c97b.json` | PASS |
| `PP1526-20160808017711__708099a2d1a10449706621feec170f92.json` | PASS |
| `PP1793-20160728017681__8a762292f41170f6620630d4ed9f920c.json` | PASS |

## Failure detail

### `CL10021-20220422028232__a50c26346c8ed7a5cb05ad0269626618.json`
- subject_matters: got ['-'], expected []

