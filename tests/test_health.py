import httpx
from asgi_lifespan import LifespanManager

from agent_api.core.config import Settings
from agent_api.main import create_app
from tests.fakes import FakeStreamAgent


async def test_health_returns_minimal_status_without_configuration(settings: Settings) -> None:
    app = create_app(
        settings_factory=lambda: settings,
        agent_factory=lambda _: FakeStreamAgent(),
    )

    async with LifespanManager(app):
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            response = await client.get("/health")

    assert response.status_code == 200
    assert response.json() == {"status": "ok"}
    assert "test-secret" not in response.text
