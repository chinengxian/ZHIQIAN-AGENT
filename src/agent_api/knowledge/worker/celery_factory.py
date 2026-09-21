from __future__ import annotations

from celery import Celery  # type: ignore[import-untyped]

from agent_api.core.config import Settings


def create_celery_app(settings: Settings) -> Celery:
    """构造只接受 JSON、延迟确认且定期扫描 outbox 的 Celery 应用。"""

    app = Celery(
        "agent_api.knowledge",
        broker=settings.redis_url.get_secret_value(),
        include=["agent_api.knowledge.worker.celery_app"],
    )
    app.conf.update(
        task_serializer="json",
        accept_content=["json"],
        result_backend=None,
        task_acks_late=True,
        task_reject_on_worker_lost=True,
        task_default_queue="knowledge",
        worker_prefetch_multiplier=1,
        beat_schedule={
            "dispatch-knowledge-outbox": {
                "task": "agent_api.knowledge.dispatch_outbox",
                "schedule": 2.0,
            },
            "recover-stale-ingestion-jobs": {
                "task": "agent_api.knowledge.recover_stale_jobs",
                "schedule": 60.0,
            },
        },
    )
    return app
