# Wiki 可读摘要 TDD 证据

- 来源计划：`docs/superpowers/plans/2026-10-07-wiki-readable-summary.md`
- 用户旅程：阅读者看到连贯摘要、白名单信息卡与可展开来源；搜索和 Agent 仅获得原子事实；旧页面不暴露 Markdown 标记。

| 保证 | 测试 | 结果 |
| --- | --- | --- |
| 分段检查点、迁移与旧版兼容 | `tests/test_wiki_processor.py tests/test_knowledge_models.py` | 14 passed |
| 两阶段生成、来源校验 | `tests/test_wiki_generator.py` | 10 passed |
| 标题与信息卡白名单 | `tests/test_wiki_presentation.py` | 通过 |
| 发布、API、Agent/检索隔离 | 聚焦后端 31 项 | 31 passed |
| 结构化页面与旧页兼容展示 | `web/tests/knowledge/wikiPanel.spec.ts` | 5 passed |
| 前端回归 | `npm test -- --run` | 36 passed |

未运行项：独立 PostgreSQL 集成库、真实付费模型摘要质量、Playwright 浏览器验收；它们需要独立服务与数据，不能由单元或组件测试替代。
