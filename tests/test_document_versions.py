from uuid import UUID

import pytest

from agent_api.knowledge.application.ingestion import VersionActivationError, VersionActivator

DOCUMENT_ID = UUID("60000000-0000-0000-0000-000000000001")
OLD_VERSION_ID = UUID("60000000-0000-0000-0000-000000000002")
NEW_VERSION_ID = UUID("60000000-0000-0000-0000-000000000003")


class FakeVersionRepository:
    def __init__(self) -> None:
        self.active_version_id = OLD_VERSION_ID
        self.ready: list[UUID] = []
        self.cleanup: list[UUID] = []
        self.failed: list[tuple[UUID, str]] = []

    async def activate(
        self,
        document_id: UUID,
        version_id: UUID,
        previous_version_id: UUID | None,
    ) -> None:
        assert document_id == DOCUMENT_ID
        assert previous_version_id == self.active_version_id
        self.active_version_id = version_id
        self.ready.append(version_id)
        if previous_version_id is not None:
            self.cleanup.append(previous_version_id)

    async def mark_failed(self, version_id: UUID, code: str) -> None:
        self.failed.append((version_id, code))


class FakeIndex:
    def __init__(self, indexed_count: int) -> None:
        self.indexed_count = indexed_count

    async def count_version(self, version_id: UUID) -> int:
        assert version_id == NEW_VERSION_ID
        return self.indexed_count


async def test_version_activates_only_after_index_count_matches() -> None:
    repository = FakeVersionRepository()

    await VersionActivator(repository, FakeIndex(3)).activate(
        DOCUMENT_ID,
        NEW_VERSION_ID,
        previous_version_id=OLD_VERSION_ID,
        expected_child_count=3,
    )

    assert repository.active_version_id == NEW_VERSION_ID
    assert repository.ready == [NEW_VERSION_ID]
    assert repository.cleanup == [OLD_VERSION_ID]
    assert repository.failed == []


async def test_failed_index_verification_preserves_old_active_version() -> None:
    repository = FakeVersionRepository()

    with pytest.raises(VersionActivationError, match="index_verification_failed"):
        await VersionActivator(repository, FakeIndex(2)).activate(
            DOCUMENT_ID,
            NEW_VERSION_ID,
            previous_version_id=OLD_VERSION_ID,
            expected_child_count=3,
        )

    assert repository.active_version_id == OLD_VERSION_ID
    assert repository.ready == []
    assert repository.cleanup == []
    assert repository.failed == [(NEW_VERSION_ID, "index_verification_failed")]
