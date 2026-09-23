import json

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
