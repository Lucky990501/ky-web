"""No Provider: stalled startup, cancellation, timeout and success."""
import asyncio
import pytest
from app.preview_runtime_start import guarded_start
from app.service import GenerationCancelled
from app.runtime.base import RuntimeStartError
from app.preview_budget import preview_task_failure


def test_start_timeout_cleans_pending_operation_and_releases_lock():
    async def run():
        lock=asyncio.Lock();closed=[];events=[]
        async def stuck():
            async with lock:
                try:await asyncio.Future()
                finally:closed.append(True)
        with pytest.raises(RuntimeStartError):
            await guarded_start(stuck(),lambda:False,events.append,timeout=.01)
        assert closed==[True] and not lock.locked()
        assert events==['preview_start_timeout']
        assert preview_task_failure({}, {'lifecycle_events':[{'event':events[0]}]},None)=='PREVIEW_RUNTIME_START_TIMEOUT'
    asyncio.run(run())


def test_cancel_while_starting_does_not_wait_for_sdk_or_dispatch_turn():
    async def run():
        stop=False;closed=[]
        async def stuck():
            nonlocal stop
            stop=True
            try:await asyncio.Future()
            finally:closed.append(True)
        with pytest.raises(GenerationCancelled):
            await guarded_start(stuck(),lambda:stop,lambda _:None,timeout=2)
        assert closed==[True]
    asyncio.run(run())


def test_completed_start_and_start_error_are_not_retried():
    async def run():
        calls=[]
        async def result():calls.append(1);return 'session'
        assert await guarded_start(result(),lambda:False,lambda _:None)=='session'
        async def failure():calls.append(1);raise RuntimeStartError('thread_start')
        with pytest.raises(RuntimeStartError):await guarded_start(failure(),lambda:False,lambda _:None)
        assert len(calls)==2
    asyncio.run(run())
