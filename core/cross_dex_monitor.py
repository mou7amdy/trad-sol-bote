# core/cross_dex_monitor.py
"""
Phase 2.3 — Cross-DEX Price Monitor

Queries the same token's price from two independent data sources and
compares them.  A large price gap between sources is a strong signal of
price manipulation or thin liquidity.

Data sources (all called concurrently)
---------------------------------------
1. DexScreener  — free, no key required. Returns all pool prices for a mint.
2. Jupiter      — free aggregator price (best on-chain quote).

Price gap thresholds
--------------------
< 2%   → HEALTHY     score 100
2–5%   → ACCEPTABLE  score 75
5–10%  → SUSPICIOUS  score 40  (triggers manipulation warning)
> 10%  → MANIPULATED score 10  (hard gate: blocks signal)

Only one source
---------------
If fewer than 2 sources return a price, a neutral score of 50 is returned and
no gate is triggered (can't compare with a single data point).
"""

import asyncio
from dataclasses import dataclass, field
from typing import Optional

import httpx
from loguru import logger

# ---------------------------------------------------------------------------
# Thresholds
# ---------------------------------------------------------------------------
GAP_HEALTHY:      float = 2.0    # %
GAP_ACCEPTABLE:   float = 5.0    # %
GAP_SUSPICIOUS:   float = 10.0   # %

SCORE_HEALTHY:      float = 100.0
SCORE_ACCEPTABLE:   float = 75.0
SCORE_SUSPICIOUS:   float = 40.0
SCORE_MANIPULATED:  float = 10.0
SCORE_SINGLE_SOURCE: float = 50.0  # neutral — can't compare


# ---------------------------------------------------------------------------
# Data classes
# ---------------------------------------------------------------------------

@dataclass
class CrossDexResult:
    token_address:    str
    prices:           dict  = field(default_factory=dict)  # source → price
    source_count:     int   = 0    # number of sources that returned a price
    max_price:        float = 0.0
    min_price:        float = 0.0
    price_gap_pct:    float = 0.0  # (max - min) / min × 100
    gap_label:        str   = "UNKNOWN"
    is_manipulated:   bool  = False
    cross_dex_score:  float = 50.0


# ---------------------------------------------------------------------------
# Monitor
# ---------------------------------------------------------------------------

