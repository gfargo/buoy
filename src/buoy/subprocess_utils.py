from __future__ import annotations

import asyncio


async def communicate(proc, timeout, input=None):
    """proc.communicate() with a timeout that kills+reaps the child on expiry."""
    try:
        return await asyncio.wait_for(proc.communicate(input), timeout=timeout)
    except (TimeoutError, asyncio.CancelledError):
        try:
            proc.kill()
        except ProcessLookupError:
            pass
        try:
            await proc.wait()
        except ProcessLookupError:
            pass
        raise
