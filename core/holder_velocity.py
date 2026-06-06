# core/holder_velocity.py
"""
Phase 1.2 — Holder Velocity Tracker

Tracks how quickly a token accumulates unique holders after pool creation.
Fast holder growth is a reliable leading indicator of genuine retail interest.
A sudden *drop* in holder count is a very early rug warning.

Two operating modes
-------------------
quick_analyze(token_address)
    Single-shot analysis callable from the main signal pipeline.
    Makes one Birdeye holder-count call, estimates velocity from token age,
    and returns immediately (< 2 s).  Suitable for the 8-second budget.

start_background_tracking(token_addresses, interval_s=15)
    Continuous background poller.  Stores timestamped snapshots every
    ``interval_s`` seconds.  Subsequent calls to quick_analyze will prefer
    the cached velocity derived from real measurements.
"""

import asyncio
import time
from collections import deque
from dataclasses import dataclass
from typing import Optional

import httpx
from loguru import logger

from config.settings import settings

# ---------------------------------------------------------------------------
# Thresholds (all rates are holders per minute)
# ---------------------------------------------------------------------------
_STRONG_VELOCITY:   float = 50.0   # >50/min  → STRONG
_MODERATE_VELOCITY: float = 20.0   # >20/min  → MODERATE
_SLOW_VELOCITY:     float = 5.0    # > 5/min  → SLOW
# A drop of >10% from the peak holder count triggers a rug warning
_RUG_DROP_THRESHOLD: float = 0.10

# Max snapshots kept per token in the background cache
_MAX_SNAPSHOTS: int = 40  # 40 × 15 s = 10 minutes of history


# ---------------------------------------------------------------------------
# Data classes
# ---------------------------------------------------------------------------

@dataclass
class _HolderSnapshot:
    """Internal: single timestamped holder-count measurement."""
    ts: float        # time.monotonic()
    wall_ts: float   # time.time()
    count: int


@dataclass
class HolderVelocityResult:
    token_address:         str
    current_holders:       int
    holder_change:         int         # net change from previous snapshot (or 0)
    holders_per_minute:    float       # instantaneous velocity
    peak_holders:          int         # highest count seen in the session
    velocity_label:        str         # "STRONG" | "MODERATE" | "SLOW" | "STAGNANT" | "DROPPING"
    is_rug_warning:        bool        # sudden holder drop > 10% from peak
    holder_velocity_score: float       # 0–100
    data_source:           str         # "birdeye" | "rpc_estimate" | "cache"


# ---------------------------------------------------------------------------
# Tracker
# ---------------------------------------------------------------------------

