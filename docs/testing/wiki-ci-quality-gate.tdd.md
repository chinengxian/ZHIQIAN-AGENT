# Wiki CI 分层质量门 TDD 证据

- 日期：2026-10-18
- 来源规格：`docs/superpowers/specs/2026-10-18-wiki-ci-quality-gate-design.md`
- 实施计划：`docs/superpowers/plans/2026-10-18-wiki-ci-quality-gate.md`

## 用户目标

- 拉取请求和主分支变更继续获得快速、确定性的 Wiki 质量反馈。
- 每天自动运行真实 PostgreSQL/Milvus 完整质量门并保留报告。
- 轻量 Ruff 必须覆盖 Wiki 生产代码、基础设施适配器和配置装配入口。

## RED／GREEN 证据

| 保证 | RED | GREEN |
| --- | --- | --- |
| 每晚北京时间 02:30 可运行完整门并保留 14 天报告 | `uv run pytest tests/test_wiki_quality_workflow.py -q`：`1 failed`，缺少 `schedule:`；RED 提交 `e45bbeb` | 同一命令：`1 passed`；GREEN 提交 `f231cb0` |
| 轻量 Ruff 覆盖 Wiki 生产代码与装配入口 | `uv run pytest tests/test_wiki_quality_command.py::test_light_plan_runs_linter_before_targeted_tests -q`：`1 failed`，缺少 `src/agent_api/knowledge/wiki`；RED 提交 `d564dc5` | `uv run pytest tests/test_wiki_quality_command.py -q`：`4 passed`；`uv run wiki-quality light`：`49 passed, 1 deselected`；GREEN 提交 `d384c2f` |

## 最终验证

| 命令 | 结果 | 保证 |
| --- | --- | --- |
| `uv run ruff check .` | PASS，`All checks passed!` | 全仓 Ruff 无错误 |
| `uv run mypy src` | PASS，73 个源文件无问题 | 后端类型检查无错误 |
| `uv run pytest tests/test_wiki_quality_workflow.py tests/test_wiki_quality_command.py -q` | PASS，`5 passed` | 工作流与质量命令契约一致 |
| `uv run wiki-quality light` | PASS，`49 passed, 1 deselected` | 扩展后的静态检查和确定性 Wiki 回归通过 |

## 已知边界

- 定时工作流只有推送到 GitHub 后才能由 GitHub Actions 实际调度；本地测试验证的是工作流静态契约。
- 本任务不调用真实付费模型，也不包含 1 万知识单元性能验收。
- 完整门继续使用确定性 grounded generator，真实模型摘要质量仍需单独授权验收。
