from pathlib import Path

WORKFLOW_PATH = Path(__file__).parents[1] / ".github" / "workflows" / "wiki-quality.yml"


def test_wiki_quality_workflow_separates_automatic_and_manual_gates() -> None:
    workflow = WORKFLOW_PATH.read_text(encoding="utf-8")

    assert "pull_request:" in workflow
    assert "workflow_dispatch:" in workflow
    assert "uv run wiki-quality light" in workflow
    assert "if: github.event_name == 'workflow_dispatch'" in workflow
    assert "uv run wiki-quality full" in workflow
    assert "if: always()" in workflow
    assert "artifacts/wiki-eval-report.json" in workflow
