import json
from pathlib import Path

from agent_api.knowledge.domain.events import OutboxEventType


def test_outbox_event_type_values_are_protocol_stable() -> None:
    assert {event.value for event in OutboxEventType} == {
        "document.ingestion.requested",
        "document.deletion.requested",
        "document.version.cleanup.requested",
        "knowledge_base.deletion.requested",
        "wiki.document.generate.requested",
    }


def test_outbox_event_type_is_json_compatible() -> None:
    encoded = json.dumps({"event_type": OutboxEventType.DOCUMENT_INGESTION_REQUESTED})
    assert json.loads(encoded) == {"event_type": "document.ingestion.requested"}


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
                if f'"{event_type.value}"' in content or f"'{event_type.value}'" in content:
                    offenders.append(f"{path.relative_to(root)}:{event_type.value}")
    assert offenders == []
