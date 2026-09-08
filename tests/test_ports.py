"""Tests for concurrency-safe RTP and RTCP port allocation."""

from __future__ import annotations

import pytest

from wildix_media.errors import MediaCapacityError
from wildix_media.ports import RtpPortPool, SharedRtpPortPool


@pytest.mark.asyncio
async def test_pool_allocates_non_overlapping_rtp_rtcp_pairs() -> None:
    """Each allocated RTP port should leave its adjacent RTCP port unused."""
    pool = RtpPortPool(10_000, 10_003)

    assert await pool.acquire() == 10_000
    assert await pool.acquire() == 10_002
    with pytest.raises(MediaCapacityError):
        await pool.acquire()


@pytest.mark.asyncio
async def test_single_port_tunnel_allows_one_call() -> None:
    """A combined single-port tunnel should retain its one-call behavior."""
    pool = RtpPortPool(5_060, 5_060)

    assert await pool.acquire() == 5_060
    with pytest.raises(MediaCapacityError):
        await pool.acquire()


@pytest.mark.asyncio
async def test_shared_pool_admits_configured_concurrency() -> None:
    """Shared-port capacity should count calls without allocating more ports."""
    pool = SharedRtpPortPool(5_060, capacity=2)

    assert await pool.acquire() == 5_060
    assert await pool.acquire() == 5_060
    with pytest.raises(MediaCapacityError):
        await pool.acquire()
    await pool.release(5_060)
    assert await pool.acquire() == 5_060
