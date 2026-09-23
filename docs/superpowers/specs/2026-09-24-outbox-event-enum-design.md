# Outbox 事件类型枚举设计

## 目标

将知识模块当前使用的五种 Outbox 事件类型集中定义为领域枚举，消除生产端、消费端、数据库查询和测试中的裸字符串，同时保持 PostgreSQL 已存数据及 Celery JSON 消息协议不变。

## 范围

新增 `OutboxEventType`，覆盖以下事件：

- `document.ingestion.requested`
- `document.deletion.requested`
- `document.version.cleanup.requested`
- `knowledge_base.deletion.requested`
- `wiki.document.generate.requested`

所有 Python 代码中对这些事件值的创建、比较、查询、测试构造和断言都改用枚举成员。SSE 进度事件、可观测性日志事件和任务状态不属于本次范围。

## 设计

在 `src/agent_api/knowledge/domain/events.py` 中定义继承 `StrEnum` 的 `OutboxEventType`。枚举位于独立模块，避免把事件类型与 `statuses.py` 中的持久化状态混在一起。

内部函数使用 `OutboxEventType` 表达事件类型。Celery 的 `process_event` 是外部消息边界，接收 JSON 字符串后立即转换为枚举；未知事件保持失败，不进行默认处理。`StrEnum` 仍可作为字符串写入现有 `VARCHAR` 字段并序列化成原有 JSON 字符串，因此不修改数据库模型、迁移或消息协议。

## 数据流

1. 应用服务或仓储使用枚举成员创建 `OutboxEvent`。
2. Outbox dispatcher 从数据库读取原始字符串并构造待投递事件。
3. Celery JSON 消息继续携带原有字符串值。
4. `process_event` 在入口处转换为 `OutboxEventType`。
5. Worker 和 Wiki 分发逻辑只比较枚举成员。

## 错误处理

不在枚举中的消息在 Celery 消费边界转换时抛出 `ValueError`。普通 Worker 的白名单兜底仍保留，防止未来调用者绕过 Celery 边界传入不支持的枚举类型。

## 测试策略

遵循 TDD：

1. 先增加测试，固定五个枚举成员及其持久化字符串值。
2. 验证枚举可以按原值进行 JSON 序列化，确保 Celery 协议兼容。
3. 更新事件生产、查询和 Worker 分发测试使用枚举成员。
4. 运行定向单元测试、集成测试、Ruff 和 Mypy，确认没有遗留裸事件字符串。

## 不在范围内

- 不修改 Outbox 表结构。
- 不增加或删除事件类型。
- 不修改事件名称或 payload。
- 不重构 SSE、日志或任务状态枚举。
