from pathlib import Path

WORKFLOW_PATH = Path(__file__).parents[1] / ".github" / "workflows" / "wiki-quality.yml"


def test_wiki_quality_workflow_separates_automatic_and_full_gates() -> None:
    workflow = WORKFLOW_PATH.read_text(encoding="utf-8")

    assert "pull_request:" in workflow
    assert "push:" in workflow
    assert "schedule:" in workflow
    assert 'cron: "30 18 * * *"' in workflow
    assert "workflow_dispatch:" in workflow
    assert "uv run wiki-quality light" in workflow
    assert (
        "if: github.event_name == 'workflow_dispatch' || github.event_name == 'schedule'"
        in workflow
    )
    assert "uv run wiki-quality full" in workflow
    assert workflow.count("if: always()") >= 2
    assert "artifacts/wiki-eval-report.json" in workflow
    assert "retention-days: 14" in workflow
    assert "docker compose down" in workflow
