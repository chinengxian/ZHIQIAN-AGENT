from __future__ import annotations

import json
import math
from pathlib import Path
from typing import Any

WIKI_EVAL_THRESHOLDS: dict[str, dict[str, float | str]] = {
    "page_recall_at_5": {"operator": ">=", "value": 0.90},
    "claim_recall_at_8": {"operator": ">=", "value": 0.85},
    "citation_precision": {"operator": ">=", "value": 0.95},
    "synonym_result_consistency": {"operator": ">=", "value": 0.85},
    "no_answer_false_match_rate": {"operator": "<=", "value": 0.05},
    "needs_review_leaks": {"operator": "==", "value": 0},
    "invalid_source_leaks": {"operator": "==", "value": 0},
    "old_page_version_leaks": {"operator": "==", "value": 0},
}


def _passes_threshold(actual: float | int, operator: str, threshold: float) -> bool:
    if operator == ">=":
        return actual >= threshold or math.isclose(actual, threshold, abs_tol=1e-12)
    if operator == "<=":
        return actual <= threshold or math.isclose(actual, threshold, abs_tol=1e-12)
    if operator == "==":
        return actual == threshold
    raise ValueError(f"不支持的 Wiki 评测比较符：{operator}")


def _build_checks(metrics: dict[str, float | int | str]) -> list[dict[str, Any]]:
    checks: list[dict[str, Any]] = []
    for metric, rule in WIKI_EVAL_THRESHOLDS.items():
        actual = metrics.get(metric)
        operator = str(rule["operator"])
        threshold = float(rule["value"])
        passed = isinstance(actual, (int, float)) and _passes_threshold(
            actual, operator, threshold
        )
        checks.append(
            {
                "metric": metric,
                "operator": operator,
                "threshold": rule["value"],
                "actual": actual,
                "passed": passed,
            }
        )
    return checks


def _build_report(
    metrics: dict[str, float | int | str],
    case_diagnostics: list[dict[str, Any]],
) -> dict[str, Any]:
    checks = _build_checks(metrics)
    failures = [
        {key: value for key, value in check.items() if key != "passed"}
        for check in checks
        if not check["passed"]
    ]
    diagnostic_queries = sum(
        len(case.get("queries", [])) for case in case_diagnostics
    )
    return {
        "schema_version": "wiki-eval-report-v1",
        "status": "failed" if failures else "passed",
        "metrics": metrics,
        "thresholds": WIKI_EVAL_THRESHOLDS,
        "checks": checks,
        "failures": failures,
        "summary": {
            "total_checks": len(checks),
            "passed_checks": len(checks) - len(failures),
            "failed_checks": len(failures),
            "diagnostic_cases": len(case_diagnostics),
            "diagnostic_queries": diagnostic_queries,
        },
        "case_diagnostics": case_diagnostics,
    }


def _write_report(report: dict[str, Any], report_path: Path) -> None:
    """原子写入报告，避免评测中断时留下半份 JSON。"""

    report_path.parent.mkdir(parents=True, exist_ok=True)
    temporary_path = report_path.with_suffix(f"{report_path.suffix}.tmp")
    temporary_path.write_text(
        json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    temporary_path.replace(report_path)


def finalize_wiki_eval_report(
    *,
    metrics: dict[str, float | int | str],
    case_diagnostics: list[dict[str, Any]],
    report_path: Path,
) -> dict[str, Any]:
    """生成并保存报告；质量门失败时保留证据后再终止评测。"""

    report = _build_report(metrics, case_diagnostics)
    _write_report(report, report_path)
    failures = report["failures"]
    if failures:
        details = ", ".join(
            f"{failure['metric']}={failure['actual']} "
            f"{failure['operator']} {failure['threshold']}"
            for failure in failures
        )
        raise AssertionError(f"Wiki 质量门未通过：{details}；报告：{report_path}")
    return report
