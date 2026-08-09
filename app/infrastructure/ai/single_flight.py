"""Best-effort deduplication of concurrent generations of the same word.

A learner looking up "abandon" while a deck build reaches "abandon" is the normal
case, and without this both pay a provider. The cost of losing that race is one
duplicated call — never a duplicated sense, because the lexicon's unique
constraints are what guarantee *correctness*. This module only saves money.

That division matters, and it is why the lock lives in Redis rather than in
Postgres. ``pg_advisory_xact_lock`` would hold a database connection for the
whole multi-second provider call; under a 500-word build plus ordinary traffic
that exhausts the pool, and a slow provider becomes a site-wide outage. A lock
around something slow belongs outside the database.

**Never load-bearing.** An unreachable Redis, a timeout, a lock we cannot take —
all mean "go ahead and generate". This can waste money; it can never block a
learner or fail a request.
"""

from __future__ import annotations

import asyncio
import time
from typing import Any, Protocol

from app.core.logging import get_logger

logger = get_logger("vocably.ai.singleflight")

#: How long a holder's claim lasts if it never releases. Comfortably longer than
#: a provider call and short enough that a killed worker does not block a word
#: for long. There is no renewal: a lease that outlives the work is the failure
#: mode a lock without one cannot have.
DEFAULT_LEASE_SECONDS = 90

#: How long a loser waits for the winner's answer before generating anyway.
#: Bounded low, because the learner is watching a spinner: paying twice beats
#: making one of them wait for the other's slow call.
DEFAULT_WAIT_SECONDS = 3.0
_POLL_INTERVAL = 0.25

#: Hard ceiling on a single Redis round trip. "Best-effort" has to mean *fast*
#: as well as non-fatal: a client that retries a refused connection with backoff
#: turns an optimisation into seconds of latency on every word, which measured
#: at ~11s per word on a 500-word build with Redis down. An exception is caught;
#: a slow success is not, unless it is bounded here.
OP_TIMEOUT_SECONDS = 1.0


class AsyncRedisLike(Protocol):
    async def set(self, name: str, value: str, *, nx: bool = ..., ex: int = ...) -> Any: ...
    async def delete(self, *names: str) -> Any: ...


class SingleFlight:
    """A named, leased, best-effort claim on generating one thing."""

    def __init__(
        self,
        redis: AsyncRedisLike | None,
        *,
        namespace: str = "lexgen",
        lease_seconds: int = DEFAULT_LEASE_SECONDS,
        wait_seconds: float = DEFAULT_WAIT_SECONDS,
        op_timeout_seconds: float = OP_TIMEOUT_SECONDS,
    ) -> None:
        self._redis = redis
        self._namespace = namespace
        self._lease = lease_seconds
        self._wait = wait_seconds
        self._op_timeout = op_timeout_seconds
        #: Flipped permanently by the first failure. See :meth:`_give_up`.
        self._disabled = False

    async def acquire(self, key: str) -> bool:
        """``True`` if this caller should generate.

        Also ``True`` when Redis is unreachable — degrading to "everybody
        generates" is the only safe direction.
        """
        if self._redis is None or self._disabled:
            return True
        try:
            taken = await asyncio.wait_for(
                self._redis.set(self._key(key), "1", nx=True, ex=self._lease),
                timeout=self._op_timeout,
            )
        except (Exception, TimeoutError) as exc:  # noqa: BLE001 — never fatal
            self._give_up(exc)
            return True
        return bool(taken)

    async def release(self, key: str) -> None:
        if self._redis is None or self._disabled:
            return
        try:
            await asyncio.wait_for(self._redis.delete(self._key(key)), timeout=self._op_timeout)
        except (Exception, TimeoutError) as exc:  # noqa: BLE001
            self._give_up(exc)

    def _give_up(self, exc: BaseException) -> None:
        """Stop using Redis for the rest of this process, after one failure.

        Retrying is worse than not trying, for two measured reasons. A refused
        connection costs real latency on *every* word — a 500-word build with
        Redis down ran at roughly eleven seconds per word before this. And a
        command cancelled by the timeout above leaves its pooled connection in an
        indeterminate state, after which the next call can block indefinitely
        waiting for a healthy one; that is a hang, in a component whose entire
        contract is that it cannot break anything.

        One strike is therefore the right number. The cost of giving up is that
        two workers may buy the same word twice — money, never correctness, which
        the lexicon's unique constraints guarantee regardless.
        """
        self._disabled = True
        logger.warning(
            "single-flight disabled for this process after %s; duplicate "
            "generations are possible but nothing is at risk",
            type(exc).__name__,
        )

    async def wait_for_result(self, poll: ResultPoller) -> object | None:
        """Give the winner a bounded moment to publish, then give up.

        Deliberately a poll rather than a subscription: the thing being waited
        for lands in Postgres, not in Redis, so there is nothing to subscribe to
        without inventing a second notification path that can also fail.
        """
        deadline = time.monotonic() + self._wait
        while time.monotonic() < deadline:
            await asyncio.sleep(_POLL_INTERVAL)
            found = await poll()
            if found is not None:
                return found
        return None

    def _key(self, key: str) -> str:
        return f"{self._namespace}:{key}"


class ResultPoller(Protocol):
    async def __call__(self) -> object | None: ...
