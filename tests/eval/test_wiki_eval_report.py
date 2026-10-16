from __future__ import annotations

import json
from pathlib import Path

import pytest

from tests.eval.wiki_eval_report import (
    WIKI_EVAL_THRESHOLDS,
    finalize_wiki_eval_report,
)


def _passing_metrics() -> dict[str, float | int | str]:
    return {
        "page_recall_at_5": 0.90,
        "claim_recall_at_8": 0.85,
        "citation_precision": 0.95,
        "synonym_result_consistency": 0.85,
        "no_answer_false_match_rate": 0.05,
        "needs_review_leaks": 0,
        "invalid_source_leaks": 0,
        "old_page_version_leaks": 0,
        "collection": "wiki_eval_units_test",
        "embedding_fingerprint": "test-fingerprint",
        "generator": "deterministic_grounded",
    }


def test_finalize_wiki_eval_report_writes_stable_passed_report(tmp_path: Path) -> None:
    report_path = tmp_path / "nested" / "wiki-eval-report.json"

    report = finalize_wiki_eval_report(
        metrics=_passing_metrics(),
        case_diagnostics=[],
        report_path=report_path,
    )

    persisted = json.loads(report_path.read_text(encoding="utf-8"))
    assert report == persisted
    assert persisted["schema_version"] == "wiki-eval-report-v1"
    assert persisted["status"] == "passed"
    assert persisted["thresholds"] == WIKI_EVAL_THRESHOLDS
    assert persisted["summary"] == {
        "total_checks": 8,
        "passed_checks": 8,
        "failed_checks": 0,
        "diagnostic_cases": 0,
        "diagnostic_queries": 0,
    }
    assert persisted["failures"] == []


def test_finalize_wiki_eval_report_persists_failure_evidence_before_raising(
    tmp_path: Path,
) -> None:
    report_path = tmp_path / "wiki-eval-report.json"
    metrics = _passing_metrics()
    metrics["page_recall_at_5"] = 0.50
    diagnostics = [
        {
            "case_id": "Q01",
            "answerable": True,
            "expected_documents": ["02-identity-access-control.md"],
            "expected_terms": ["MFA"],
            "queries": [
                {
                    "query": "如何启用双因素认证？",
                    "missing_documents": ["02-identity-access-control.md"],
                    "missing_terms": ["MFA"],
                    "irrelevant_citation_documents": ["03-remote-access-vpn-guide.md"],
                }
            ],
        }
    ]

    with pytest.raises(AssertionError, match="page_recall_at_5"):
        finalize_wiki_eval_report(
            metrics=metrics,
            case_diagnostics=diagnostics,
            report_path=report_path,
        )

    persisted = json.loads(report_path.read_text(encoding="utf-8"))
    assert persisted["status"] == "failed"
    assert persisted["failures"] == [
        {
            "metric": "page_recall_at_5",
            "operator": ">=",
            "threshold": 0.9,
            "actual": 0.5,
        }
    ]
    assert persisted["summary"]["failed_checks"] == 1
    assert persisted["summary"]["diagnostic_queries"] == 1
    assert persisted["case_diagnostics"] == diagnostics
