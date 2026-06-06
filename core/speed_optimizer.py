# core/speed_optimizer.py
"""
Enhancement 4 — Speed Optimizer (< 2 s detection target)

Provides:
  - TTL-based in-memory caching with automatic eviction
  - asyncio.wait_for() timeout wrappers (never crash on slow APIs)
  - Parallel scan orchestration for all enrichment tasks
  - Rolling average processing-time tracking with slow-path warnings
"""

import asyncio
import time
from typing import Any, Callable, Coroutine, Optional

from loguru import logger

from config.settings import settings


# ---------------------------------------------------------------------------
# Sentinel coroutine — returned for disabled/None tasks so asyncio.gather
# always has a valid awaitable (asyncio.coroutine was removed in Python 3.11)
# ---------------------------------------------------------------------------
async def _noop() -> None:
    """Awaitable no-op used as a placeholder for disabled pipeline tasks."""
    return None


class SpeedOptimizer:
    """
    Centralised speed-optimisation layer for the signal pipeline.

    Typical usage
    -------------
    ::

        optimizer = SpeedOptimizer()

        results = await optimizer.parallel_scan(
            token_address,
            token_info,
            security_fn=lambda: full_security_scan(token_address),
            wallet_fn=lambda: wallet_analyzer.analyze_wallet(token_address, ts),
            social_fn=lambda: social_scanner.analyze_social(symbol, token_address),
            rug_fn=lambda: rug_detector.analyze_rug_risk(token_address, token_info),
            analysis_fn=lambda: analyze_token(token_info),
        )
        # results["security"], results["wallet"], etc. — None when timed out
    """

    def __init__(self) -> None:
        # Unified KV cache — key → cached value
        self._cache: dict[str, Any] = {}

        # Cache TTL in seconds (default: 5 minutes)
        self._cache_ttl: int = 300

        # Monotonic timestamps used for TTL eviction
        self._cache_timestamps: dict[str, float] = {}

        # Bounded work queue (callers can push token addresses here)
        self._processing_queue: asyncio.Queue = asyncio.Queue(maxsize=50)

        # Rolling window of the last 100 end-to-end processing times (seconds)
        self._processing_times: list[float] = []

        # Per-task default timeout — overridden per-task in parallel_scan
        self._default_timeout: float = float(
            getattr(settings, "MAX_PROCESSING_TIME", 8.0)
        )

    # ------------------------------------------------------------------
    # Timeout wrapper
    # ------------------------------------------------------------------

    async def process_with_timeout(
        self,
        coro: Coroutine,
        timeout: float = 8.0,
        default: Any = None,
    ) -> Any:
        """
        Await *coro* with a hard deadline of *timeout* seconds.

        Returns *default* on timeout or on any unhandled exception, logging
        a warning in both cases.  This method itself never raises.
        """
        try:
            return await asyncio.wait_for(coro, timeout=timeout)
        except asyncio.TimeoutError:
            logger.warning(
                f"Task timed out after {timeout:.1f}s — "
                f"returning default value ({default!r})"
            )
            return default
        except Exception as exc:
            logger.error(
                f"Task raised an unexpected error: {exc!r} — "
                f"returning default value ({default!r})"
            )
            return default

    # ------------------------------------------------------------------
    # Cache helpers
    # ------------------------------------------------------------------

    def _evict_stale(self) -> None:
        """Remove all cache entries older than ``_cache_ttl`` seconds."""
        now = time.monotonic()
        stale = [
            k for k, ts in self._cache_timestamps.items()
            if (now - ts) > self._cache_ttl
        ]
        for k in stale:
            self._cache.pop(k, None)
            del self._cache_timestamps[k]

    def cache_result(self, key: str, value: Any) -> None:
        """
        Store *value* under *key* with the current timestamp.
        Stale entries are evicted on every write.
        """
        self._evict_stale()
        self._cache[key] = value
        self._cache_timestamps[key] = time.monotonic()

    def get_cached(self, key: str) -> Optional[Any]:
        """
        Return the cached value for *key* if it has not expired, else ``None``.
        """
        self._evict_stale()
        return self._cache.get(key)

    # ------------------------------------------------------------------
    # Parallel scan
    # ------------------------------------------------------------------

    async def parallel_scan(
        self,
        token_address: str,
        token_info: Any,
        *,
        security_fn:        Optional[Callable[[], Coroutine]] = None,
        wallet_fn:          Optional[Callable[[], Coroutine]] = None,
        social_fn:          Optional[Callable[[], Coroutine]] = None,
        rug_fn:             Optional[Callable[[], Coroutine]] = None,
        analysis_fn:        Optional[Callable[[], Coroutine]] = None,
        holder_velocity_fn: Optional[Callable[[], Coroutine]] = None,
        smart_money_fn:     Optional[Callable[[], Coroutine]] = None,
        tx_pattern_fn:      Optional[Callable[[], Coroutine]] = None,
        liquidity_growth_fn: Optional[Callable[[], Coroutine]] = None,
        cross_dex_fn:       Optional[Callable[[], Coroutine]] = None,
    ) -> dict[str, Any]:
        """
        Execute all enrichment tasks concurrently with individual timeouts.

        Each ``*_fn`` parameter must be a **zero-argument callable** that
        returns a coroutine when called (i.e. a lambda or ``functools.partial``).
        Pass ``None`` for any task you want to skip — its result will be ``None``.

        Per-task timeouts
        -----------------
        =========  =======
        Task       Timeout
        =========  =======
        security   6.0 s
        wallet     5.0 s
        social     4.0 s
        rug        5.0 s
        analysis   5.0 s
        =========  =======

        Returns
        -------
        dict with keys ``security``, ``wallet``, ``social``, ``rug``,
        ``analysis``.  Each value is the task result or ``None`` when the
        task was skipped or timed out.
        """
        timeouts: dict[str, float] = {
            "security":         6.0,
            "wallet":           5.0,
            "social":           4.0,
            "rug":              5.0,
            "analysis":         5.0,
            "holder_velocity":  5.0,
            "smart_money":      6.0,
            "tx_pattern":       5.0,
            "liquidity_growth": 4.0,
            "cross_dex":        4.0,
        }

        fns: dict[str, Optional[Callable[[], Coroutine]]] = {
            "security":         security_fn,
            "wallet":           wallet_fn,
            "social":           social_fn,
            "rug":              rug_fn,
            "analysis":         analysis_fn,
            "holder_velocity":  holder_velocity_fn,
            "smart_money":      smart_money_fn,
            "tx_pattern":       tx_pattern_fn,
            "liquidity_growth": liquidity_growth_fn,
            "cross_dex":        cross_dex_fn,
        }

        # Build one awaitable per slot — _noop() for disabled tasks
        awaitables: list[Coroutine] = [
            self.process_with_timeout(fn(), timeout=timeouts[name], default=None)
            if fn is not None
            else _noop()
            for name, fn in fns.items()
        ]

        results_list = await asyncio.gather(*awaitables, return_exceptions=False)
        results: dict[str, Any] = dict(zip(fns.keys(), results_list))

        logger.debug(
            f"parallel_scan {token_address}: "
            + ", ".join(
                f"{k}={'OK' if v is not None else 'SKIP/TIMEOUT'}"
                for k, v in results.items()
            )
        )
        return results

    # ------------------------------------------------------------------
    # Performance tracking
    # ------------------------------------------------------------------

    def get_avg_processing_time(self) -> float:
        """Return the rolling average of the last 100 processing times (seconds)."""
        if not self._processing_times:
            return 0.0
        return sum(self._processing_times) / len(self._processing_times)

    def record_processing_time(self, start_time: float) -> None:
        """
        Record the elapsed time since *start_time* (a ``time.monotonic()``
        value) and log a warning when the rolling average exceeds 3 seconds.

        This is a *synchronous* method — no ``await`` needed.
        """
        elapsed = time.monotonic() - start_time
        self._processing_times.append(elapsed)

        # Keep the window capped at 100 samples
        if len(self._processing_times) > 100:
            self._processing_times = self._processing_times[-100:]

        avg = self.get_avg_processing_time()
        logger.debug(f"Pipeline time: {elapsed:.3f}s | rolling avg: {avg:.3f}s")

        if avg > 3.0:
            logger.warning(
                f"⚠️  Avg pipeline time {avg:.2f}s exceeds 3.0s target. "
                "Consider reducing API timeouts or disabling non-critical enrichments."
            )
