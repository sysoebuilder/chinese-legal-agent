"""Celery 只接收 run_id 和投递代次，不传 API Key 或完整对话。"""

from celery import Celery

from chinese_legal_agent.api.settings import ApiSettings


def create_celery(settings: ApiSettings) -> Celery:
    app = Celery(
        "agent100", broker=settings.redis_url, include=["chinese_legal_agent.api.worker"]
    )
    app.conf.update(
        task_default_queue="agent100-runs",
        task_serializer="json",
        accept_content=["json"],
        task_ignore_result=True,
        task_acks_late=True,
        task_reject_on_worker_lost=True,
        worker_prefetch_multiplier=1,
        broker_connection_retry_on_startup=True,
        task_publish_retry=False,
        broker_transport_options={
            "visibility_timeout": settings.visibility_timeout,
            "socket_connect_timeout": 2,
            "socket_timeout": 2,
        },
        visibility_timeout=settings.visibility_timeout,
        beat_schedule={
            "dispatch-persisted-runs": {"task": "agent100.dispatch", "schedule": 5.0},
        },
    )
    return app


celery_app = create_celery(ApiSettings.from_env())


class CeleryPublisher:
    def __init__(self, app: Celery = celery_app) -> None:
        self.app = app

    def publish(self, run_id: str, generation: int) -> None:
        self.app.send_task("agent100.execute", args=[run_id, generation], retry=False)


def dispatch_pending(store, publisher) -> int:
    """先成功发布再清除入队意图；崩溃窗口可能重复投递，由 worker 去重。"""
    published = 0
    for row in store.pending_dispatches():
        publisher.publish(row["run_id"], row["generation"])
        store.dispatched(row["run_id"], row["generation"])
        published += 1
    return published
