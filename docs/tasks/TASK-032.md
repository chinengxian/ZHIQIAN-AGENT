---
id: TASK-032
title: 规划文档详情与 Wiki 来源定位功能
status: done
execution_scope: approved
kind: planning
priority: normal
owner: /root
depends_on: []
requirements:
  - REQ-007
acceptance:
  - 明确前端查看文档信息与已解析正文的范围和非目标
  - 冻结文档详情、版本、分页预览和 Wiki 来源深链的设计级契约
  - 按后端、前端、集成验收拆分依赖、文件归属和可独立验证的任务
  - 不在直接规划阶段修改业务代码
read_refs:
  - docs/requirements/REQ-005.md
  - docs/requirements/REQ-006.md
  - docs/tasks/TASK-020.md
  - docs/tasks/TASK-021.md
  - docs/tasks/TASK-029.md
  - src/agent_api/knowledge/api/routes.py
  - src/agent_api/knowledge/application/management.py
  - src/agent_api/knowledge/infrastructure/database/models.py
  - web/src/views/knowledge/KnowledgeView.vue
  - web/src/views/knowledge/WikiPanel.vue
  - web/src/services/knowledgeApi.ts
write_scope:
  - .gitignore
  - docs/requirements/REQ-007.md
  - docs/superpowers/specs/2026-10-05-document-inspection-design.md
  - docs/superpowers/plans/2026-10-05-document-inspection.md
  - docs/tasks/TASK-032.md
  - docs/tasks/TASK-033.md
  - docs/tasks/TASK-034.md
  - docs/tasks/TASK-035.md
  - docs/tasks/INDEX.md
  - docs/development/README.md
base_revision: 9ed1938358f7901c30da5d7b88b6de2e8ec150db
updated_at: 2026-10-05T23:05:56+08:00
---

## 目标

把用户提出的“前端查看文档等信息”转化为可实施、可验证且能与 Wiki 来源串联的纵向任务图。

## 完成条件

- 需求明确展示范围、安全边界、失效来源行为和必需 AC。
- 技术设计复用现有 PostgreSQL 文档版本与 chunk 数据，不无依据新增迁移。
- 后端契约、前端交互和最终集成验收有清晰依赖与文件所有权。
- 本轮仅修改规划文档，不启动代码实现。

## 范围依据

用户于 2026-10-05 直接调用 `zq-plan`，说明知识库上传与 Wiki 已完成，但前端无法查看文档等信息，并要求增加该功能。该明确指令授权本需求的必要规划拆解。

## 实施记录

- 自动检查现有 REQ-005/REQ-006、TASK-020/TASK-021/TASK-029、知识库 API、文档数据模型和 Vue 页面。
- 确认已有接口只返回列表同级元数据，前端没有文档详情入口，Wiki 来源只展示版本/chunk 标识。
- 创建 REQ-007、技术设计、实施计划及 TASK-033～TASK-035；限定为详情、解析正文预览和 Wiki 来源定位。
- 更新 `.gitignore` 的精确白名单，使新增权威文档及后续 `VERIFY-006.md` 可被 Git 跟踪；不放宽其他 `docs/` 忽略范围。
- 保留现有未跟踪 Celery Beat 文件，不修改 `src/`、`web/src/`、`tests/` 或用户环境配置。

## 验证证据

| AC／条件 | 环境与命令／操作 | 结果 | 证据与修订 |
| --- | --- | --- | --- |
| ID 去重 | 扫描 `docs/tasks/TASK-*.md` | pass | 原最高 ID 为 TASK-031，新任务从 TASK-032 开始 |
| AC 覆盖 | 核对 TASK-033～TASK-035 与 REQ-007 AC-001～AC-006 | pass | 每条 required AC 至少由一个实现任务覆盖，并全部进入 TASK-035 集成验收 |
| 依赖有效 | 解析 `depends_on` 并检查缺失和循环 | pass | TASK-033 → TASK-034 → TASK-035，无缺失或循环依赖 |
| 共享文件归属 | 核对实施计划与任务 `write_scope` | pass | 后端先固定 API，前端负责 TS/页面，集成任务负责真实 E2E 与 VERIFY-006 |
| 文档可追踪性 | `git check-ignore -v` 与 `git status --short` | pass | 仅为 REQ-007、设计、计划、TASK-032～035、VERIFY-006 增加精确白名单 |
| 阶段边界 | 核对本轮 Git 状态与差异 | pass | 仅需求、设计、计划、任务索引、开发入口及对应 `.gitignore` 精确白名单发生变化；业务代码未修改 |

## 阻碍与解除条件

无规划阻碍。开发尚未开始。

## 后续事项

第一项可执行工作为 TASK-033。原 TASK-031 的付费模型质量验证与本功能没有依赖关系，不阻塞 REQ-007。

## 交接

从 REQ-007、技术设计和实施计划恢复；顺序执行 TASK-033 → TASK-034 → TASK-035。当前代理承担协调职责，用户未要求并行或外部任务。

## 变更历史

- 2026-10-05T23:05:56+08:00：根据用户指令创建并完成规划，status=done。