class CrossDexMonitor:
    """
    Compares token prices across multiple DEX data sources.

    All public methods are exception-safe — they always return a valid
    ``CrossDexResult`` even when every API call fails.
    """

    # ------------------------------------------------------------------
    # Price fetchers
    # ------------------------------------------------------------------

    async def _fetch_dexscreener(self, token_address: str) -> Optional[float]:
        """
        Fetch the highest-liquidity pool price from DexScreener.
        Returns price in USD or None on failure.
        """
        url = f"https://api.dexscreener.com/latest/dex/tokens/{token_address}"
        try:
            async with httpx.AsyncClient(timeout=6.0) as client:
                resp = await client.get(url)
                if resp.status_code != 200:
                    return None
                data  = resp.json()
                pairs = [
                    p for p in (data.get("pairs") or [])
                    if p.get("chainId") == "solana"
                    and float((p.get("liquidity") or {}).get("usd", 0) or 0) > 0
                ]
                if not pairs:
                    return None
                # Pick the pair with the highest liquidity
                best  = max(
                    pairs,
                    key=lambda p: float((p.get("liquidity") or {}).get("usd", 0) or 0),
                )
                price = float(best.get("priceUsd") or 0)
                if price > 0:
                    logger.debug(
                        f"DexScreener price for {token_address[:12]}...: ${price:.8f} "
                        f"(DEX: {best.get('dexId')})"
                    )
                    return price
        except Exception as exc:
            logger.error(f"_fetch_dexscreener error: {exc}")
        return None

    async def _fetch_jupiter(self, token_address: str) -> Optional[float]:
        """
        Fetch the aggregated on-chain best price from Jupiter Price API v6.
        Returns price in USD or None on failure.
        """
        url = f"https://price.jup.ag/v6/price?ids={token_address}"
        try:
            async with httpx.AsyncClient(timeout=6.0) as client:
                resp = await client.get(url)
                if resp.status_code != 200:
                    return None
                data  = resp.json()
                item  = (data.get("data") or {}).get(token_address)
                if not item:
                    return None
                price = float(item.get("price") or 0)
                if price > 0:
                    logger.debug(
                        f"Jupiter price for {token_address[:12]}...: ${price:.8f}"
                    )
                    return price
        except Exception as exc:
            logger.error(f"_fetch_jupiter error: {exc}")
        return None

    # ------------------------------------------------------------------
    # Gap analysis
    # ------------------------------------------------------------------

    @staticmethod
    def _gap_label(gap_pct: float) -> str:
        if gap_pct < GAP_HEALTHY:
            return "HEALTHY"
        if gap_pct < GAP_ACCEPTABLE:
            return "ACCEPTABLE"
        if gap_pct < GAP_SUSPICIOUS:
            return "SUSPICIOUS"
        return "MANIPULATED"

    @staticmethod
    def _gap_score(gap_pct: float) -> float:
        if gap_pct < GAP_HEALTHY:
            return SCORE_HEALTHY
        if gap_pct < GAP_ACCEPTABLE:
            # Linear interpolation: 2% → 100, 5% → 75
            frac = (gap_pct - GAP_HEALTHY) / (GAP_ACCEPTABLE - GAP_HEALTHY)
            return round(SCORE_HEALTHY - frac * (SCORE_HEALTHY - SCORE_ACCEPTABLE), 2)
        if gap_pct < GAP_SUSPICIOUS:
            # 5% → 75, 10% → 40
            frac = (gap_pct - GAP_ACCEPTABLE) / (GAP_SUSPICIOUS - GAP_ACCEPTABLE)
            return round(SCORE_ACCEPTABLE - frac * (SCORE_ACCEPTABLE - SCORE_SUSPICIOUS), 2)
        # > 10% → manipulated
        return SCORE_MANIPULATED

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    async def analyze(self, token_address: str) -> CrossDexResult:
        """
        Fetch prices from all sources concurrently and compute the gap.

        Always returns a valid ``CrossDexResult`` — never raises.
        """
        _safe = CrossDexResult(token_address=token_address)
        try:
            # Both fetches run in parallel
            dex_price, jup_price = await asyncio.gather(
                self._fetch_dexscreener(token_address),
                self._fetch_jupiter(token_address),
                return_exceptions=True,
            )

            # Filter out exceptions
            if isinstance(dex_price, Exception):
                dex_price = None
            if isinstance(jup_price, Exception):
                jup_price = None

            # Collect valid prices
            raw_prices: dict[str, float] = {}
            if dex_price and dex_price > 0:
                raw_prices["dexscreener"] = dex_price
            if jup_price and jup_price > 0:
                raw_prices["jupiter"] = jup_price

            source_count = len(raw_prices)
            if source_count < 2:
                logger.debug(
                    f"CrossDexMonitor: only {source_count} source(s) for "
                    f"{token_address[:12]}... — cannot compare."
                )
                return CrossDexResult(
                    token_address=token_address,
                    prices=raw_prices,
                    source_count=source_count,
                    cross_dex_score=SCORE_SINGLE_SOURCE,
                    gap_label="UNKNOWN",
                )

            prices_list = list(raw_prices.values())
            max_p       = max(prices_list)
            min_p       = min(prices_list)
            gap_pct     = ((max_p - min_p) / min_p * 100.0) if min_p > 0 else 0.0
            label       = self._gap_label(gap_pct)
            score       = self._gap_score(gap_pct)
            is_manip    = gap_pct >= GAP_SUSPICIOUS

            logger.info(
                f"CrossDEX {token_address[:12]}...: "
                f"sources={list(raw_prices.keys())}, "
                f"prices={[f'{p:.8f}' for p in prices_list]}, "
                f"gap={gap_pct:.2f}%, label={label}, score={score:.1f}"
            )

            return CrossDexResult(
                token_address=token_address,
                prices=raw_prices,
                source_count=source_count,
                max_price=max_p,
                min_price=min_p,
                price_gap_pct=round(gap_pct, 4),
                gap_label=label,
                is_manipulated=is_manip,
                cross_dex_score=score,
            )

        except Exception as exc:
            logger.error(f"CrossDexMonitor.analyze error: {exc}")
            return _safe
