from __future__ import annotations

import argparse
import os
import shlex
import subprocess
import sys
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import urlsplit


class WikiQualityConfigurationError(ValueError):
    """Wiki 质量门配置不安全或不完整。"""


@dataclass(frozen=True)
class WikiQualityStep:
    """单个质量检查步骤及其 Python 模块参数。"""

    name: str
    command: tuple[str, ...]


@dataclass(frozen=True)
class WikiQualityPlan:
    """可重复执行的 Wiki 质量检查计划。"""

    mode: str
    steps: tuple[WikiQualityStep, ...]
    environment: dict[str, str]


WikiQualityRunner = Callable[[tuple[str, ...], dict[str, str]], int]

_LINT_FILES = (
    "src/agent_api/quality/wiki.py",
    "tests/eval/wiki_eval_report.py",
    "tests/eval/test_wiki_eval_report.py",
    "tests/eval/test_wiki_retrieval_quality.py",
    "tests/test_wiki_quality_command.py",
    "tests/test_wiki_quality_workflow.py",
)
_LIGHT_TESTS = (
    "tests/test_wiki_quality_command.py",
    "tests/test_wiki_quality_workflow.py",
    "tests/eval/test_wiki_eval_report.py",
    "tests/eval/test_wiki_retrieval_quality.py",
    "tests/test_wiki_independent_retrieval.py",
    "tests/test_wiki_index_jobs.py",
)


def _light_steps() -> tuple[WikiQualityStep, ...]:
    return (
        WikiQualityStep("静态检查", ("-m", "ruff", "check", *_LINT_FILES)),
        WikiQualityStep(
            "轻量回归",
            (
                "-m",
                "pytest",
                *_LIGHT_TESTS,
                "-m",
                "not knowledge_integration",
                "-q",
            ),
        ),
    )


def _validate_test_database(database_url: str | None) -> str:
    if not database_url:
        raise WikiQualityConfigurationError(
            "完整评测需要设置 AGENT_WIKI_TEST_DATABASE_URL"
        )
    database_name = Path(urlsplit(database_url).path).name
    if not database_name.endswith("_test"):
        raise WikiQualityConfigurationError(
            "AGENT_WIKI_TEST_DATABASE_URL 必须指向名称以 _test 结尾的独立测试数据库"
        )
    return database_url


def build_quality_plan(
    mode: str,
    *,
    environment: Mapping[str, str] | None = None,
    report_path: Path = Path("artifacts/wiki-eval-report.json"),
) -> WikiQualityPlan:
    """构建轻量或完整 Wiki 质量门，完整模式强制使用独立测试库。"""

    if mode not in {"light", "full"}:
        raise WikiQualityConfigurationError(f"不支持的 Wiki 质量门模式：{mode}")
    plan_environment = dict(os.environ if environment is None else environment)
    plan_environment["RUN_KNOWLEDGE_INTEGRATION"] = "0"
    steps = _light_steps()
    if mode == "full":
        database_url = _validate_test_database(
            plan_environment.get("AGENT_WIKI_TEST_DATABASE_URL")
        )
        plan_environment.update(
            {
                "RUN_KNOWLEDGE_INTEGRATION": "1",
                "AGENT_DATABASE_URL": database_url,
                "WIKI_EVAL_REPORT_PATH": str(report_path),
            }
        )
        steps += (
            WikiQualityStep(
                "完整评测",
                (
                    "-m",
                    "pytest",
                    "tests/eval/test_wiki_retrieval_quality.py",
                    "-q",
                    "-s",
                ),
            ),
        )
    return WikiQualityPlan(mode=mode, steps=steps, environment=plan_environment)


def _repository_root() -> Path:
    return Path(__file__).resolve().parents[3]


def _subprocess_runner(command: tuple[str, ...], environment: dict[str, str]) -> int:
    completed = subprocess.run(
        (sys.executable, *command),
        cwd=_repository_root(),
        env=environment,
        check=False,
    )
    return completed.returncode


def run_quality_plan(
    plan: WikiQualityPlan,
    *,
    runner: WikiQualityRunner = _subprocess_runner,
) -> int:
    """顺序执行质量门，在首个失败步骤停止并透传退出码。"""

    for step in plan.steps:
        rendered = shlex.join((sys.executable, *step.command))
        print(f"[Wiki 质量门] {step.name}：{rendered}", flush=True)
        exit_code = runner(step.command, plan.environment)
        if exit_code != 0:
            print(
                f"[Wiki 质量门] {step.name}失败，退出码：{exit_code}",
                file=sys.stderr,
                flush=True,
            )
            return exit_code
    print(f"[Wiki 质量门] {plan.mode} 模式通过", flush=True)
    return 0


def _parse_args(arguments: Sequence[str] | None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="运行 Wiki 分层质量门")
    parser.add_argument(
        "mode",
        nargs="?",
        choices=("light", "full"),
        default="light",
        help="light 运行轻量检查；full 追加真实 PostgreSQL/Milvus 评测",
    )
    parser.add_argument(
        "--report-path",
        type=Path,
        default=Path("artifacts/wiki-eval-report.json"),
        help="完整评测报告输出路径",
    )
    return parser.parse_args(arguments)


def main(arguments: Sequence[str] | None = None) -> int:
    args = _parse_args(arguments)
    try:
        plan = build_quality_plan(args.mode, report_path=args.report_path)
    except WikiQualityConfigurationError as error:
        print(f"[Wiki 质量门] 配置错误：{error}", file=sys.stderr)
        return 2
    try:
        return run_quality_plan(plan)
    except KeyboardInterrupt:
        print("[Wiki 质量门] 已中断", file=sys.stderr)
        return 130


if __name__ == "__main__":
    raise SystemExit(main())
