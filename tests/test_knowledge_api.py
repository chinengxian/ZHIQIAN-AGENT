from uuid import UUID

import httpx
from fastapi import FastAPI

from agent_api.knowledge.api.routes import get_management_service, router
from agent_api.knowledge.application.management import KnowledgeConflictError

KB_ID = UUID("10000000-0000-0000-0000-000000000001")
DOCUMENT_ID = UUID("20000000-0000-0000-0000-000000000001")
JOB_ID = UUID("30000000-0000-0000-0000-000000000001")
VERSION_ID = UUID("30000000-0000-0000-0000-000000000002")


class FakeManagementService:
    def __init__(self) -> None:
        self.max_upload_bytes = 1024
        self.created: list[dict[str, object]] = []
        self.uploaded: list[tuple[UUID, str, str, bytes]] = []
        self.duplicate_policies: list[str] = []

    async def create_knowledge_base(self, **values: object) -> dict[str, object]:
        self.created.append(values)
        return {
            "id": KB_ID,
            "name": values["name"],
            "description": values["description"],
            "enabled": True,
            "document_count": 0,
            "processing_count": 0,
        }

    async def list_knowledge_bases(self) -> list[dict[str, object]]:
        return [
            {
                "id": KB_ID,
                "name": "产品资料",
                "description": "说明书",
                "enabled": True,
                "document_count": 2,
                "processing_count": 1,
            }
        ]

    async def get_knowledge_base(self, knowledge_base_id: UUID) -> dict[str, object]:
        return (await self.list_knowledge_bases())[0]

    async def update_knowledge_base(
        self, knowledge_base_id: UUID, **values: object
    ) -> dict[str, object]:
        item = (await self.list_knowledge_bases())[0]
        item.update({key: value for key, value in values.items() if value is not None})
        return item

    async def list_documents(self, knowledge_base_id: UUID) -> list[dict[str, object]]:
        return [
            {
                "id": DOCUMENT_ID,
                "knowledge_base_id": knowledge_base_id,
                "title": "guide",
                "filename": "guide.md",
                "mime_type": "text/markdown",
                "status": "ready",
                "active_version_id": None,
                "job": None,
            }
        ]

    async def get_document(self, document_id: UUID) -> dict[str, object]:
        return (await self.list_documents(KB_ID))[0]

    async def request_document_action(self, document_id: UUID, action: str) -> dict[str, object]:
        return {"document_id": document_id, "job_id": JOB_ID, "status": "pending"}

    async def delete_document(self, document_id: UUID) -> dict[str, object]:
        return {"document_id": document_id, "job_id": JOB_ID, "status": "deleting"}

    async def stream_events(self):
        yield {"type": "document_progress", "document_id": str(DOCUMENT_ID), "progress": 50}

    async def upload_document(
        self,
        knowledge_base_id: UUID,
        filename: str,
        mime_type: str,
        content: bytes,
        *,
        on_duplicate: str = "reject",
    ) -> dict[str, object]:
        self.uploaded.append((knowledge_base_id, filename, mime_type, content))
        self.duplicate_policies.append(on_duplicate)
        return {
            "document_id": DOCUMENT_ID,
            "document_version_id": VERSION_ID,
            "job_id": JOB_ID,
            "status": "pending",
        }

    async def delete_knowledge_base(self, knowledge_base_id: UUID, *, confirm: bool) -> None:
        raise KnowledgeConflictError("knowledge_base_not_empty", requires_confirmation=True)


def create_test_app(service: FakeManagementService) -> FastAPI:
    app = FastAPI()
    app.include_router(router)
    app.dependency_overrides[get_management_service] = lambda: service
    return app


async def test_knowledge_base_create_and_list_contract() -> None:
    service = FakeManagementService()
    transport = httpx.ASGITransport(app=create_test_app(service))
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        created = await client.post(
            "/api/v1/knowledge-bases",
            json={"name": "产品资料", "description": "说明书"},
        )
        listed = await client.get("/api/v1/knowledge-bases")

    assert created.status_code == 201
    assert created.json() == {
        "id": str(KB_ID),
        "name": "产品资料",
        "description": "说明书",
        "enabled": True,
        "document_count": 0,
        "processing_count": 0,
    }
    assert listed.status_code == 200
    assert listed.json()[0]["document_count"] == 2
    assert service.created == [{"name": "产品资料", "description": "说明书"}]


