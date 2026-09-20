# Streaming Chat Agent

一个简洁的全栈流式对话工作台。后端使用 FastAPI 与 LangChain，可在 OpenAI 兼容服务和 Anthropic 之间显式选择模型供应商；前端使用 Vue 3、TypeScript、Vite 与 Vuetify 3。供应商、模型地址、密钥和模型名仅从后端环境变量读取。

## 环境要求

- Python 3.11+
- Node.js 20 LTS 或 22 LTS
- 支持 OpenAI Chat Completions 协议的模型服务，或 Anthropic API
- 启用知识库时需要 Docker Desktop（WSL 2 后端）和至少 8 GB 可用内存

## 后端启动

```powershell
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -e ".[dev]"
Copy-Item .env.example .env
```

编辑 `.env`。`AGENT_MODEL_PROVIDER` 是必填项，只能选择一个供应商；修改任何模型配置后需要重启后端进程才会生效。

OpenAI 或 OpenAI 兼容服务使用以下完整配置：

```dotenv
AGENT_MODEL_PROVIDER=openai
AGENT_OPENAI_BASE_URL=https://api.example.com/v1
AGENT_OPENAI_API_KEY=replace-me
AGENT_OPENAI_MODEL=replace-me
```

Anthropic 使用以下配置；`AGENT_ANTHROPIC_BASE_URL` 可选，不设置时使用 SDK 默认地址：

```dotenv
AGENT_MODEL_PROVIDER=anthropic
AGENT_ANTHROPIC_API_KEY=replace-me
AGENT_ANTHROPIC_MODEL=replace-me
# AGENT_ANTHROPIC_BASE_URL=https://api.anthropic.com
```

从旧版升级的 OpenAI 部署必须在原有三项配置之外增加 `AGENT_MODEL_PROVIDER=openai`，否则后端会因缺少必填供应商而无法启动。未选中供应商的配置字段会被忽略；为避免配置歧义，建议 `.env` 只保留当前所选供应商的字段。

## 知识基础设施

`.env.example` 默认启用原生知识库。PostgreSQL 保存权威业务状态和正文，Milvus 保存可重建的 Dense/BM25 索引，Redis 用于异步任务，原文件写入独立的 `AGENT_STORAGE_ROOT`。Embedding 配置与聊天模型完全独立。

启动基础设施并应用迁移：

```powershell
docker compose up -d postgres redis etcd minio milvus
.\.venv\Scripts\python.exe -m alembic upgrade head
```

首次启动 API 时，如果 Milvus collection 不存在，应用会按当前 `AGENT_EMBEDDING_DIMENSION` 创建包含 Dense、BM25 sparse 和知识范围字段的 collection；如果 collection 已存在但 schema 或维度不兼容，启动会安全失败且不会删除或重建已有索引。PostgreSQL、Redis、Milvus、迁移版本和存储目录任一校验失败时，API 都不会进入就绪状态。

只运行原有聊天功能时可以设置：

```dotenv
AGENT_KNOWLEDGE_ENABLED=false
```

启动 API：

```powershell
.\.venv\Scripts\python.exe -m uvicorn agent_api.main:app --reload
```

健康检查位于 `GET http://127.0.0.1:8000/health`，流式接口为 `POST http://127.0.0.1:8000/api/v1/chat/stream`。

## 前端启动

打开另一个终端：

```powershell
Set-Location web
npm ci
npm run dev
```

访问 Vite 输出的本地地址。开发服务器会把 `/api` 与 `/health` 代理到 `127.0.0.1:8000`，页面不会接触或保存模型密钥。

## SSE 契约

请求体发送页内会话 UUID 和当前一轮用户消息；同一 `conversation_id` 会恢复进程内短期记忆：

```json
{
  "conversation_id": "82de55a8-6065-4eeb-84af-079ea2e2b2c5",
  "message": "你好"
}
```

短期记忆由 LangGraph `InMemorySaver` 提供，仅适合当前本地／受信环境；进程重启后清空，不支持多进程共享或生产持久化。

服务端依次返回 `message`、`done` 或 `error` 事件：

```text
event: message
data: {"content":"你好"}

event: done
data: {}
```

## 质量命令

后端：

```powershell
.\.venv\Scripts\python.exe -m pytest
.\.venv\Scripts\python.exe -m ruff check .
.\.venv\Scripts\python.exe -m ruff format --check .
.\.venv\Scripts\python.exe -m mypy src tests
```

前端：

```powershell
Set-Location web
npm run lint
npm run type-check
npm run test -- --run
npm run build
npm run test:e2e
```

E2E 使用测试专用的确定性流模型，不会请求真实模型或消耗额度。Playwright 配置默认驱动本机 Chrome。

## 工程结构

```text
src/agent_api/
  api/          # HTTP 与 SSE 边界
  core/         # 配置和 lifespan 初始化
  llm/          # 统一 Agent、供应商策略注册表及 OpenAI/Anthropic 模型适配
  knowledge/    # 知识领域状态、PostgreSQL/Milvus/Redis 与文件存储基础设施
  schemas/      # 请求模型
migrations/     # Alembic 数据库迁移
tests/          # 后端测试与浏览器联调夹具
web/src/
  components/   # 对话工作台组件
  composables/  # 会话状态机
  services/     # Fetch 与增量 SSE 解析
  styles/       # 视觉令牌
```

`.env` 已被忽略；不要把真实 API Key 提交到版本控制。
