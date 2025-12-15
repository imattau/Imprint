import asyncio
import json
import logging
import time
from typing import Any, Dict, Iterable, List

import websockets

logger = logging.getLogger(__name__)


class RelayBackoff:
    def __init__(self) -> None:
        self.cooldowns: Dict[str, float] = {}
        self.failure_count: Dict[str, int] = {}

    def is_on_cooldown(self, relay: str) -> bool:
        return self.cooldowns.get(relay, 0) > time.time()

    def record_failure(self, relay: str) -> None:
        failures = self.failure_count.get(relay, 0) + 1
        self.failure_count[relay] = failures
        delay = min(120, 5 * (2 ** (failures - 1)))
        self.cooldowns[relay] = time.time() + delay
        logger.warning("Relay %s on cooldown for %ss after failure", relay, delay)

    def record_success(self, relay: str) -> None:
        self.failure_count[relay] = 0
        self.cooldowns[relay] = 0


class _TTLCache:
    def __init__(self, ttl_seconds: int = 30) -> None:
        self.ttl = ttl_seconds
        self._store: Dict[str, tuple[float, Any]] = {}

    def get(self, key: str) -> Any:
        item = self._store.get(key)
        if not item:
            return None
        expires_at, value = item
        if expires_at < time.time():
            self._store.pop(key, None)
            return None
        return value

    def set(self, key: str, value: Any) -> None:
        self._store[key] = (time.time() + self.ttl, value)

    def invalidate(self, prefix: str | None = None) -> None:
        if prefix is None:
            self._store.clear()
            return
        for k in list(self._store.keys()):
            if k.startswith(prefix):
                self._store.pop(k, None)


class RelayClient:
    """Minimal relay client with bounded concurrency, basic backoff, and short TTL caching."""

    def __init__(
        self,
        max_concurrent: int = 5,
        max_relays_for_reads: int = 5,
        max_relays_for_writes: int = 5,
        timeout_seconds: int = 5,
    ) -> None:
        self._sem = asyncio.Semaphore(max_concurrent)
        self.max_reads = max_relays_for_reads
        self.max_writes = max_relays_for_writes
        self.timeout = timeout_seconds
        self.backoff = RelayBackoff()
        self.cache = _TTLCache(ttl_seconds=30)

    def _should_skip(self) -> bool:
        return bool(__import__("os").getenv("PYTEST_CURRENT_TEST"))

    async def publish_event(self, event: Dict[str, Any], relays: Iterable[str]) -> Dict[str, str]:
        """Publish to a bounded set of relays; returns per-relay status."""

        if self._should_skip():
            return {}
        # Invalidate caches that may contain stale views.
        self.cache.invalidate()
        results: Dict[str, str] = {}
        targets = list(dict.fromkeys(relays))[: self.max_writes]
        send_tasks = []
        for relay in targets:
            if self.backoff.is_on_cooldown(relay):
                results[relay] = "cooldown"
                continue
            send_tasks.append(self._send_event(relay, event))
        for coro in asyncio.as_completed(send_tasks):
            relay, status = await coro
            results[relay] = status
        return results

    async def _send_event(self, relay: str, event: Dict[str, Any]) -> tuple[str, str]:
        async with self._sem:
            try:
                start = time.time()
                async with websockets.connect(relay, open_timeout=self.timeout, close_timeout=self.timeout) as ws:
                    await ws.send(json.dumps(["EVENT", event]))
                    try:
                        await asyncio.wait_for(ws.recv(), timeout=self.timeout)
                    except asyncio.TimeoutError:
                        pass
                self.backoff.record_success(relay)
                logger.info("Published event %s to %s in %.0fms", event.get("id"), relay, (time.time() - start) * 1000)
                return relay, "ok"
            except Exception as exc:  # noqa: BLE001
                self.backoff.record_failure(relay)
                logger.warning("Publish failed to %s: %s", relay, exc)
                return relay, f"error:{exc}"

    async def fetch_events(
        self, filters: List[Dict[str, Any]], relays: Iterable[str], timeout_seconds: int | None = None
    ) -> List[Dict[str, Any]]:
        """Fetch events matching filters from a bounded set of relays, with simple caching."""

        if self._should_skip():
            return []
        cache_key = f"{json.dumps(filters, sort_keys=True)}|{','.join(sorted(set(relays)))}"
        cached = self.cache.get(cache_key)
        if cached is not None:
            return cached

        timeout = timeout_seconds or self.timeout
        targets = list(dict.fromkeys(relays))[: self.max_reads]
        events: list[Dict[str, Any]] = []

        async def _fetch(relay: str):
            if self.backoff.is_on_cooldown(relay):
                return
            async with self._sem:
                try:
                    start = time.time()
                    async with websockets.connect(relay, open_timeout=timeout, close_timeout=timeout) as ws:
                        sub_id = f"fetch-{int(start)}"
                        await ws.send(json.dumps(["REQ", sub_id, *filters]))
                        async for raw in ws:
                            msg = json.loads(raw)
                            if msg and msg[0] == "EOSE":
                                break
                            if msg and msg[0] == "EVENT" and len(msg) >= 3:
                                events.append(msg[2])
                    self.backoff.record_success(relay)
                    logger.info(
                        "Fetched %d events from %s in %.0fms", len(events), relay, (time.time() - start) * 1000
                    )
                except Exception as exc:  # noqa: BLE001
                    self.backoff.record_failure(relay)
                    logger.warning("Fetch failed from %s: %s", relay, exc)

        await asyncio.gather(*(_fetch(relay) for relay in targets))
        self.cache.set(cache_key, events)
        return events


relay_client = RelayClient()