class HolderVelocityTracker:
    """
    Tracks holder growth for multiple tokens.

    All public methods are exception-safe — they always return a valid
    ``HolderVelocityResult`` even when every API call fails.
    """

    def __init__(self) -> None:
        self._birdeye_key: str = getattr(settings, "BIRDEYE_API_KEY", "")
        self._rpc_url: str = settings.SOLANA_RPC_URL
        # token_address → deque of _HolderSnapshot
        self._snapshots: dict[str, deque] = {}
        # token_address → peak holder count
        self._peaks: dict[str, int] = {}
        # background tracking set
        self._tracking: set[str] = set()

    # ------------------------------------------------------------------
    # API helpers
    # ------------------------------------------------------------------

    def _has_birdeye(self) -> bool:
        return bool(
            self._birdeye_key
            and not self._birdeye_key.startswith("your_")
        )

    async def _fetch_holder_count_birdeye(self, token_address: str) -> Optional[int]:
        """
        Fetch total unique holder count from Birdeye v3 API.
        Returns None on any failure.
        """
        if not self._has_birdeye():
            return None
        url = (
            f"https://public-api.birdeye.so/defi/v3/token/holder"
            f"?address={token_address}&offset=0&limit=1"
        )
        headers = {"x-chain": "solana", "X-API-KEY": self._birdeye_key}
        try:
            async with httpx.AsyncClient(timeout=8.0) as client:
                resp = await client.get(url, headers=headers)
                if resp.status_code == 200:
                    data = resp.json()
                    total: Optional[int] = (
                        data.get("data", {}).get("total")
                        if isinstance(data.get("data"), dict)
                        else None
                    )
                    if isinstance(total, int):
                        logger.debug(
                            f"Birdeye holder count for {token_address[:12]}...: {total}"
                        )
                        return total
                elif resp.status_code == 429:
                    logger.warning("Birdeye holder API rate-limited.")
                else:
                    logger.warning(
                        f"Birdeye holder API HTTP {resp.status_code} for {token_address[:12]}..."
                    )
        except Exception as exc:
            logger.error(f"_fetch_holder_count_birdeye error: {exc}")
        return None

    async def _fetch_holder_count_rpc(self, token_address: str) -> Optional[int]:
        """
        Estimate holder count from ``getTokenLargestAccounts`` (free RPC fallback).
        Returns the count of accounts with a non-zero balance (max 20).
        This is a lower-bound — useful for scoring relative to a baseline.
        """
        payload = {
            "jsonrpc": "2.0",
            "id": 1,
            "method": "getTokenLargestAccounts",
            "params": [token_address],
        }
        try:
            async with httpx.AsyncClient(timeout=8.0) as client:
                resp = await client.post(self._rpc_url, json=payload)
                if resp.status_code == 200:
                    data = resp.json()
                    if "error" in data:
                        return None
                    accounts: list[dict] = (
                        data.get("result", {}).get("value", []) or []
                    )
                    nonzero = sum(
                        1 for a in accounts if float(a.get("amount", 0)) > 0
                    )
                    logger.debug(
                        f"RPC holder estimate for {token_address[:12]}...: {nonzero}"
                    )
                    return nonzero
        except Exception as exc:
            logger.error(f"_fetch_holder_count_rpc error: {exc}")
        return None

    async def _fetch_holder_count(self, token_address: str) -> tuple[int, str]:
        """
        Fetch holder count from the best available source.
        Returns ``(count, source_label)``.
        """
        count = await self._fetch_holder_count_birdeye(token_address)
        if count is not None:
            return count, "birdeye"

        count = await self._fetch_holder_count_rpc(token_address)
        if count is not None:
            return count, "rpc_estimate"

        return 0, "unavailable"

    # ------------------------------------------------------------------
    # Scoring
    # ------------------------------------------------------------------

    @staticmethod
    def _score(velocity: float, is_rug_warning: bool) -> float:
        """Convert holders/minute to a 0–100 score."""
        if is_rug_warning:
            return 0.0
        if velocity >= _STRONG_VELOCITY:
            return 100.0
        if velocity >= _MODERATE_VELOCITY:
            # Linear scale: 20 → 60 pts, 50 → 100 pts
            return 60.0 + (velocity - _MODERATE_VELOCITY) / (_STRONG_VELOCITY - _MODERATE_VELOCITY) * 40.0
        if velocity >= _SLOW_VELOCITY:
            # Linear scale: 5 → 20 pts, 20 → 60 pts
            return 20.0 + (velocity - _SLOW_VELOCITY) / (_MODERATE_VELOCITY - _SLOW_VELOCITY) * 40.0
        if velocity > 0:
            return max(5.0, velocity / _SLOW_VELOCITY * 20.0)
        return 5.0  # stagnant but not dropping

    @staticmethod
    def _label(velocity: float, is_rug_warning: bool) -> str:
        if is_rug_warning:
            return "DROPPING"
        if velocity >= _STRONG_VELOCITY:
            return "STRONG"
        if velocity >= _MODERATE_VELOCITY:
            return "MODERATE"
        if velocity > 0:
            return "SLOW"
        return "STAGNANT"

    # ------------------------------------------------------------------
    # Snapshot management
    # ------------------------------------------------------------------

    def _add_snapshot(self, token_address: str, count: int) -> None:
        if token_address not in self._snapshots:
            self._snapshots[token_address] = deque(maxlen=_MAX_SNAPSHOTS)
            self._peaks[token_address] = 0
        self._snapshots[token_address].append(
            _HolderSnapshot(ts=time.monotonic(), wall_ts=time.time(), count=count)
        )
        if count > self._peaks[token_address]:
            self._peaks[token_address] = count

    def _compute_velocity_from_cache(self, token_address: str) -> Optional[tuple[float, int, int]]:
        """
        Derive velocity from cached snapshots.
        Returns ``(holders_per_minute, holder_change, peak)`` or None if
        fewer than 2 snapshots are available.
        """
        snaps = self._snapshots.get(token_address)
        if not snaps or len(snaps) < 2:
            return None
        oldest = snaps[0]
        newest = snaps[-1]
        elapsed_minutes = (newest.ts - oldest.ts) / 60.0
        if elapsed_minutes <= 0:
            return None
        velocity = (newest.count - oldest.count) / elapsed_minutes
        change = newest.count - snaps[-2].count if len(snaps) >= 2 else 0
        return velocity, change, self._peaks.get(token_address, newest.count)

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    async def quick_analyze(self, token_address: str) -> HolderVelocityResult:
        """
        Single-shot holder velocity analysis.

        1. If background snapshots are available, derive velocity from them.
        2. Otherwise, fetch the current holder count and estimate velocity
           from how long the token has existed (using RPC signature history).
        3. Returns a safe default on total failure.
        """
        _safe = HolderVelocityResult(
            token_address=token_address,
            current_holders=0,
            holder_change=0,
            holders_per_minute=0.0,
            peak_holders=0,
            velocity_label="STAGNANT",
            is_rug_warning=False,
            holder_velocity_score=10.0,  # neutral — not penalised, not rewarded
            data_source="unavailable",
        )
        try:
            # ── Try cached velocity first (from background tracker) ────
            cached = self._compute_velocity_from_cache(token_address)
            if cached is not None:
                velocity, change, peak = cached
                snaps = self._snapshots[token_address]
                current = snaps[-1].count if snaps else 0
                is_rug = current < peak * (1.0 - _RUG_DROP_THRESHOLD) and peak > 10
                score = self._score(max(0.0, velocity), is_rug)
                return HolderVelocityResult(
                    token_address=token_address,
                    current_holders=current,
                    holder_change=change,
                    holders_per_minute=round(max(0.0, velocity), 2),
                    peak_holders=peak,
                    velocity_label=self._label(max(0.0, velocity), is_rug),
                    is_rug_warning=is_rug,
                    holder_velocity_score=round(score, 2),
                    data_source="cache",
                )

            # ── Fresh API call ─────────────────────────────────────────
            count, source = await self._fetch_holder_count(token_address)
            if count == 0:
                return _safe

            self._add_snapshot(token_address, count)
            peak = self._peaks.get(token_address, count)
            is_rug = count < peak * (1.0 - _RUG_DROP_THRESHOLD) and peak > 10

            # Estimate token age from signature history to derive velocity
            velocity = 0.0
            age_minutes = await self._estimate_token_age_minutes(token_address)
            if age_minutes > 0 and count > 1:
                # Assume roughly linear accumulation from zero
                velocity = max(0.0, (count - 1) / age_minutes)

            score = self._score(velocity, is_rug)
            result = HolderVelocityResult(
                token_address=token_address,
                current_holders=count,
                holder_change=0,
                holders_per_minute=round(velocity, 2),
                peak_holders=peak,
                velocity_label=self._label(velocity, is_rug),
                is_rug_warning=is_rug,
                holder_velocity_score=round(score, 2),
                data_source=source,
            )
            logger.info(
                f"HolderVelocity for {token_address[:12]}...: "
                f"holders={count}, vel={velocity:.1f}/min, "
                f"label={result.velocity_label}, score={score:.1f}"
            )
            return result

        except Exception as exc:
            logger.error(f"quick_analyze error for {token_address[:12]}...: {exc}")
            return _safe

    async def _estimate_token_age_minutes(self, token_address: str) -> float:
        """
        Estimate token age in minutes from the oldest transaction signature.
        Returns 1.0 (one minute) as a safe minimum to avoid division by zero.
        """
        payload = {
            "jsonrpc": "2.0",
            "id": 1,
            "method": "getSignaturesForAddress",
            "params": [token_address, {"limit": 1000, "commitment": "confirmed"}],
        }
        try:
            async with httpx.AsyncClient(timeout=6.0) as client:
                resp = await client.post(self._rpc_url, json=payload)
                if resp.status_code == 200:
                    result = resp.json().get("result") or []
                    if isinstance(result, list) and result:
                        oldest_block_time = result[-1].get("blockTime")
                        if oldest_block_time:
                            age_seconds = time.time() - oldest_block_time
                            return max(1.0, age_seconds / 60.0)
        except Exception as exc:
            logger.debug(f"_estimate_token_age_minutes error: {exc}")
        return 1.0

    def start_tracking(self, token_address: str) -> None:
        """Register *token_address* for continuous background polling."""
        self._tracking.add(token_address)

    def stop_tracking(self, token_address: str) -> None:
        """Remove *token_address* from background polling."""
        self._tracking.discard(token_address)

    async def run_background_loop(self, interval_s: float = 15.0) -> None:
        """
        Continuously poll all tracked tokens every *interval_s* seconds.
        This coroutine runs forever; launch it as an asyncio background task.
        """
        logger.info(
            f"HolderVelocityTracker background loop started (interval={interval_s}s)."
        )
        while True:
            for token_address in list(self._tracking):
                try:
                    count, _ = await self._fetch_holder_count(token_address)
                    if count > 0:
                        self._add_snapshot(token_address, count)
                        peak = self._peaks.get(token_address, count)
                        if count < peak * (1.0 - _RUG_DROP_THRESHOLD) and peak > 10:
                            logger.warning(
                                f"⚠️  Rug warning: {token_address[:12]}... "
                                f"holders dropped from {peak} → {count}"
                            )
                except Exception as exc:
                    logger.error(
                        f"Background poll error for {token_address[:12]}...: {exc}"
                    )
            await asyncio.sleep(interval_s)
