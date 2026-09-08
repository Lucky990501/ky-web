from __future__ import annotations

import asyncio
import logging

from app.main import product_store, runtime, store, task_service
from app.settings import settings
from app.task_queue import RedisTaskQueue

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s worker %(message)s")
logger = logging.getLogger(__name__)


async def run() -> None:
    if settings.task_queue != "redis":
        raise RuntimeError("Worker 需要 ENTERPRISE_POC_TASK_QUEUE=redis。")
    store.initialize()
    queue = RedisTaskQueue.from_settings(settings)
    queue.ping()
    queue.recover_processing()
    logger.info("worker ready")
    try:
        while True:
            task_id = await asyncio.to_thread(queue.reserve)
            if not task_id:
                continue
            task = product_store.task_for_worker(task_id)
            if not task or task["status"] in {"completed", "cancelled"}:
                queue.acknowledge(task_id)
                continue
            try:
                await task_service.execute(task)
            finally:
                queue.acknowledge(task_id)
    finally:
        await runtime.close()


if __name__ == "__main__":
    asyncio.run(run())
