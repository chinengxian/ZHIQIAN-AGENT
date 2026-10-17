from __future__ import annotations

from pathlib import Path

import pytest

from agent_api.quality.wiki import (
    WikiQualityConfigurationError,
    build_quality_plan,
    run_quality_plan,
)


def test_light_plan_runs_linter_before_targeted_tests() -> None:
    plan = build_quality_plan("light", environment={})

    assert [step.name for step in plan.steps] == ["静态检查", "轻量回归"]
    assert plan.steps[0].command[:3] == ("-m", "ruff", "check")
    lint_targets = set(plan.steps[0].command[3:])
    assert "src/agent_api/knowledge/wiki" in lint_targets
    assert "src/agent_api/knowledge/infrastructure/milvus/wiki_index.py" in lint_targets
    assert "src/agent_api/knowledge/infrastructure/observability/events.py" in lint_targets
    assert "src/agent_api/core/config.py" in lint_targets
    assert "src/agent_api/core/lifespan.py" in lint_targets
    assert "tests/eval/test_wiki_eval_report.py" in plan.steps[1].command
    assert "tests/test_wiki_independent_retrieval.py" in plan.steps[1].command
    assert plan.steps[1].command[-3:] == ("-m", "not knowledge_integration", "-q")
    assert plan.environment["RUN_KNOWLEDGE_INTEGRATION"] == "0"


def test_full_plan_requires_an_isolated_test_database() -> None:
    with pytest.raises(WikiQualityConfigurationError, match="AGENT_WIKI_TEST_DATABASE_URL"):
        build_quality_plan("full", environment={})

    with pytest.raises(WikiQualityConfigurationError, match="_test"):
        build_quality_plan(
            "full",
            environment={
                "AGENT_WIKI_TEST_DATABASE_URL": (
                    "postgresql+asyncpg://agent:agent@127.0.0.1:5432/agent"
                )
            },
        )


def test_full_plan_enables_integration_and_keeps_report_path(tmp_path: Path) -> None:
    database_url = "postgresql+asyncpg://agent:agent@127.0.0.1:5432/agent_wiki_test"
    report_path = tmp_path / "wiki-report.json"

    plan = build_quality_plan(
        "full",
        environment={"AGENT_WIKI_TEST_DATABASE_URL": database_url},
        report_path=report_path,
    )

    assert [step.name for step in plan.steps] == ["静态检查", "轻量回归", "完整评测"]
    assert plan.environment["RUN_KNOWLEDGE_INTEGRATION"] == "1"
    assert plan.environment["AGENT_DATABASE_URL"] == database_url
    assert plan.environment["WIKI_EVAL_REPORT_PATH"] == str(report_path)
    assert plan.steps[-1].command == (
        "-m",
        "pytest",
        "tests/eval/test_wiki_retrieval_quality.py",
        "-q",
        "-s",
    )


def test_run_quality_plan_stops_after_first_failed_step() -> None:
    plan = build_quality_plan("light", environment={})
    executed: list[tuple[str, ...]] = []

    def fake_runner(command: tuple[str, ...], environment: dict[str, str]) -> int:
        del environment
        executed.append(command)
        return 7

    exit_code = run_quality_plan(plan, runner=fake_runner)

    assert exit_code == 7
    assert executed == [plan.steps[0].command]
