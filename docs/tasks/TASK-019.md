---
id: TASK-019
title: 建设知识基础设施与持久化基线
status: done
execution_scope: approved
kind: backend
priority: high
owner: /root
depends_on:
  - TASK-018
requirements:
  - REQ-005
acceptance:
  - REQ-005/AC-001
  - REQ-005/AC-005
  - REQ-005/AC-011
read_refs:
  - docs/requirements/REQ-005.md
  - docs/superpowers/specs/2026-09-20-native-knowledge-rag-design.md
  - pyproject.toml
  - src/agent_api/core/config.py
  - src/agent_api/core/lifespan.py
write_scope:
  - pyproject.toml
  - .env.example
  - README.md
  - docs/development/README.md
  - docs/development/knowledge-infrastructure-flow.md
  - compose.yaml
  - alembic.ini
  - migrations/
  - src/agent_api/core/config.py
  - src/agent_api/core/lifespan.py
  - src/agent_api/knowledge/domain/
  - src/agent_api/knowledge/infrastructure/database/
  - src/agent_api/knowledge/infrastructure/storage/
  - tests/test_knowledge_config.py
  - tests/test_knowledge_models.py
  - tests/test_knowledge_storage.py
  - tests/test_knowledge_startup.py
  - tests/conftest.py
  - docs/tasks/TASK-019.md
base_revision: 83c58e29e87019acc449de54d6ef368726a85668
updated_at: 2026-09-20T22:07:43+08:00
---

## 目标

建立 PostgreSQL、Redis、Milvus、文件存储、迁移和知识领域模型的可测试基础，为后续入库与检索提供唯一共享配置和持久化边界。

## 完成条件

- SQLAlchemy/Alembic 模型覆盖工作区、知识库、文档、版本、chunk、任务和 outbox。
- Compose 可以启动 PostgreSQL、Redis、Milvus 及其内部依赖，应用文件卷独立。
- 启动期校验连接、迁移、存储目录、Milvus schema 和 Embedding 维度，不静默破坏已有数据。
- 根依赖、锁文件、配置示例和 README 由本任务统一修改。

## 范围依据

REQ-005 AC-001、AC-005、AC-011 及已确认设计第 3、6、7、12 节。

## 实施记录

- 2026-09-20：由 `zq-flow` 自动选择为首个 approved 后端任务，owner 更新为 `/root` 并开始执行。
- 环境预检确认 Python 3.11 与 `uv` 可用；本机未安装 Docker，因此容器集成证据暂时不能在当前主机运行。
- 首轮 TDD 已完成：配置、领域状态、ORM 元数据和本地存储共 19 个测试先因模块缺失失败，最小实现后全部通过。
- 增加 PostgreSQL/Redis/Milvus 启动检查、Milvus Dense + BM25 schema 安全创建与兼容性校验、Alembic 初始迁移、异步数据库运行时、固定版本 Compose、独立上传目录及 `uv.lock`。
- 按 Milvus 2.6 官方部署资料固定 Milvus `v2.6.23` 与配套 MinIO 版本；现有 collection 不兼容时只失败，不删除或重建。
- 依赖审计发现并修复开发环境中的 pytest/setuptools 公告；锁定 pytest 9.1.1、setuptools 83.0.0 后重新审计为零已知漏洞。
- 根据用户可读性要求，为配置、生命周期、状态、ORM 模型、启动检查、存储、迁移与 Compose 补充中文职责/安全边界注释；新增中文链路文档，区分已实现启动链和后续入库、检索链。
- 安装并启动 Docker Desktop 4.91.0（Engine 29.8.0，WSL 2）；清理旧安装遗留的失效运行时套接字后，真实启动 PostgreSQL、Redis、etcd、MinIO 与 Milvus。
- 真实迁移暴露默认工作区 UUID 被绑定为 VARCHAR 的问题；按 TDD 新增回归测试并改为原生 UUID 参数，随后 Alembic 成功升级到 head。
- Docker Hub 的 MinIO 旧标签已不可拉取；在保持固定版本不变的前提下改用经 manifest 验证存在的官方 Quay 镜像。

## 验证证据

| AC／条件 | 环境与命令／操作 | 结果 | 证据与修订 |
| --- | --- | --- | --- |
| AC-001 | `docker compose up -d postgres redis etcd minio milvus`；`.venv\\Scripts\\python.exe -m alembic upgrade head`；`.venv\\Scripts\\python.exe -m pytest -q`；Ruff、format、mypy | pass | 五个容器均 healthy；Alembic current=`20260920_0001 (head)`；97 tests passed；Ruff/format/mypy pass |
| AC-005 | ORM/迁移元数据测试；真实 PostgreSQL 默认工作区查询；项目 `DatabaseStartupCheck`、`RedisStartupCheck`、`MilvusStartupCheck` | pass | 默认工作区 UUID 正确落库；database/redis/milvus 均返回 ok；Milvus `knowledge_chunks` Dense + BM25 schema 创建并校验通过；原子版本切换行为由 TASK-020 继续实现 |
| AC-011 | 配置秘密脱敏、启动失败回收、受控 UUID 文件路径、危险 root/后缀拒绝、幂等删除 | pass / fault injection deferred | `tests/test_knowledge_config.py`、`tests/test_knowledge_startup.py`、`tests/test_knowledge_storage.py`；真实故障注入由 TASK-024 负责 |
| 依赖安全 | `uv run --no-sync --with pip-audit pip-audit` | pass | No known vulnerabilities found；本地项目包因未发布 PyPI 被正常跳过 |

## 阻碍与解除条件

无。Docker Desktop、真实容器、数据库迁移以及应用使用的 PostgreSQL／Redis／Milvus 启动检查均已通过；外部付费 Embedding 调用不属于本基础设施任务。

## 后续事项

无独立建议。

## 交接

当前代码已向 TASK-020 准备可导入的数据库运行时、迁移、配置和存储端口，并向 TASK-022 准备 Milvus/Embedding 配置边界。TASK-019 已完成，TASK-020 的依赖阻碍已解除。

## 变更历史

- 2026-09-20T17:53:12+08:00：创建 approved 高优先级基础任务，status=todo。
- 2026-09-20T18:15:56+08:00：`zq-flow` 选择任务并开始执行，owner=/root，status=in_progress；记录 Docker 不可用边界。
- 2026-09-20T20:23:03+08:00：本地实现和质量门完成，status=verifying；等待真实容器集成证据。
- 2026-09-20T20:39:59+08:00：补充中文代码注释与端到端链路文档，保持 status=verifying。
- 2026-09-20T22:07:43+08:00：完成 Docker/WSL2 安装、五服务真实启动、在线迁移与三项应用启动检查；修复 UUID 迁移回归，status=done。
