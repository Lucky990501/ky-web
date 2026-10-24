"""Bound Preview startup, including cancellation before a turn exists."""
import asyncio
from app.runtime.base import RuntimeStartError
from app.service import GenerationCancelled

START_TIMEOUT_SECONDS = 60


async def guarded_start(operation, cancelled, stage_event, *, timeout=START_TIMEOUT_SECONDS):
    pending = asyncio.ensure_future(operation)
    deadline = asyncio.get_running_loop().time() + timeout
    try:
        while True:
            if cancelled():
                stage_event('preview_start_cancelled')
                raise GenerationCancelled()
            remaining = deadline - asyncio.get_running_loop().time()
            if remaining <= 0:
                stage_event('preview_start_timeout')
                raise RuntimeStartError('preview_start_timeout')
            done, _ = await asyncio.wait({pending}, timeout=min(.2, remaining))
            if done:
                result = pending.result()
                if cancelled():
                    stage_event('preview_start_cancelled')
                    raise GenerationCancelled()
                return result
    finally:
        if not pending.done():
            pending.cancel()
            done, _ = await asyncio.wait({pending}, timeout=3)
            if done:
                try:pending.result()
                except BaseException:pass
            else:
                pending.add_done_callback(lambda task: None if task.cancelled() else task.exception())
