from pathlib import Path

from pydantic import AnyHttpUrl, SecretStr

from agent_api.core.config import ModelProvider, Settings

COLLECTION_PROBE_ENV_FILE = Path(__file__).with_name(
    "__pytest_collection_dotenv_guard_intentionally_missing__.env"
)
assert not COLLECTION_PROBE_ENV_FILE.exists()

try:
    Settings(
        model_provider=ModelProvider.OPENAI,
        openai_base_url=AnyHttpUrl("https://example.test/v1"),
        openai_api_key=SecretStr("collection-probe-secret"),
        openai_model="collection-probe-model",
        _env_file=COLLECTION_PROBE_ENV_FILE,  # type: ignore[call-arg]
    )
except AssertionError as error:
    assert "dotenv reads are forbidden" in str(error)
    COLLECTION_GUARD_ACTIVE = True
else:
    raise AssertionError("pytest_configure did not install the collection-time dotenv guard")


def test_dotenv_guard_is_active_during_collection() -> None:
    assert COLLECTION_GUARD_ACTIVE
