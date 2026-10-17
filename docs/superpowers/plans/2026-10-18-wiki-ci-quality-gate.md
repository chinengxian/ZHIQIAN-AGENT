# Wiki CI 分层质量门实施计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 让 Wiki 拉取请求持续获得快速反馈，并让真实 PostgreSQL/Milvus 完整质量门每天自动运行和留存报告。

**Architecture:** 保留现有 `wiki-quality light/full` 两级命令边界，只调整 GitHub Actions 的触发策略和轻量 Ruff 输入范围。工作流契约由文本级测试锁定，质量命令契约由 Python 单元测试锁定，完整评测仍强制使用独立 `_test` 数据库。

**Tech Stack:** GitHub Actions、Python 3.11、pytest、Ruff、mypy、Docker Compose、PostgreSQL、Milvus

---

## 文件结构

- 修改 `.github/workflows/wiki-quality.yml`：增加夜间触发、完整任务条件和报告保留期。
- 修改 `tests/test_wiki_quality_workflow.py`：验证自动轻量门、夜间完整门、人工完整门和清理契约。
- 修改 `src/agent_api/quality/wiki.py`：扩大轻量 Ruff 对 Wiki 生产代码的覆盖。
- 修改 `tests/test_wiki_quality_command.py`：锁定 Ruff 目标范围并防止退回只检查评测脚本。
- 修改 `docs/verification/VERIFY-005.md`：记录 CI 分层门的新鲜验证证据和边界。
- 创建 `docs/testing/wiki-ci-quality-gate.tdd.md`：保存 RED/GREEN、命令和已知缺口。

### Task 1：夜间完整质量门契约

**Files:**
- Modify: `.github/workflows/wiki-quality.yml`
- Modify: `tests/test_wiki_quality_workflow.py`

- [x] **Step 1：写入失败的工作流契约测试**

把 `tests/test_wiki_quality_workflow.py` 的主测试更新为：

```python
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
```

- [x] **Step 2：运行测试并确认 RED**

Run：

```powershell
uv run pytest tests/test_wiki_quality_workflow.py -q
```

Expected：FAIL，至少缺少 `schedule:`、cron、定时任务条件或 `retention-days: 14`。

- [x] **Step 3：提交 RED 检查点**

```powershell
git add tests/test_wiki_quality_workflow.py
git commit -m "测试：锁定Wiki夜间完整质量门契约"
```

- [x] **Step 4：实现最小工作流变更**

在 `.github/workflows/wiki-quality.yml` 的触发器中加入：

```yaml
  schedule:
    # GitHub Actions 使用 UTC；18:30 对应北京时间次日 02:30。
    - cron: "30 18 * * *"
```

把完整任务条件改为：

```yaml
    if: github.event_name == 'workflow_dispatch' || github.event_name == 'schedule'
```

在上传报告步骤加入：

```yaml
          retention-days: 14
```

- [x] **Step 5：运行测试并确认 GREEN**

Run：

```powershell
uv run pytest tests/test_wiki_quality_workflow.py -q
```

Expected：`1 passed`。

- [x] **Step 6：提交 GREEN 检查点**

```powershell
git add .github/workflows/wiki-quality.yml
git commit -m "持续集成：增加Wiki夜间完整质量门"
```

### Task 2：扩大轻量 Ruff 覆盖

**Files:**
- Modify: `src/agent_api/quality/wiki.py`
- Modify: `tests/test_wiki_quality_command.py`

- [x] **Step 1：写入失败的 Ruff 目标测试**

在 `tests/test_wiki_quality_command.py` 的轻量计划测试中增加：

```python
    lint_targets = set(plan.steps[0].command[3:])
    assert "src/agent_api/knowledge/wiki" in lint_targets
    assert "src/agent_api/knowledge/infrastructure/milvus/wiki_index.py" in lint_targets
    assert "src/agent_api/knowledge/infrastructure/observability/events.py" in lint_targets
    assert "src/agent_api/core/config.py" in lint_targets
    assert "src/agent_api/core/lifespan.py" in lint_targets
```

- [x] **Step 2：运行测试并确认 RED**

Run：

```powershell
uv run pytest tests/test_wiki_quality_command.py::test_light_plan_runs_linter_before_targeted_tests -q
```

Expected：FAIL，提示 Wiki 生产目录或配置装配文件不在 Ruff 目标中。

- [x] **Step 3：提交 RED 检查点**

