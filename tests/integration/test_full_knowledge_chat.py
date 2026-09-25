import json
import os
import threading
from http.server import ThreadingHTTPServer
from pathlib import Path
from uuid import UUID, uuid4

import httpx
import pytest
from asgi_lifespan import LifespanManager
from pydantic import AnyHttpUrl, SecretStr
from pymilvus import MilvusClient  # type: ignore[import-untyped]

from agent_api.core.config import ModelProvider, Settings
from agent_api.knowledge.application.ingestion import IngestionPipeline, ParentChildChunker
from agent_api.knowledge.infrastructure.docling.parser import DoclingParser
from agent_api.knowledge.infrastructure.embedding.openai import create_embedding_adapter
from agent_api.knowledge.infrastructure.jobs.repository import SqlAlchemyIngestionRepository
from agent_api.knowledge.infrastructure.milvus.index import MilvusChunkIndex
from agent_api.llm.agent import LangChainAgentStream
from agent_api.main import create_app
from tests.integration.knowledge_chat_model import DeterministicKnowledgeModel
from tests.test_docling_adapter import minimal_text_pdf
from tests.test_ingestion_e2e_integration import docx_bytes
from tests.test_knowledge_management_integration import (
    DATABASE_URL,
    LocalEmbeddingHandler,
    cleanup_knowledge_base,
)

pytestmark = [
    pytest.mark.knowledge_integration,
    pytest.mark.skipif(
        os.getenv("RUN_KNOWLEDGE_INTEGRATION") != "1",
        reason="set RUN_KNOWLEDGE_INTEGRATION=1 to use local knowledge containers",
    ),
]


def parse_events(body: str) -> list[tuple[str, dict[str, object]]]:
    events = []
    for frame in body.split("\n\n"):
        if not frame.strip():
            continue
        event, data = frame.split("\ndata: ", 1)
        events.append((event.removeprefix("event: "), json.loads(data)))
    return events


async def test_four_formats_upload_to_real_index_and_cited_chat(tmp_path: Path) -> None:
    server = ThreadingHTTPServer(("127.0.0.1", 0), LocalEmbeddingHandler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    settings = Settings(
        model_provider=ModelProvider.OPENAI,
        openai_base_url=AnyHttpUrl("https://example.test/v1"),
        openai_api_key=SecretStr("local-test-key"),
        openai_model="deterministic-knowledge-test",
        knowledge_enabled=True,
        database_url=SecretStr(DATABASE_URL),
        redis_url=SecretStr("redis://127.0.0.1:6379/0"),
        storage_root=tmp_path / "uploads",
        milvus_uri="http://127.0.0.1:19530",
        embedding_base_url=AnyHttpUrl(f"http://127.0.0.1:{server.server_port}/v1"),
        embedding_api_key=SecretStr("local-embedding-key"),
        embedding_model="text-embedding-3-small",
        embedding_dimension=1536,
        _env_file=None,  # type: ignore[call-arg]
    )
    app = create_app(
        settings_factory=lambda: settings,
        agent_factory=lambda _: LangChainAgentStream(DeterministicKnowledgeModel()),
    )
    base_id: UUID | None = None
    versions: list[UUID] = []
    milvus = MilvusClient(uri=settings.milvus_uri)
    index = MilvusChunkIndex(milvus, settings.milvus_collection)
    try:
        async with LifespanManager(app):
            database = app.state.knowledge_database
            pipeline = IngestionPipeline(
                repository=SqlAlchemyIngestionRepository(
                    database.session_factory, app.state.knowledge_startup.storage
                ),
                parser=DoclingParser(),
                chunker=ParentChildChunker(),
                embedder=create_embedding_adapter(settings),
                index=index,
            )
            transport = httpx.ASGITransport(app=app)
            async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
                created = await client.post(
                    "/api/v1/knowledge-bases", json={"name": f"闭环-{uuid4()}", "description": ""}
                )
                assert created.status_code == 201
                base_id = UUID(created.json()["id"])
                fixtures = [
                    (
                        "pdf.pdf",
                        "application/pdf",
                        minimal_text_pdf("OrbitalPDF evidence"),
                        "OrbitalPDF",
                    ),
                    (
                        "docx.docx",
                        "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
                        docx_bytes(),
                        "integration",
                    ),
                    (
                        "markdown.md",
                        "text/markdown",
                        b"# Manual\nMarkdownToken evidence",
                        "MarkdownToken",
                    ),
                    ("plain.txt", "text/plain", b"PlainToken evidence", "PlainToken"),
                ]
                for filename, mime_type, content, question in fixtures:
                    uploaded = await client.post(
                        f"/api/v1/knowledge-bases/{base_id}/documents",
                        files={"file": (filename, content, mime_type)},
                    )
                    assert uploaded.status_code == 202
                    accepted = uploaded.json()
                    versions.append(UUID(accepted["document_version_id"]))
                    await pipeline.run(UUID(accepted["job_id"]))
                    document = await client.get(f"/api/v1/documents/{accepted['document_id']}")
                    assert document.json()["status"] == "ready"
                    assert await index.count_version(versions[-1]) > 0
                    response = await client.post(
                        "/api/v1/chat/stream",
                        json={
                            "conversation_id": str(uuid4()),
                            "message": question,
                            "knowledge_scope": {
                                "mode": "selected",
                                "knowledge_base_ids": [str(base_id)],
                            },
                        },
                    )
                    assert response.status_code == 200, response.text
                    events = parse_events(response.text)
                    sources = [data for name, data in events if name == "sources"]
                    messages = [str(data["content"]) for name, data in events if name == "message"]
                    assert sources and messages
                    assert any(
                        item["document_id"] == accepted["document_id"]
                        for item in sources[-1]["items"]
                    )
                    assert (
                        "[" in "".join(messages)
                        and question.casefold() in "".join(messages).casefold()
                    )
                    assert events[-1][0] == "done"
    finally:
        for version in versions:
            await index.delete_version(version)
        if base_id is not None:
            from agent_api.knowledge.infrastructure.database.runtime import DatabaseRuntime

            database = DatabaseRuntime(DATABASE_URL)
            try:
                await cleanup_knowledge_base(database, base_id)
            finally:
                await database.close()
        milvus.close()
        server.shutdown()
        server.server_close()
