"""Concurrency-safe allocation of configured RTP listener ports."""

from __future__ import annotations

import asyncio
from collections import deque

from wildix_media.errors import MediaCapacityError


class RtpPortPool:
    """Allocate one non-overlapping RTP/RTCP UDP port pair per active call."""

    def __init__(self, start: int, end: int) -> None:
        """Initialize a pool from an inclusive port range.

        Args:
            start: First port available for allocation.
            end: Last port available for allocation.
        """
        # aiortp binds RTCP on RTP + 1, so adjacent RTP allocations collide.
        ports = (start,) if start == end else range(start, end, 2)
        self._available = deque(ports)
        self._in_use: set[int] = set()
        self._lock = asyncio.Lock()

    async def acquire(self) -> int:
        """Reserve and return one RTP port.

        Returns:
            An unused RTP port whose adjacent RTCP port is also reserved.

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


class SharedRtpPortPool:
    """Limit concurrent calls that intentionally share one UDP RTP port."""

    def __init__(self, port: int, capacity: int) -> None:
        """Initialize shared-port capacity tracking.

        Args:
            port: Local UDP port returned for every admitted call.
            capacity: Maximum number of simultaneous leases.
        """
        self._port = port
        self._capacity = capacity
        self._in_use = 0
        self._lock = asyncio.Lock()

    async def acquire(self) -> int:
        """Admit one call and return the shared RTP port.

        Returns:
            Shared local UDP port.

        Raises:
            MediaCapacityError: If all shared call slots are occupied.
        """
        async with self._lock:
            if self._in_use >= self._capacity:
                raise MediaCapacityError("No shared RTP capacity remains")
            self._in_use += 1
            return self._port

    async def release(self, port: int) -> None:
        """Release one shared call slot.

        Args:
            port: Shared port returned by ``acquire``.

        Returns:
            ``None``. A mismatched port or empty pool is ignored.
        """
        async with self._lock:
            if port != self._port or self._in_use == 0:
                return
            self._in_use -= 1
