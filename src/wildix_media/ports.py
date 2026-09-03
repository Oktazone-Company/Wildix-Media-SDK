"""Concurrency-safe allocation of configured RTP listener ports."""

from __future__ import annotations

import asyncio
from collections import deque

from wildix_media.errors import MediaCapacityError


class RtpPortPool:
    """Allocate one exclusive UDP media port to each active call."""

    def __init__(self, start: int, end: int) -> None:
        """Initialize a pool from an inclusive port range.

        Args:
            start: First port available for allocation.
            end: Last port available for allocation.
        """
        self._available = deque(range(start, end + 1))
        self._in_use: set[int] = set()
        self._lock = asyncio.Lock()

    async def acquire(self) -> int:
        """Reserve and return one RTP port.

        Returns:
            An unused port from the configured range.

        Raises:
            MediaCapacityError: If no port remains available.

        Side effects:
            Marks the returned port as in use until ``release`` is called.
        """
        async with self._lock:
            if not self._available:
                raise MediaCapacityError("No RTP ports are available for another call")
            port = self._available.popleft()
            self._in_use.add(port)
            return port

    async def release(self, port: int) -> None:
        """Return a previously acquired RTP port to the pool.

        Args:
            port: Port returned by ``acquire``.

        Returns:
            ``None``.

        Side effects:
            Makes the port available to a future call. Unknown ports are ignored.
        """
        async with self._lock:
            if port not in self._in_use:
                return
            self._in_use.remove(port)
            self._available.append(port)