```powershell
git add tests/test_wiki_quality_command.py
git commit -m "测试：锁定Wiki轻量门生产代码覆盖"
```

- [x] **Step 4：扩展 `_LINT_FILES`**

把 `src/agent_api/quality/wiki.py` 中 `_LINT_FILES` 的开头调整为：

```python
_LINT_FILES = (
    "src/agent_api/knowledge/wiki",
    "src/agent_api/knowledge/infrastructure/milvus/wiki_index.py",
    "src/agent_api/knowledge/infrastructure/observability/events.py",
    "src/agent_api/core/config.py",
    "src/agent_api/core/lifespan.py",
    "src/agent_api/quality/wiki.py",
```

保留原有评测与质量门测试文件，避免缩小既有覆盖。

- [x] **Step 5：运行目标测试和轻量门并确认 GREEN**

Run：

```powershell
uv run pytest tests/test_wiki_quality_command.py -q
uv run wiki-quality light
```

Expected：命令测试全部通过；轻量门输出 `49 passed, 1 deselected` 或更多通过项，且退出码为 0。

- [x] **Step 6：提交 GREEN 检查点**

```powershell
git add src/agent_api/quality/wiki.py
git commit -m "持续集成：扩大Wiki轻量静态检查范围"
```

### Task 3：全量验证与证据归档

**Files:**
- Modify: `docs/verification/VERIFY-005.md`
- Create: `docs/testing/wiki-ci-quality-gate.tdd.md`

- [x] **Step 1：运行新鲜验证命令**

Run：

```powershell
uv run ruff check .
uv run mypy src
uv run pytest tests/test_wiki_quality_workflow.py tests/test_wiki_quality_command.py -q
uv run wiki-quality light
```

Expected：Ruff 与 mypy 退出码为 0；工作流和命令契约测试全部通过；轻量门退出码为 0。

- [x] **Step 2：核对工作流 diff**

Run：

```powershell
git diff HEAD~4 -- .github/workflows/wiki-quality.yml src/agent_api/quality/wiki.py tests/test_wiki_quality_workflow.py tests/test_wiki_quality_command.py
```

Expected：只包含夜间 cron、完整任务条件、报告保留期、Ruff 目标和对应测试；不包含供应商凭据、付费调用或业务检索逻辑。

- [x] **Step 3：写入 TDD 证据报告**

创建 `docs/testing/wiki-ci-quality-gate.tdd.md`，内容必须包含：

```markdown
# Wiki CI 分层质量门 TDD 证据

- 来源规格：`docs/superpowers/specs/2026-10-18-wiki-ci-quality-gate-design.md`
- 实施计划：`docs/superpowers/plans/2026-10-18-wiki-ci-quality-gate.md`

| 保证 | RED | GREEN |
| --- | --- | --- |
| 每晚北京时间 02:30 可运行完整门并保留 14 天报告 | 工作流契约测试因缺少 schedule/retention 失败 | 工作流契约测试通过 |
| 轻量 Ruff 覆盖 Wiki 生产代码与装配入口 | 命令契约测试因缺少生产目标失败 | 命令契约测试与 `wiki-quality light` 通过 |

## 已知边界

- 定时工作流只有推送到 GitHub 后才能由 GitHub Actions 实际调度。
- 本任务不调用真实付费模型，也不包含 1 万知识单元性能验收。
```

把实际命令、退出码和测试计数补充到对应 GREEN 单元格，不记录凭据或文档正文。

- [x] **Step 4：更新 VERIFY-005**

在 `docs/verification/VERIFY-005.md` 顶部新增“2026-10-18 CI 分层质量门”章节，记录：

- PR/main 运行轻量门；
- 每天北京时间 02:30 和人工触发运行完整门；
- 报告保留 14 天；
- 新鲜 Ruff、mypy、契约测试和轻量门结果；
- GitHub 实际定时调度需在推送后由远端运行验证。

- [x] **Step 5：提交证据**

```powershell
git add -f docs/testing/wiki-ci-quality-gate.tdd.md docs/verification/VERIFY-005.md
git commit -m "验证：记录Wiki持续质量门证据"
```

- [x] **Step 6：确认工作树状态**

Run：

```powershell
git status --short --branch
git log -6 --oneline
```

Expected：工作树干净；当前任务包含两个 RED 检查点、两个 GREEN 检查点和一个证据提交，且均可从当前 HEAD 到达。
