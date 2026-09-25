import json
from pathlib import Path

import pytest

from agent_api.knowledge.domain.events import OutboxEventType


def test_outbox_event_type_members_are_complete() -> None:
    assert set(OutboxEventType) == {
        OutboxEventType.DOCUMENT_INGESTION_REQUESTED,
        OutboxEventType.DOCUMENT_DELETION_REQUESTED,
        OutboxEventType.DOCUMENT_VERSION_CLEANUP_REQUESTED,
        OutboxEventType.KNOWLEDGE_BASE_DELETION_REQUESTED,
        OutboxEventType.WIKI_DOCUMENT_GENERATE_REQUESTED,
    }


def test_outbox_event_type_is_json_compatible() -> None:
    encoded = json.dumps({"event_type": OutboxEventType.DOCUMENT_INGESTION_REQUESTED})
    assert json.loads(encoded) == {
        "event_type": OutboxEventType.DOCUMENT_INGESTION_REQUESTED.value
    }


def test_outbox_event_type_rejects_unknown_value() -> None:
    with pytest.raises(ValueError):
        OutboxEventType("unknown.event")


def test_outbox_event_types_have_no_duplicate_python_literals() -> None:
    root = Path(__file__).resolve().parents[1]
    excluded = {
        root / "src/agent_api/knowledge/domain/events.py",
        Path(__file__).resolve(),
    }
    offenders: list[str] = []
    for base in (root / "src/agent_api", root / "tests"):
        for path in base.rglob("*.py"):
            if path in excluded:
                continue
            content = path.read_text(encoding="utf-8")
            for event_type in OutboxEventType:
                if event_type.value in content:
                    offenders.append(f"{path.relative_to(root)}:{event_type.value}")
    assert offenders == []
