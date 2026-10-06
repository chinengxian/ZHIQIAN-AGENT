from __future__ import annotations

import json
from pathlib import Path


CASES_PATH = Path(__file__).with_name("wiki_cases.json")


def test_wiki_eval_cases_cover_all_approved_questions() -> None:
    payload = json.loads(CASES_PATH.read_text(encoding="utf-8"))
    cases = payload["cases"]

    assert [case["id"] for case in cases] == [f"Q{index:02d}" for index in range(1, 17)]
    assert all(len(case["queries"]) >= 3 for case in cases)
    assert all(
        {"expected_documents", "expected_terms", "forbidden_terms", "answerable"}
        <= case.keys()
        for case in cases
    )
    assert cases[-1]["answerable"] is False
    assert cases[-1]["expected_documents"] == []