async def test_document_upload_returns_202_after_persistence_boundary() -> None:
    service = FakeManagementService()
    transport = httpx.ASGITransport(app=create_test_app(service))
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.post(
            f"/api/v1/knowledge-bases/{KB_ID}/documents",
            files={"file": ("guide.md", "# 使用说明".encode(), "text/markdown")},
        )

    assert response.status_code == 202
    assert response.json() == {
        "document_id": str(DOCUMENT_ID),
        "document_version_id": str(VERSION_ID),
        "job_id": str(JOB_ID),
        "status": "pending",
    }
    assert service.uploaded == [(KB_ID, "guide.md", "text/markdown", "# 使用说明".encode())]


async def test_oversized_upload_is_rejected_before_management_call() -> None:
    service = FakeManagementService()
    transport = httpx.ASGITransport(app=create_test_app(service))
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.post(
            f"/api/v1/knowledge-bases/{KB_ID}/documents",
            files={"file": ("large.txt", b"x" * 1025, "text/plain")},
        )

    assert response.status_code == 400
    assert response.json()["detail"]["code"] == "file_too_large"
    assert service.uploaded == []


async def test_explicit_duplicate_policy_is_forwarded_and_version_id_returned() -> None:
    service = FakeManagementService()
    transport = httpx.ASGITransport(app=create_test_app(service))
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.post(
            f"/api/v1/knowledge-bases/{KB_ID}/documents?on_duplicate=new_version",
            files={"file": ("guide.txt", b"same text", "text/plain")},
        )

    assert response.status_code == 202
    assert response.json()["document_version_id"] == str(VERSION_ID)
    assert service.duplicate_policies == ["new_version"]


async def test_delete_non_empty_knowledge_base_requires_explicit_confirmation() -> None:
    service = FakeManagementService()
    transport = httpx.ASGITransport(app=create_test_app(service))
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.delete(f"/api/v1/knowledge-bases/{KB_ID}")

    assert response.status_code == 409
    assert response.json() == {
        "detail": {
            "code": "knowledge_base_not_empty",
            "message": "Knowledge base contains documents",
            "requires_confirmation": True,
        }
    }


async def test_knowledge_base_payload_rejects_blank_name() -> None:
    service = FakeManagementService()
    transport = httpx.ASGITransport(app=create_test_app(service))
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.post(
            "/api/v1/knowledge-bases",
            json={"name": "   ", "description": ""},
        )

    assert response.status_code == 422
    assert service.created == []


async def test_management_query_update_document_actions_and_event_stream() -> None:
    service = FakeManagementService()
    transport = httpx.ASGITransport(app=create_test_app(service))
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        detail = await client.get(f"/api/v1/knowledge-bases/{KB_ID}")
        updated = await client.patch(f"/api/v1/knowledge-bases/{KB_ID}", json={"enabled": False})
        documents = await client.get(f"/api/v1/knowledge-bases/{KB_ID}/documents")
        document = await client.get(f"/api/v1/documents/{DOCUMENT_ID}")
        retry = await client.post(f"/api/v1/documents/{DOCUMENT_ID}/retry")
        reindex = await client.post(f"/api/v1/documents/{DOCUMENT_ID}/reindex")
        deleted = await client.delete(f"/api/v1/documents/{DOCUMENT_ID}")
        events = await client.get("/api/v1/knowledge/events/stream")

    assert detail.status_code == 200
    assert updated.json()["enabled"] is False
    assert documents.json()[0]["filename"] == "guide.md"
    assert document.json()["id"] == str(DOCUMENT_ID)
    assert retry.status_code == 202
    assert reindex.status_code == 202
    assert deleted.status_code == 202
    assert events.headers["content-type"].startswith("text/event-stream")
    assert "event: document_progress" in events.text
    assert f'"document_id":"{DOCUMENT_ID}"' in events.text
