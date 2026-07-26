"""Event bus abstraction, transactional outbox, and worker supervision.

Design doc §4.2: domain events are written to the outbox table in the same DB
transaction as the state change (write_outbox); a relay publishes unpublished
rows to the bus and marks them published. Consumers must be idempotent — a
crash between publish and mark-published causes a redelivery.

Worker contract for module agents: a module may define
`get_workers(settings) -> Iterable[Callable]`, where each callable is invoked
as `fn(bus, sessionmaker)` and must return a coroutine. Workers are started
with the outbox relay by run_worker_coroutines and cancelled on shutdown.
"""

from __future__ import annotations

import asyncio
import json
from abc import ABC, abstractmethod
from collections import defaultdict
from collections.abc import AsyncIterator, Callable, Iterable

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.core.models import OutboxEvent
from app.core.timeutil import utcnow

SHUTDOWN_GRACE_SECONDS = 3.0


class EventBus(ABC):
    @abstractmethod
    async def publish(self, stream: str, event: dict) -> None: ...

    @abstractmethod
    async def subscribe(self, stream: str) -> AsyncIterator[dict]:
        """Return an async iterator of events; every subscriber sees every
        event published to the stream after it subscribed (fan-out)."""
        ...

    async def close(self) -> None:
        return None


class InProcessBus(EventBus):
    """Default dev/test bus: fan-out over asyncio queues, no persistence."""

    def __init__(self) -> None:
        self._subscribers: dict[str, set[asyncio.Queue]] = defaultdict(set)

    async def publish(self, stream: str, event: dict) -> None:
        for queue in list(self._subscribers.get(stream, ())):
            queue.put_nowait(event)

    async def subscribe(self, stream: str) -> AsyncIterator[dict]:
        queue: asyncio.Queue = asyncio.Queue()
        self._subscribers[stream].add(queue)
        subscribers = self._subscribers

        async def _gen() -> AsyncIterator[dict]:
            try:
                while True:
                    yield await queue.get()
            finally:
                subscribers[stream].discard(queue)

        return _gen()


class RedisBus(EventBus):
    """Redis Streams bus: XADD on publish, XREAD-from-last on subscribe."""

    def __init__(self, url: str) -> None:
        import redis.asyncio as aioredis

        self._redis = aioredis.from_url(url)

    async def publish(self, stream: str, event: dict) -> None:
        await self._redis.xadd(stream, {"data": json.dumps(event, default=str)})

    async def subscribe(self, stream: str) -> AsyncIterator[dict]:
        redis_client = self._redis
        last_id = "$"  # only new entries

        async def _gen() -> AsyncIterator[dict]:
            nonlocal last_id
            while True:
                response = await redis_client.xread(
                    {stream: last_id}, block=1000, count=100
                )
                if not response:
                    continue
                for _stream, entries in response:
                    for entry_id, fields in entries:
                        last_id = entry_id
                        raw = fields.get(b"data") or fields.get("data")
                        yield json.loads(raw)

        return _gen()

    async def close(self) -> None:
        await self._redis.aclose()


async def write_outbox(
    session: AsyncSession, stream: str, payload: dict
) -> OutboxEvent:
    """Insert an OutboxEvent row in the caller's transaction (flush only)."""
    row = OutboxEvent(stream=stream, payload=payload)
    session.add(row)
    await session.flush()
    return row


async def _relay_batch(
    sessionmaker: async_sessionmaker[AsyncSession],
    bus: EventBus,
    batch_size: int,
) -> int:
    """Publish one batch of unpublished outbox rows; return rows published."""
    published = 0
    async with sessionmaker() as session:
        rows = (
            (
                await session.execute(
                    select(OutboxEvent)
                    .where(OutboxEvent.published_at.is_(None))
                    .order_by(OutboxEvent.id)
                    .limit(batch_size)
                )
            )
            .scalars()
            .all()
        )
        for row in rows:
            await bus.publish(row.stream, row.payload)
            row.published_at = utcnow()
            published += 1
        await session.commit()
    return published


async def outbox_relay(
    sessionmaker: async_sessionmaker[AsyncSession],
    bus: EventBus,
    stop_event: asyncio.Event,
    poll_interval: float = 0.2,
    batch_size: int = 100,
) -> None:
    """Publish unpublished outbox rows to the bus, then mark them published.

    Idempotent at the row level: each row is published and marked in one
    transaction; rows already marked published are never republished.

    The DB batch runs shielded: on cancellation the in-flight batch is
    drained to completion before the CancelledError is re-raised. Cancelling
    mid-aiosqlite-call can otherwise wedge the connection thread and hang
    the whole app shutdown.
    """
    while not stop_event.is_set():
        batch = asyncio.ensure_future(_relay_batch(sessionmaker, bus, batch_size))
        try:
            published = await asyncio.shield(batch)
        except asyncio.CancelledError:
            try:
                await batch
            except Exception:
                pass
            raise
        if not published:
            try:
                await asyncio.wait_for(stop_event.wait(), timeout=poll_interval)
            except TimeoutError:
                pass


async def run_worker_coroutines(
    settings,
    sessionmaker: async_sessionmaker[AsyncSession],
    bus: EventBus,
    worker_fns: Iterable[Callable] = (),
) -> None:
    """Run the outbox relay plus module-provided worker coroutines.

    Each worker fn is called as fn(bus, sessionmaker) and must return a
    coroutine. Intended to be wrapped in asyncio.create_task by the caller;
    cancellation tears down all children gracefully (relay stop event set,
    worker tasks cancelled) and re-raises.
    """
    stop = asyncio.Event()
    tasks = [
        asyncio.create_task(
            outbox_relay(sessionmaker, bus, stop), name="outbox-relay"
        )
    ]
    for fn in worker_fns or ():
        tasks.append(
            asyncio.create_task(
                fn(bus, sessionmaker), name=getattr(fn, "__name__", "module-worker")
            )
        )
    try:
        await asyncio.gather(*tasks)
    except BaseException:
        stop.set()
        for task in tasks:
            task.cancel()
        # Bounded wait: a worker wedged mid-DB-call (e.g. aiosqlite connection
        # lost to a cancelled future) must not hang app shutdown forever.
        _done, pending = await asyncio.wait(tasks, timeout=SHUTDOWN_GRACE_SECONDS)
        for task in pending:
            print(
                f"WARNING: worker {task.get_name()!r} did not stop within "
                f"{SHUTDOWN_GRACE_SECONDS}s; abandoning it during shutdown"
            )
        raise
