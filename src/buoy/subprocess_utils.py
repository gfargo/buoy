from __future__ import annotations

import asyncio
import inspect


def _consume_task_result(task: asyncio.Future) -> None:
    """Consume a detached cleanup task's result to avoid warning noise."""
    if task.cancelled():
        return
    try:
        task.exception()
    except (asyncio.CancelledError, Exception):
        pass


async def _settle(awaitable, timeout: float) -> None:
    """Wait for cleanup without allowing a stuck transport to block forever."""
    task = asyncio.ensure_future(awaitable)
    done, _ = await asyncio.wait({task}, timeout=timeout)
    if task in done:
        _consume_task_result(task)
        return

    task.cancel()
    done, _ = await asyncio.wait({task}, timeout=timeout)
    if task in done:
        _consume_task_result(task)
    else:
        # Some subprocess transports do not complete cancellation while an
        # inherited pipe remains open. Detach rather than defeating the caller's
        # timeout; consume the result if the transport eventually settles.
        task.add_done_callback(_consume_task_result)


async def communicate(proc, timeout, input=None):
    """Communicate with a child process and reliably drain it after cancellation."""
    communication = proc.communicate(input)
    try:
        return await asyncio.wait_for(communication, timeout=timeout)
    except (TimeoutError, asyncio.CancelledError):
        # A patched or externally-cancelled wait_for may not have taken ownership
        # of the communicate coroutine. Close it to avoid leaking an un-awaited
        # coroutine before starting a second communicate call.
        if inspect.iscoroutine(communication):
            communication.close()
        try:
            proc.kill()
        except ProcessLookupError:
            pass

        cleanup_timeout = min(max(float(timeout or 0), 0.1), 1.0)
        try:
            # communicate() drains stdout/stderr while reaping the child. Calling
            # wait() alone can deadlock when a pipe filled before the timeout.
            drain = proc.communicate()
            if inspect.isawaitable(drain):
                await _settle(drain, cleanup_timeout)
        except ProcessLookupError:
            pass
        finally:
            try:
                wait = proc.wait()
                if inspect.isawaitable(wait):
                    await _settle(wait, cleanup_timeout)
            except ProcessLookupError:
                pass
        raise
